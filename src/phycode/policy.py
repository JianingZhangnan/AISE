from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from phycode.models import PolicyAction, PolicyDecision, ToolCall


class WorkspaceViolation(ValueError):
    pass


@dataclass(frozen=True)
class PolicyContext:
    workspace_root: Path
    allowlist: list[Path]
    interactive: bool


CREDENTIAL_FILENAMES = {".env", ".env.local", "id_rsa", "id_ed25519"}
SHELL_TOOLS = {"shell.run", "test.run"}
SAFE_TOOLS = {
    "file.read",
    "file.list",
    "search.grep",
    "search.glob",
    "memory.read",
    "config.read",
    "workspace.status",
    "keys.status",
}
RISKY_TOOLS = {"file.write", "file.edit", "memory.write", "config.write", "shell.run", "test.run"}
DANGEROUS_SHELL_PATTERNS = [
    re.compile(r"\brm\s+-rf\s+/", re.IGNORECASE),
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
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+-R\s+777\s+/", re.IGNORECASE),
    re.compile(r":\(\)\s*\{.*\|.*\}\s*;\s*:"),
]
# Credential-like references that must never be read back through a shell command.
CREDENTIAL_SHELL_PATTERNS = [
    re.compile(r"(?<![\w.])\.env(?:\.\w+)?(?![\w])", re.IGNORECASE),
    re.compile(r"\bid_rsa\b", re.IGNORECASE),
    re.compile(r"\bid_ed25519\b", re.IGNORECASE),
    re.compile(r"[\w./\\-]*\.pem\b", re.IGNORECASE),
    re.compile(r"[\w./\\-]*\.key\b", re.IGNORECASE),
]


def _allowed_roots(context: PolicyContext) -> list[Path]:
    return [context.workspace_root.resolve(), *[path.resolve() for path in context.allowlist]]


def resolve_workspace_path(path: str, context: PolicyContext) -> Path:
    raw_path = Path(path).expanduser()
    candidate = raw_path.resolve() if raw_path.is_absolute() else (context.workspace_root / raw_path).resolve()
    for root in _allowed_roots(context):
        if candidate == root or root in candidate.parents:
            return candidate
    raise WorkspaceViolation(f"path escapes workspace: {path}")


def _is_credential_path(path: str) -> bool:
    name = Path(path).name
    return name in CREDENTIAL_FILENAMES or name.endswith(".pem") or name.endswith(".key")


class PolicyEngine:
    def decide(self, call: ToolCall, context: PolicyContext) -> PolicyDecision:
        if "path" in call.args:
            path = str(call.args["path"])
            try:
                resolve_workspace_path(path, context)
            except WorkspaceViolation:
                return PolicyDecision(
                    tool_call_id=call.id,
                    decision=PolicyAction.DENY,
                    rule_id="workspace.path_escape",
                    reason="Path is outside the workspace allowlist",
                )
            if call.tool_name.startswith("file.") and _is_credential_path(path):
                return PolicyDecision(
                    tool_call_id=call.id,
                    decision=PolicyAction.DENY,
                    rule_id="credential.read_blocked",
                    reason="Credential-like files cannot be read by model-callable tools",
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
