from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from phycode.execution import ExecutionJournal, ExecutionJournalError
from phycode.models import ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from phycode.redaction import redact_text
from phycode.tools.base import ToolRegistry
from phycode.visibility import PathVisibilityPolicy, VisibilityViolation

_MINIMAL_ENVIRONMENT_NAMES = (
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
)


def _to_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _result(
    call: ToolCall,
    status: str,
    *,
    stdout: bytes | str | None = "",
    stderr: bytes | str | None = "",
) -> ToolResult:
    return ToolResult(
        tool_call_id=call.id,
        status=status,
        stdout=redact_text(_to_text(stdout)),
        stderr=redact_text(_to_text(stderr)),
    )


def _invalid(call: ToolCall, message: str) -> ToolResult:
    return _result(call, "invalid_tool_args", stderr=message)


def register_process_tools(
    registry: ToolRegistry,
    workspace_root: Path,
    allowed_executables: frozenset[Path],
    journal: ExecutionJournal | None = None,
) -> None:
    root = workspace_root.expanduser().resolve()
    if journal is not None and journal.workspace_root != root:
        raise ValueError("execution journal workspace does not match process workspace")
    visibility = PathVisibilityPolicy(root)
    executable_allowlist = frozenset(path.expanduser().resolve() for path in allowed_executables)

    def process_run(call: ToolCall) -> ToolResult:
        unknown_args = set(call.args) - {"argv", "cwd", "timeout"}
        if unknown_args:
            return _invalid(call, f"unknown process.run argument(s): {', '.join(sorted(unknown_args))}")

        argv = call.args.get("argv")
        if not isinstance(argv, list) or not argv:
            return _invalid(call, "argv must be a non-empty list of strings")
        if any(not isinstance(item, str) or not item or "\x00" in item for item in argv):
            return _invalid(call, "argv entries must be non-empty strings without NUL characters")

        cwd = call.args.get("cwd", ".")
        if not isinstance(cwd, str) or not cwd or "\x00" in cwd:
            return _invalid(call, "cwd must be a non-empty string without NUL characters")
        try:
            resolved_cwd = visibility.resolve(cwd)
        except (OSError, RuntimeError, VisibilityViolation) as exc:
            return _invalid(call, f"cwd is not visible: {exc}")

        timeout = call.args.get("timeout", 30)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 300:
            return _invalid(call, "timeout must be an integer from 1 through 300")

        requested_executable = Path(argv[0]).expanduser()
        if not requested_executable.is_absolute():
            return _invalid(call, "executable path must be absolute")
        try:
            executable = requested_executable.resolve()
        except (OSError, RuntimeError) as exc:
            return _invalid(call, f"executable path cannot be resolved: {exc}")
        if executable not in executable_allowlist:
            return _invalid(call, f"executable is not allowed: {executable}")
        argv = [str(executable), *argv[1:]]
        minimal_environment = {
            name: value
            for name in _MINIMAL_ENVIRONMENT_NAMES
            if (value := os.environ.get(name)) is not None
        }

        started_at = datetime.now(timezone.utc)
        artifacts_before = ()
        script_path: str | None = None
        script_sha256: str | None = None
        if journal is not None:
            try:
                journal.validate_cwd(resolved_cwd)
                artifacts_before = journal.snapshot_artifacts()
                script_path, script_sha256 = journal.snapshot_script(tuple(argv), resolved_cwd)
            except (OSError, RuntimeError, ExecutionJournalError):
                return _invalid(call, "process execution could not be journaled safely")

        def finish_journal(status: str, exit_code: int | None) -> ToolResult | None:
            if journal is None:
                return None
            try:
                journal.record_process(
                    call_id=call.id,
                    argv=tuple(argv),
                    cwd=resolved_cwd,
                    status=status,
                    exit_code=exit_code,
                    started_at=started_at,
                    artifacts_before=artifacts_before,
                    script_path=script_path,
                    script_sha256=script_sha256,
                )
            except (OSError, RuntimeError, ExecutionJournalError):
                return _result(call, "tool_error", stderr="execution journal update failed")
            return None

        try:
            completed = subprocess.run(
                argv,
                cwd=resolved_cwd,
                shell=False,
                text=True,
                capture_output=True,
                timeout=timeout,
                env=minimal_environment,
            )
        except subprocess.TimeoutExpired as exc:
            journal_error = finish_journal("timeout", None)
            if journal_error is not None:
                return journal_error
            return _result(
                call,
                "timeout",
                stdout=_to_text(exc.stdout),
                stderr=_to_text(exc.stderr) or f"Process timed out after {timeout} seconds",
            )
        except OSError as exc:
            journal_error = finish_journal("tool_error", None)
            if journal_error is not None:
                return journal_error
            return _result(call, "tool_error", stderr=str(exc))

        status = "ok" if completed.returncode == 0 else "command_failed"
        journal_error = finish_journal(status, completed.returncode)
        if journal_error is not None:
            return journal_error
        return _result(call, status, stdout=completed.stdout, stderr=completed.stderr)

    registry.register(
        ToolSpec(
            name="process.run",
            description="Run an allowlisted executable with structured arguments and no shell",
            input_schema={
                "type": "object",
                "properties": {
                    "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "cwd": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 300},
                },
                "required": ["argv"],
                "additionalProperties": False,
            },
            risk_level=ToolRiskLevel.RISKY,
        ),
        process_run,
    )
