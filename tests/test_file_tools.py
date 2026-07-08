from pathlib import Path

from phycode.models import PolicyAction, ToolCall
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.file_tools import register_file_tools


def test_file_read_reads_workspace_file(tmp_path: Path):
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    registry = ToolRegistry()
    register_file_tools(registry)
    result = ToolRuntime(registry).run(
        ToolCall(tool_name="file.read", args={"path": "README.md"}),
        PolicyContext(tmp_path, [], True),
    )
    assert result.policy.decision == PolicyAction.ALLOW
    assert result.tool_result.stdout == "hello"


def test_file_read_resolves_paths_against_policy_workspace(tmp_path: Path):
    (tmp_path / "nested.txt").write_text("workspace copy", encoding="utf-8")
    registry = ToolRegistry()
    register_file_tools(registry)
    result = ToolRuntime(registry).run(
        ToolCall(tool_name="file.read", args={"path": "nested.txt"}),
        PolicyContext(tmp_path, [], True),
    )
    assert result.tool_result.stdout == "workspace copy"


def test_file_edit_requires_approval_then_writes_diff(tmp_path: Path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    registry = ToolRegistry()
    register_file_tools(registry)
    call = ToolCall(tool_name="file.edit", args={"path": "app.py", "old": "x = 1", "new": "x = 2"})
    result = ToolRuntime(registry).run(call, PolicyContext(tmp_path, [], True), approved=True)
    assert result.tool_result.status == "ok"
    assert "x = 2" in (tmp_path / "app.py").read_text(encoding="utf-8")
    assert "-x = 1" in result.tool_result.stdout
    assert "+x = 2" in result.tool_result.stdout
