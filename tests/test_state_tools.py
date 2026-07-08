from pathlib import Path

from phycode.models import ToolCall
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.state_tools import register_state_tools


def test_workspace_status_reports_root(tmp_path: Path):
    registry = ToolRegistry()
    register_state_tools(registry, workspace_root=tmp_path)
    result = ToolRuntime(registry).run(ToolCall(tool_name="workspace.status", args={}), PolicyContext(tmp_path, [], True))
    assert str(tmp_path) in result.tool_result.stdout
