from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from phycode.models import PolicyAction, PolicyDecision, ToolCall, ToolResult, ToolSpec
from phycode.policy import PolicyContext, PolicyEngine, WorkspaceViolation, resolve_workspace_path

ToolExecutor = Callable[[ToolCall], ToolResult]


@dataclass(frozen=True)
class ToolRuntimeResult:
    policy: PolicyDecision
    tool_result: ToolResult


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._executors: dict[str, ToolExecutor] = {}

    def register(self, spec: ToolSpec, executor: ToolExecutor) -> None:
        self._specs[spec.name] = spec
        self._executors[spec.name] = executor

    def list_specs(self) -> list[ToolSpec]:
        return [self._specs[name] for name in sorted(self._specs)]

    def executor_for(self, name: str) -> ToolExecutor | None:
        return self._executors.get(name)


class ToolRuntime:
    def __init__(self, registry: ToolRegistry, policy: PolicyEngine | None = None) -> None:
        self.registry = registry
        self.policy = policy if policy is not None else PolicyEngine()

    def run(self, call: ToolCall, context: PolicyContext, approved: bool = False) -> ToolRuntimeResult:
        decision = self.policy.decide(call, context)
        if decision.decision == PolicyAction.DENY:
            return ToolRuntimeResult(
                decision,
                ToolResult(tool_call_id=call.id, status="policy_blocked", stderr=decision.reason),
            )
        if decision.decision == PolicyAction.ASK and not approved:
            return ToolRuntimeResult(
                decision,
                ToolResult(tool_call_id=call.id, status="policy_requires_approval", stderr=decision.reason),
            )

        executor = self.registry.executor_for(call.tool_name)
        if executor is None:
            return ToolRuntimeResult(
                decision,
                ToolResult(tool_call_id=call.id, status="tool_error", stderr=f"Tool not registered: {call.tool_name}"),
            )

        normalized_call = self._normalize_call_paths(call, context)
        return ToolRuntimeResult(decision, executor(normalized_call))

    def _normalize_call_paths(self, call: ToolCall, context: PolicyContext) -> ToolCall:
        if "path" not in call.args:
            return call
        try:
            resolved = resolve_workspace_path(str(call.args["path"]), context)
        except WorkspaceViolation:
            return call
        args = dict(call.args)
        args["path"] = str(resolved)
        return call.model_copy(update={"args": args})
