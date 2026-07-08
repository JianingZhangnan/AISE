from pathlib import Path

from phycode.feedback import classify_feedback
from phycode.models import FeedbackKind, ToolCall
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.shell_tools import register_shell_tools


def test_shell_success_maps_to_success_feedback(tmp_path: Path):
    registry = ToolRegistry()
    register_shell_tools(registry, workspace_root=tmp_path, test_command="python --version")
    call = ToolCall(tool_name="shell.run", args={"command": "python --version"})
    runtime_result = ToolRuntime(registry).run(call, PolicyContext(tmp_path, [], True), approved=True)
    feedback = classify_feedback(runtime_result.tool_result)
    assert feedback[0].kind == FeedbackKind.SUCCESS


def test_tool_error_maps_to_tool_error():
    from phycode.models import ToolResult

    feedback = classify_feedback(ToolResult(tool_call_id="x", status="tool_error", stderr="old text not found"))
    assert feedback[0].kind == FeedbackKind.TOOL_ERROR
