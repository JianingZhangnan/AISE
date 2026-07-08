from __future__ import annotations

from pathlib import Path

from phycode.models import ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from phycode.tools.base import ToolRegistry


def register_state_tools(registry: ToolRegistry, workspace_root: Path) -> None:
    def workspace_status(call: ToolCall) -> ToolResult:
        return ToolResult(tool_call_id=call.id, status="ok", stdout=f"workspace_root={workspace_root}")

    registry.register(
        ToolSpec(
            name="workspace.status",
            description="Show workspace status",
            input_schema={"type": "object"},
            risk_level=ToolRiskLevel.SAFE,
        ),
        workspace_status,
    )
