from __future__ import annotations

from dataclasses import dataclass

from phycode.context import ContextBuilder, SessionStore
from phycode.feedback import classify_feedback
from phycode.llm import LLMClient
from phycode.models import AgentEvent, AgentEventType, ToolCall
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRuntime
from phycode.trace import TraceStore


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
    ) -> None:
        self.llm = llm
        self.context_builder = context_builder
        self.tool_runtime = tool_runtime
        self.policy_context = policy_context
        self.trace_store = trace_store
        self.session_store = session_store
        self.max_steps = max_steps

    def run_once(self, user_input: str) -> AgentRunResult:
        return self.run(user_input)

    def run(self, user_input: str) -> AgentRunResult:
        all_events: list[AgentEvent] = []
        final_text: str | None = None
        current_input = user_input
        for _step in range(self.max_steps):
            messages = self.context_builder.build(current_input)
            events = self.llm.generate(messages, [])
            for event in events:
                normalized = event.model_copy(update={"session_id": self.session_store.session.id})
                self._record(normalized)
                all_events.append(normalized)

                if normalized.type == AgentEventType.ASSISTANT_FINAL:
                    final_text = str(normalized.payload.get("text", ""))
                    return AgentRunResult(final_text, all_events, "final")

                if normalized.type == AgentEventType.TOOL_CALL_REQUESTED:
                    all_events.extend(self._handle_tool_event(normalized))
            current_input = ""
        return AgentRunResult(final_text, all_events, "max_steps")

    def _handle_tool_event(self, event: AgentEvent) -> list[AgentEvent]:
        call = ToolCall(
            tool_name=str(event.payload["tool_name"]),
            args=dict(event.payload.get("args", {})),
            provider_call_id=event.payload.get("provider_call_id"),
        )
        runtime_result = self.tool_runtime.run(call, self.policy_context)
        policy_event = AgentEvent(
            session_id=self.session_store.session.id,
            type=AgentEventType.POLICY_DECISION,
            payload=runtime_result.policy.model_dump(mode="json"),
        )
        result_event = AgentEvent(
            session_id=self.session_store.session.id,
            type=AgentEventType.TOOL_CALL_OUTPUT,
            payload=runtime_result.tool_result.model_dump(mode="json"),
        )
        feedback_events = [
            AgentEvent(
                session_id=self.session_store.session.id,
                type=AgentEventType.FEEDBACK_SIGNAL,
                payload=signal.model_dump(mode="json"),
            )
            for signal in classify_feedback(runtime_result.tool_result)
        ]
        emitted = [policy_event, result_event, *feedback_events]
        for item in emitted:
            self._record(item)
        return emitted

    def _record(self, event: AgentEvent) -> None:
        self.session_store.add_event(event)
        self.trace_store.append(event)
