from __future__ import annotations

import subprocess
from pathlib import Path

from phycode.models import ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from phycode.tools.base import ToolRegistry


def _to_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _run_command(command: str, cwd: Path, timeout: int = 30) -> ToolResult:
    try:
        completed = subprocess.run(command, cwd=cwd, shell=True, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return ToolResult(
            tool_call_id="pending",
            status="timeout",
            stdout=_to_text(exc.stdout),
            stderr=_to_text(exc.stderr) or f"Command timed out after {timeout} seconds",
        )
    status = "ok" if completed.returncode == 0 else "command_failed"
    return ToolResult(tool_call_id="pending", status=status, stdout=completed.stdout, stderr=completed.stderr)


def register_shell_tools(registry: ToolRegistry, workspace_root: Path, test_command: str) -> None:
    def shell_run(call: ToolCall) -> ToolResult:
        timeout = int(call.args.get("timeout", 30))
        result = _run_command(str(call.args["command"]), workspace_root, timeout)
        return result.model_copy(update={"tool_call_id": call.id})

    def test_run(call: ToolCall) -> ToolResult:
        timeout = int(call.args.get("timeout", 60))
        result = _run_command(str(call.args.get("command", test_command)), workspace_root, timeout)
        status = "ok" if result.status == "ok" else "test_failed"
        return result.model_copy(update={"tool_call_id": call.id, "status": status})

    registry.register(
        ToolSpec(
            name="shell.run",
            description="Run a bounded shell command",
            input_schema={
                "type": "object",
                "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}},
                "required": ["command"],
            },
            risk_level=ToolRiskLevel.RISKY,
        ),
        shell_run,
    )
    registry.register(
        ToolSpec(
            name="test.run",
            description="Run configured tests",
            input_schema={
                "type": "object",
                "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}},
            },
            risk_level=ToolRiskLevel.RISKY,
        ),
        test_run,
    )
