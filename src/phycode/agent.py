from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from phycode.context import ContextBuilder, SessionStore
from phycode.feedback import classify_feedback
from phycode.llm import LLMClient
from phycode.models import AgentEvent, AgentEventType, ToolCall
from phycode.policy import PolicyContext
from phycode.tools.base import ApprovalHandler, ToolRuntime
from phycode.trace import TraceStore

# Feedback kinds that mean "the same corrective action is not making progress".
_FAILURE_KINDS = {"command_failed", "test_failed", "tool_error", "timeout", "invalid_tool_args"}
# Provider events that should terminate the loop and defer to the stop controller.
_TERMINAL_EVENT_REASONS = {
    AgentEventType.ERROR: "error",
    AgentEventType.INCOMPLETE: "incomplete",
    AgentEventType.USER_INTERRUPT: "user_interrupt",
}


@dataclass(frozen=True)
class AgentRunResult:
    final_text: str | None
    events: list[AgentEvent]
    stopped_reason: str


class AgentLoop:
    def __init__(
        self,
        llm: LLMClient,
        context_builder: ContextBuilder,
        tool_runtime: ToolRuntime,
        policy_context: PolicyContext,
        trace_store: TraceStore,
        session_store: SessionStore,
        max_steps: int = 50,
        approval_handler: ApprovalHandler | None = None,
        max_repeated_failures: int = 3,
        max_repeated_actions: int = 3,
        max_tool_calls: int = 40,
    ) -> None:
        self.llm = llm
        self.context_builder = context_builder
        self.tool_runtime = tool_runtime
        self.policy_context = policy_context
        self.trace_store = trace_store
        self.session_store = session_store
        self.max_steps = max_steps
        self.approval_handler = approval_handler
        self.max_repeated_failures = max_repeated_failures
        self.max_repeated_actions = max_repeated_actions
        self.max_tool_calls = max_tool_calls

    def run_once(self, user_input: str) -> AgentRunResult:
        return self.run(user_input)

    def run(self, user_input: str) -> AgentRunResult:
        all_events: list[AgentEvent] = []
        final_text: str | None = None
        specs = self.tool_runtime.registry.list_specs()
        failure_streak = 0
        last_failure_key: tuple[str, str] | None = None
        action_result_counts: dict[str, int] = {}
        tool_call_count = 0
        tool_budget_warning_at = max(1, self.max_tool_calls - 2)

        for _step in range(self.max_steps):
            messages = self.context_builder.build(user_input, specs)
            try:
                events = self.llm.generate(messages, specs)
            except Exception as exc:  # provider failure -> error event, defer to stop controller
                error_event = self._new_event(AgentEventType.ERROR, {"message": str(exc)})
                self._record(error_event)
                all_events.append(error_event)
                return AgentRunResult(final_text, all_events, "error")

            for event in events:
                normalized = event.model_copy(update={"session_id": self.session_store.session.id})
                self._record(normalized)
                all_events.append(normalized)

                if normalized.type == AgentEventType.ASSISTANT_FINAL:
                    final_text = str(normalized.payload.get("text", ""))
                    return AgentRunResult(final_text, all_events, "final")

                if normalized.type in _TERMINAL_EVENT_REASONS:
                    return AgentRunResult(final_text, all_events, _TERMINAL_EVENT_REASONS[normalized.type])

                if normalized.type == AgentEventType.TOOL_CALL_REQUESTED:
                    tool_call_count += 1
                    tool_events = self._handle_tool_event(normalized)
                    all_events.extend(tool_events)
                    if tool_call_count == tool_budget_warning_at:
                        budget_event = self._new_event(
                            AgentEventType.FEEDBACK_SIGNAL,
                            {
                                "kind": "tool_budget_near_limit",
                                "summary": "Tool budget is nearly exhausted; synthesize the best-supported final answer now",
                                "evidence": {"used": tool_call_count, "limit": self.max_tool_calls},
                                "retryable": False,
                                "suggested_next_step": "Return the final answer using the evidence already gathered",
                            },
                        )
                        self._record(budget_event)
                        all_events.append(budget_event)
                    if tool_call_count >= self.max_tool_calls:
                        return self._finalize_from_evidence(user_input, all_events)
                    action_result_key = self._successful_action_result_key(normalized, tool_events)
                    if action_result_key is not None:
                        action_result_counts[action_result_key] = action_result_counts.get(action_result_key, 0) + 1
                        if action_result_counts[action_result_key] >= self.max_repeated_actions:
                            return self._finalize_from_evidence(user_input, all_events)
                    failure_key = self._failure_key(normalized, tool_events)
                    if failure_key is None:
                        failure_streak = 0
                        last_failure_key = None
                    else:
                        failure_streak = failure_streak + 1 if failure_key == last_failure_key else 1
                        last_failure_key = failure_key
                        if failure_streak >= self.max_repeated_failures:
                            return AgentRunResult(final_text, all_events, "repeated_failure")
        return AgentRunResult(final_text, all_events, "max_steps")

    def _finalize_from_evidence(self, user_input: str, all_events: list[AgentEvent]) -> AgentRunResult:
        finalization_input = (
            f"{user_input}\n\n"
            "Tool use is now disabled. Do not emit a tool call, search expression, or plan. "
            "Using only the evidence already gathered, return the best-supported answer now in the requested format."
        )
        messages = self.context_builder.build(finalization_input, [])
        try:
            events = self.llm.generate(messages, [])
        except Exception as exc:
            error_event = self._new_event(AgentEventType.ERROR, {"message": str(exc)})
            self._record(error_event)
            all_events.append(error_event)
            return AgentRunResult(None, all_events, "error")

        for event in events:
            normalized = event.model_copy(update={"session_id": self.session_store.session.id})
            self._record(normalized)
            all_events.append(normalized)
            if normalized.type == AgentEventType.ASSISTANT_FINAL:
                return AgentRunResult(str(normalized.payload.get("text", "")), all_events, "final")
            if normalized.type in _TERMINAL_EVENT_REASONS:
                return AgentRunResult(None, all_events, _TERMINAL_EVENT_REASONS[normalized.type])
        return AgentRunResult(None, all_events, "tool_budget")

    def _successful_action_result_key(self, request: AgentEvent, tool_events: list[AgentEvent]) -> str | None:
        output = next((item for item in tool_events if item.type == AgentEventType.TOOL_CALL_OUTPUT), None)
        if output is None or output.payload.get("status") != "ok":
            return None
        payload = {
            "tool_name": request.payload.get("tool_name"),
            "args": request.payload.get("args", {}),
            "stdout": output.payload.get("stdout", ""),
            "stderr": output.payload.get("stderr", ""),
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _handle_tool_event(self, event: AgentEvent) -> list[AgentEvent]:
        call = ToolCall(
            tool_name=str(event.payload["tool_name"]),
            args=dict(event.payload.get("args", {})),
            provider_call_id=event.payload.get("provider_call_id"),
        )
        runtime_result = self.tool_runtime.run(
            call, self.policy_context, approval_handler=self.approval_handler
        )
        policy_event = self._new_event(
            AgentEventType.POLICY_DECISION, runtime_result.policy.model_dump(mode="json")
        )
        result_event = self._new_event(
            AgentEventType.TOOL_CALL_OUTPUT, runtime_result.tool_result.model_dump(mode="json")
        )
        feedback_events = [
            self._new_event(AgentEventType.FEEDBACK_SIGNAL, signal.model_dump(mode="json"))
            for signal in classify_feedback(runtime_result.tool_result)
        ]
        emitted = [policy_event, result_event, *feedback_events]
        for item in emitted:
            self._record(item)
        return emitted

    def _failure_key(self, request: AgentEvent, tool_events: list[AgentEvent]) -> tuple[str, str] | None:
        tool_name = str(request.payload.get("tool_name", ""))
        for item in tool_events:
            if item.type != AgentEventType.FEEDBACK_SIGNAL:
                continue
            kind = str(item.payload.get("kind", ""))
            if kind in _FAILURE_KINDS:
                return (tool_name, kind)
        return None

    def _new_event(self, event_type: AgentEventType, payload: dict) -> AgentEvent:
        return AgentEvent(session_id=self.session_store.session.id, type=event_type, payload=payload)

    def _record(self, event: AgentEvent) -> None:
        self.session_store.add_event(event)
        self.trace_store.append(event)
