from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from phycode.context import ContextBuilder, SessionStore
from phycode.feedback import classify_feedback
from phycode.llm import LLMClient
from phycode.models import AgentEvent, AgentEventType, ToolCall
from phycode.policy import PolicyContext
from phycode.tools.base import ApprovalHandler, ToolRuntime
from phycode.trace import TraceStore

EventSink = Callable[[AgentEvent], None]

# Feedback kinds that mean "the same corrective action is not making progress".
_FAILURE_KINDS = {"command_failed", "test_failed", "tool_error", "timeout", "invalid_tool_args"}
# Tools that change workspace/state; a successful one counts as real progress and
# resets the no-progress loop guard so legitimate iteration (edit -> re-test) is allowed.
_MUTATING_TOOLS = {"file.write", "file.edit", "memory.write", "config.write", "shell.run", "test.run"}
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
        max_repeated_calls: int = 5,
        event_sink: EventSink | None = None,
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
        self.max_repeated_calls = max_repeated_calls
        self.event_sink = event_sink

    def run_once(self, user_input: str) -> AgentRunResult:
        return self.run(user_input)

    def run(self, user_input: str) -> AgentRunResult:
        all_events: list[AgentEvent] = []
        final_text: str | None = None
        current_input = user_input
        specs = self.tool_runtime.registry.list_specs()
        failure_streak = 0
        last_failure_key: tuple[str, str] | None = None
        call_counts: dict[tuple[str, str], int] = {}

        for _step in range(self.max_steps):
            messages = self.context_builder.build(current_input, specs)
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
                    tool_events = self._handle_tool_event(normalized)
                    all_events.extend(tool_events)
                    failure_key = self._failure_key(normalized, tool_events)
                    if failure_key is None:
                        failure_streak = 0
                        last_failure_key = None
                    else:
                        failure_streak = failure_streak + 1 if failure_key == last_failure_key else 1
                        last_failure_key = failure_key
                        if failure_streak >= self.max_repeated_failures:
                            return AgentRunResult(final_text, all_events, "repeated_failure")

                    # No-progress loop guard: a mutating tool that succeeds is progress
                    # (reset); otherwise count repeats of the same (tool, args) and stop
                    # if the model keeps re-issuing it without changing anything.
                    if self._made_progress(normalized, tool_events):
                        call_counts.clear()
                    else:
                        signature = self._call_signature(normalized)
                        call_counts[signature] = call_counts.get(signature, 0) + 1
                        if call_counts[signature] >= self.max_repeated_calls:
                            return AgentRunResult(final_text, all_events, "repeated_calls")
            current_input = ""
        return AgentRunResult(final_text, all_events, "max_steps")

    def _made_progress(self, request: AgentEvent, tool_events: list[AgentEvent]) -> bool:
        if str(request.payload.get("tool_name", "")) not in _MUTATING_TOOLS:
            return False
        return any(
            item.type == AgentEventType.FEEDBACK_SIGNAL and item.payload.get("kind") == "success"
            for item in tool_events
        )

    def _call_signature(self, request: AgentEvent) -> tuple[str, str]:
        tool_name = str(request.payload.get("tool_name", ""))
        try:
            args_key = json.dumps(request.payload.get("args", {}), sort_keys=True, default=str)
        except TypeError:
            args_key = str(request.payload.get("args", {}))
        return (tool_name, args_key)

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
        if self.event_sink is not None:
            self.event_sink(event)
