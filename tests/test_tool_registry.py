from pathlib import Path

import pytest

from phycode.composition import registry_subset
from phycode.models import (
    PolicyAction,
    PolicyDecision,
    ToolCall,
    ToolResult,
    ToolRiskLevel,
    ToolSpec,
)
from phycode.policy import PolicyContext, PolicyEngine
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


def test_registry_subset_preserves_call_normalizer() -> None:
    registry = ToolRegistry()

    def normalize(call: ToolCall) -> ToolCall:
        return call.model_copy(update={"args": {"value": "normalized"}})

    registry.register(
        ToolSpec(
            name="workspace.status",
            description="status",
            input_schema={},
            risk_level=ToolRiskLevel.SAFE,
        ),
        lambda call: ToolResult(tool_call_id=call.id, status="ok"),
        normalizer=normalize,
    )

    selected = registry_subset(registry, frozenset({"workspace.status"}))

    assert selected.normalizer_for("workspace.status") is normalize


def test_runtime_normalizes_once_before_policy_approval_and_execution(tmp_path: Path) -> None:
    registry = ToolRegistry()
    observed: list[tuple[str, ToolCall]] = []

    def normalize(call: ToolCall) -> ToolCall:
        observed.append(("normalizer", call))
        args = dict(call.args)
        args["argv"] = ["C:/trusted/python.exe", *call.args["argv"][1:]]
        return call.model_copy(update={"args": args})

    def execute(call: ToolCall) -> ToolResult:
        observed.append(("executor", call))
        return ToolResult(tool_call_id=call.id, status="ok")

    class RecordingPolicy(PolicyEngine):
        def decide(self, call: ToolCall, context: PolicyContext) -> PolicyDecision:
            del context
            observed.append(("policy", call))
            return PolicyDecision(
                tool_call_id=call.id,
                decision=PolicyAction.ASK,
                rule_id="test.approval",
                reason="approval required",
            )

    registry.register(
        ToolSpec(
            name="process.run",
            description="run",
            input_schema={"required": ["argv"]},
            risk_level=ToolRiskLevel.RISKY,
        ),
        execute,
        normalizer=normalize,
    )
    original = ToolCall(
        id="call_stable",
        provider_call_id="provider_stable",
        tool_name="process.run",
        args={"argv": ["python", "reproduce.py"], "cwd": ".", "timeout": 15},
    )

    def approve(call: ToolCall, decision: PolicyDecision) -> bool:
        assert decision.tool_call_id == call.id
        observed.append(("approval", call))
        return True

    result = ToolRuntime(registry, policy=RecordingPolicy()).run(
        original,
        PolicyContext(tmp_path, [], interactive=False),
        approval_handler=approve,
    )

    assert result.tool_result.status == "ok"
    assert [stage for stage, _call in observed] == [
        "normalizer",
        "policy",
        "approval",
        "executor",
    ]
    canonical_calls = [call for stage, call in observed if stage != "normalizer"]
    assert all(call == canonical_calls[0] for call in canonical_calls)
    assert canonical_calls[0].id == original.id
    assert canonical_calls[0].provider_call_id == original.provider_call_id
    assert canonical_calls[0].args == {
        "argv": ["C:/trusted/python.exe", "reproduce.py"],
        "cwd": ".",
        "timeout": 15,
    }
    assert original.args["argv"][0] == "python"


def test_runtime_normalizer_exception_fails_closed_before_policy_or_execution(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    observed: list[str] = []

    def fail_normalization(call: ToolCall) -> ToolCall:
        del call
        observed.append("normalizer")
        raise RuntimeError("private normalizer detail")

    def execute(call: ToolCall) -> ToolResult:
        observed.append("executor")
        return ToolResult(tool_call_id=call.id, status="ok")

    class RecordingPolicy(PolicyEngine):
        def decide(self, call: ToolCall, context: PolicyContext) -> PolicyDecision:
            del call, context
            observed.append("policy")
            raise AssertionError("policy must not receive an unnormalized call")

    registry.register(
        ToolSpec(
            name="process.run",
            description="run",
            input_schema={"required": ["argv"]},
            risk_level=ToolRiskLevel.RISKY,
        ),
        execute,
        normalizer=fail_normalization,
    )

    result = ToolRuntime(registry, policy=RecordingPolicy()).run(
        ToolCall(tool_name="process.run", args={"argv": ["python", "reproduce.py"]}),
        PolicyContext(tmp_path, [], interactive=False),
        approval_handler=lambda call, decision: observed.append("approval") or True,
    )

    assert observed == ["normalizer"]
    assert result.policy.decision == PolicyAction.DENY
    assert result.policy.rule_id == "runtime.call_normalization_failed"
    assert result.tool_result.status == "invalid_tool_args"
    assert result.tool_result.stderr == "Tool arguments could not be normalized safely"


@pytest.mark.parametrize(
    "invalid_update",
    [
        {"id": "replaced_call"},
        {"tool_name": "file.write"},
        {"provider_call_id": "replaced_provider_call"},
    ],
)
def test_runtime_rejects_normalizer_that_changes_call_identity(
    tmp_path: Path,
    invalid_update: dict[str, str],
) -> None:
    registry = ToolRegistry()
    observed: list[str] = []

    def corrupt_identity(call: ToolCall) -> ToolCall:
        return call.model_copy(update=invalid_update)

    registry.register(
        ToolSpec(
            name="process.run",
            description="run",
            input_schema={"required": ["argv"]},
            risk_level=ToolRiskLevel.RISKY,
        ),
        lambda call: observed.append("executor")
        or ToolResult(tool_call_id=call.id, status="ok"),
        normalizer=corrupt_identity,
    )
    call = ToolCall(
        id="stable_call",
        provider_call_id="stable_provider_call",
        tool_name="process.run",
        args={"argv": ["python", "reproduce.py"]},
    )

    class RecordingPolicy(PolicyEngine):
        def decide(self, received: ToolCall, context: PolicyContext) -> PolicyDecision:
            del received, context
            observed.append("policy")
            raise AssertionError("policy must not receive a call with corrupted identity")

    result = ToolRuntime(registry, policy=RecordingPolicy()).run(
        call,
        PolicyContext(tmp_path, [], interactive=False),
    )

    assert observed == []
    assert result.policy.rule_id == "runtime.call_normalization_failed"
    assert result.tool_result.tool_call_id == call.id
    assert result.tool_result.status == "invalid_tool_args"


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


def test_default_registry_declares_state_mutation_for_batch_serialization(tmp_path: Path) -> None:
    from phycode.composition import build_default_registry
    from phycode.context import MemoryStore

    registry = build_default_registry(
        workspace_root=tmp_path,
        test_command="uv run pytest",
        memory_store=MemoryStore.ephemeral(),
    )

    mutating = {
        "file.write",
        "file.edit",
        "memory.write",
        "config.write",
        "shell.run",
        "test.run",
        "process.run",
    }
    for name in mutating:
        spec = registry.spec_for(name)
        assert spec is not None
        assert spec.mutates_state, name

    for name in {"file.read", "file.list", "workspace.status", "memory.read", "config.read"}:
        spec = registry.spec_for(name)
        assert spec is not None
        assert not spec.mutates_state, name
