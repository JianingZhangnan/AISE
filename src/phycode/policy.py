from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from phycode.models import AgentProfile, PolicyAction, PolicyDecision, ToolCall
from phycode.profiles import ProfileSpec, profile_spec
from phycode.visibility import PathVisibilityPolicy, VisibilityViolation, is_sensitive_path


class WorkspaceViolation(ValueError):
    pass


@dataclass(frozen=True)
class PolicyContext:
    workspace_root: Path
    allowlist: list[Path]
    interactive: bool
    profile_spec: ProfileSpec = field(default_factory=lambda: profile_spec(AgentProfile.CODING))

    @property
    def visibility(self) -> PathVisibilityPolicy:
        return PathVisibilityPolicy(
            self.workspace_root,
            self.allowlist,
            self.profile_spec.hidden_path_components,
        )


SHELL_TOOLS = {"shell.run", "test.run"}
SAFE_TOOLS = {
    "file.read",
    "file.inspect",
    "file.list",
    "search.grep",
    "search.glob",
    "memory.read",
    "config.read",
    "workspace.status",
    "keys.status",
    "web.search",
    "web.fetch",
    "calculator.calculate",
}
RISKY_TOOLS = {
    "file.write",
    "file.edit",
    "memory.write",
    "config.write",
    "shell.run",
    "test.run",
    "image.inspect",
    "process.run",
}
DANGEROUS_SHELL_PATTERNS = [
    # recursive/forced rm of a root-ish target (-rf, -fr, -r, -f ... / ~ * or bare .)
    re.compile(r"\brm\s+-[a-z]*[rf][a-z]*\s+(?:/|~|\*|\.(?:\s|$))", re.IGNORECASE),
    re.compile(r"\bdel\s+/s\b", re.IGNORECASE),
    re.compile(r"\b(?:rmdir|rd)\s+/s\b", re.IGNORECASE),
    re.compile(r"\bformat\s+[A-Z]:", re.IGNORECASE),
    re.compile(r"\bcurl\b.*\|\s*sh\b", re.IGNORECASE),
    re.compile(r"\bwget\b.*\|\s*sh\b", re.IGNORECASE),
    re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE),
    re.compile(r"\bgit\s+push\b.*\s(?:--force|-f)\b", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bdd\b.*\bof=/dev/", re.IGNORECASE),
    re.compile(r">\s*/dev/sd", re.IGNORECASE),
    # actual OS shutdown invocation, not the bare word appearing in other text
    re.compile(r"\bshutdown\s+(?:/[a-z]|-[a-z]|now)\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+-R\s+777\s+/", re.IGNORECASE),
    re.compile(r":\(\)\s*\{.*\|.*\}\s*;\s*:"),
]
_CREDENTIAL_READ_COMMANDS = (
    r"(?:cat|tac|type|more|less|head|tail|nl|strings|xxd|od|base64|Get-Content|gc|cp|copy|scp|rsync|curl|wget|openssl|sftp)"
)
# Credential-like references that must never be read back through a shell command.
# Specific credential filenames are always blocked; the broad *.pem/*.key match only
# fires alongside a file-reading command so it does not deny e.g. `jq '.key'`.
CREDENTIAL_SHELL_PATTERNS = [
    re.compile(r"(?<![\w.])\.env(?:\.\w+)?(?![\w])", re.IGNORECASE),
    re.compile(r"\bid_rsa\b", re.IGNORECASE),
    re.compile(r"\bid_ed25519\b", re.IGNORECASE),
    re.compile(r"\.ssh[\\/]", re.IGNORECASE),
    re.compile(r"\.aws[\\/]credentials\b", re.IGNORECASE),
    re.compile(r"(?<![\w.])\.netrc\b", re.IGNORECASE),
    re.compile(rf"\b{_CREDENTIAL_READ_COMMANDS}\b[^\n|]*?\.(?:pem|key)\b", re.IGNORECASE),
]


def resolve_workspace_path(path: str, context: PolicyContext) -> Path:
    try:
        return context.visibility.resolve(path)
    except VisibilityViolation as exc:
        raise WorkspaceViolation(str(exc)) from exc


class PolicyEngine:
    def decide(self, call: ToolCall, context: PolicyContext) -> PolicyDecision:
        if "path" in call.args:
            path = str(call.args["path"])
            try:
                resolved_path = context.visibility.resolve(path)
            except VisibilityViolation as exc:
                hidden_path = exc.hidden
                return PolicyDecision(
                    tool_call_id=call.id,
                    decision=PolicyAction.DENY,
                    rule_id="prbench.hidden_path_blocked" if hidden_path else "workspace.path_escape",
                    reason=(
                        "Path is hidden from the active profile"
                        if hidden_path
                        else "Path is outside the workspace allowlist"
                    ),
                )
            if call.tool_name.startswith("file.") and (
                is_sensitive_path(path, context.profile_spec.hidden_path_components)
                or is_sensitive_path(str(resolved_path), context.profile_spec.hidden_path_components)
            ):
                return PolicyDecision(
                    tool_call_id=call.id,
                    decision=PolicyAction.DENY,
                    rule_id="credential.read_blocked",
                    reason="Credential-like files cannot be read by model-callable tools",
                )

        if call.tool_name == "process.run":
            cwd = call.args.get("cwd", ".")
            try:
                context.visibility.resolve(str(cwd))
            except (OSError, RuntimeError, VisibilityViolation) as exc:
                hidden_path = isinstance(exc, VisibilityViolation) and exc.hidden
                return PolicyDecision(
                    tool_call_id=call.id,
                    decision=PolicyAction.DENY,
                    rule_id="prbench.hidden_path_blocked" if hidden_path else "workspace.path_escape",
                    reason=(
                        "Path is hidden from the active profile"
                        if hidden_path
                        else "Path is outside the workspace allowlist"
                    ),
                )

        if call.tool_name in SHELL_TOOLS:
            command = str(call.args.get("command", ""))
            for pattern in DANGEROUS_SHELL_PATTERNS:
                if pattern.search(command):
                    return PolicyDecision(
                        tool_call_id=call.id,
                        decision=PolicyAction.DENY,
                        rule_id="shell.dangerous_command",
                        reason="Command matches a dangerous shell pattern",
                    )
            for pattern in CREDENTIAL_SHELL_PATTERNS:
                if pattern.search(command):
                    return PolicyDecision(
                        tool_call_id=call.id,
                        decision=PolicyAction.DENY,
                        rule_id="credential.shell_read_blocked",
                        reason="Shell command references a credential-like file",
                    )

        if call.tool_name in SAFE_TOOLS:
            return PolicyDecision(
                tool_call_id=call.id,
                decision=PolicyAction.ALLOW,
                rule_id="tool.safe_default",
                reason="Safe read/status tool",
            )

        if call.tool_name in RISKY_TOOLS:
            return PolicyDecision(
                tool_call_id=call.id,
                decision=PolicyAction.ASK,
                rule_id="tool.risky_default",
                reason="Risky tool requires approval",
                requires_user=True,
            )

        return PolicyDecision(
            tool_call_id=call.id,
            decision=PolicyAction.DENY,
            rule_id="tool.unknown",
            reason="Unknown tool",
        )
