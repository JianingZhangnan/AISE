from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from phycode.models import PolicyAction, PolicyDecision, ToolCall, ToolResult, ToolSpec
from phycode.policy import PolicyContext, PolicyEngine, WorkspaceViolation, resolve_workspace_path

ToolExecutor = Callable[[ToolCall], ToolResult]
ToolCallNormalizer = Callable[[ToolCall], ToolCall]
ApprovalHandler = Callable[[ToolCall, PolicyDecision], bool]


@dataclass(frozen=True)
class ToolRuntimeResult:
    policy: PolicyDecision
    tool_result: ToolResult


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._executors: dict[str, ToolExecutor] = {}
        self._normalizers: dict[str, ToolCallNormalizer] = {}

    def register(
        self,
        spec: ToolSpec,
        executor: ToolExecutor,
        *,
        normalizer: ToolCallNormalizer | None = None,
    ) -> None:
        self._specs[spec.name] = spec
        self._executors[spec.name] = executor
        if normalizer is None:
            self._normalizers.pop(spec.name, None)
        else:
            self._normalizers[spec.name] = normalizer

    def list_specs(self) -> list[ToolSpec]:
        return [self._specs[name] for name in sorted(self._specs)]

    def executor_for(self, name: str) -> ToolExecutor | None:
        return self._executors.get(name)

    def normalizer_for(self, name: str) -> ToolCallNormalizer | None:
        return self._normalizers.get(name)

    def spec_for(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)


class ToolRuntime:
    def __init__(self, registry: ToolRegistry, policy: PolicyEngine | None = None) -> None:
        self.registry = registry
        self.policy = policy if policy is not None else PolicyEngine()

    def run(
        self,
        call: ToolCall,
        context: PolicyContext,
        approved: bool = False,
        approval_handler: ApprovalHandler | None = None,
    ) -> ToolRuntimeResult:
        try:
            normalized_call = self._normalize_call(call, context)
        except Exception:
            decision = PolicyDecision(
                tool_call_id=call.id,
                decision=PolicyAction.DENY,
                rule_id="runtime.call_normalization_failed",
                reason="Tool arguments could not be normalized safely",
            )
            return ToolRuntimeResult(
                decision,
                ToolResult(
                    tool_call_id=call.id,
                    status="invalid_tool_args",
                    stderr=decision.reason,
                ),
            )

        decision = self.policy.decide(normalized_call, context)
        if decision.decision == PolicyAction.DENY:
            return ToolRuntimeResult(
                decision,
                ToolResult(
                    tool_call_id=normalized_call.id,
                    status="policy_blocked",
                    stderr=decision.reason,
                ),
            )
        if decision.decision == PolicyAction.ASK:
            if not approved and approval_handler is not None:
                approved = bool(approval_handler(normalized_call, decision))
            if not approved:
                return ToolRuntimeResult(
                    decision,
                    ToolResult(
                        tool_call_id=normalized_call.id,
                        status="policy_requires_approval",
                        stderr=decision.reason,
                    ),
                )

        executor = self.registry.executor_for(normalized_call.tool_name)
        if executor is None:
            return ToolRuntimeResult(
                decision,
                ToolResult(
                    tool_call_id=normalized_call.id,
                    status="tool_error",
                    stderr=f"Tool not registered: {normalized_call.tool_name}",
                ),
            )

        missing = self._missing_required_args(normalized_call)
        if missing:
            return ToolRuntimeResult(
                decision,
                ToolResult(
                    tool_call_id=normalized_call.id,
                    status="invalid_tool_args",
                    stderr=f"Missing required argument(s): {', '.join(missing)}",
                ),
            )

        try:
            tool_result = executor(normalized_call)
        except Exception as exc:  # executors must never crash the loop
            tool_result = ToolResult(
                tool_call_id=normalized_call.id,
                status="tool_error",
                stderr=str(exc),
            )
        return ToolRuntimeResult(decision, tool_result)

    def _normalize_call(self, call: ToolCall, context: PolicyContext) -> ToolCall:
        normalizer = self.registry.normalizer_for(call.tool_name)
        normalized_call = normalizer(call) if normalizer is not None else call
        if (
            not isinstance(normalized_call, ToolCall)
            or normalized_call.id != call.id
            or normalized_call.tool_name != call.tool_name
            or normalized_call.provider_call_id != call.provider_call_id
        ):
            raise ValueError("tool call normalizer changed call identity")
        return self._normalize_call_paths(normalized_call, context)

    def _missing_required_args(self, call: ToolCall) -> list[str]:
        spec = self.registry.spec_for(call.tool_name)
        if spec is None:
            return []
        required = spec.input_schema.get("required", []) if isinstance(spec.input_schema, dict) else []
        return [name for name in required if name not in call.args]

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
