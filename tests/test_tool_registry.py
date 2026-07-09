from pathlib import Path

import pytest

from phycode.models import PolicyAction, ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.file_tools import register_file_tools


def test_runtime_returns_policy_block_for_denied_call(tmp_path: Path):
    registry = ToolRegistry()
    runtime = ToolRuntime(registry=registry)
    call = ToolCall(tool_name="unknown.tool", args={})
    result = runtime.run(call, PolicyContext(tmp_path, [], interactive=False))
    assert result.policy.decision == PolicyAction.DENY
    assert result.tool_result.status == "policy_blocked"


def test_registry_lists_specs():
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="x.echo", description="Echo", input_schema={}, risk_level=ToolRiskLevel.SAFE),
        lambda call: ToolResult(tool_call_id=call.id, status="ok"),
    )
    assert [spec.name for spec in registry.list_specs()] == ["x.echo"]


def test_missing_required_arg_yields_invalid_tool_args(tmp_path: Path):
    registry = ToolRegistry()
    register_file_tools(registry)
    # file.read requires "path"; omit it and expect a structured error, not a crash.
    result = ToolRuntime(registry).run(
        ToolCall(tool_name="file.read", args={}),
        PolicyContext(tmp_path, [], True),
    )
    assert result.tool_result.status == "invalid_tool_args"
    assert "path" in result.tool_result.stderr


def test_executor_exception_is_captured_as_tool_error(tmp_path: Path):
    registry = ToolRegistry()

    def boom(call: ToolCall) -> ToolResult:
        raise RuntimeError("kaboom")

    # workspace.status is a policy-safe tool; swap in an executor that raises.
    registry.register(
        ToolSpec(name="workspace.status", description="boom", input_schema={}, risk_level=ToolRiskLevel.SAFE),
        boom,
    )
    result = ToolRuntime(registry).run(
        ToolCall(tool_name="workspace.status", args={}),
        PolicyContext(tmp_path, [], True),
    )
    assert result.tool_result.status == "tool_error"
    assert "kaboom" in result.tool_result.stderr


def test_file_specs_declare_required_fields():
    registry = ToolRegistry()
    register_file_tools(registry)
    spec = registry.spec_for("file.edit")
    assert spec is not None
    schema = spec.input_schema
    assert set(schema.get("required", [])) == {"path", "old", "new"}
