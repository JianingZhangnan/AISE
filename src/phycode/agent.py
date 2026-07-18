from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from phycode.context import ContextBuilder, SessionStore
from phycode.event_projection import project_agent_event
from phycode.feedback import artifact_verification_feedback, classify_feedback
from phycode.llm import LLMClient
from phycode.models import AgentEvent, AgentEventType, ToolCall
from phycode.policy import PolicyContext
from phycode.tools.base import ApprovalHandler, ToolRuntime
from phycode.trace import TraceStore

# Feedback kinds that mean "the same corrective action is not making progress".
_FAILURE_KINDS = {
    "command_failed",
    "test_failed",
    "tool_error",
    "timeout",
    "invalid_tool_args",
    "policy_blocked",
    "policy_requires_approval",
}
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
    terminal_blocker: str | None = None


@dataclass(frozen=True)
class _VerificationOutcome:
    ok: bool
    feedback_event: AgentEvent | None = None
    fatal: bool = False


class CompletionVerification(Protocol):
    @property
    def ok(self) -> bool: ...

    @property
    def issues(self) -> Sequence[Any]: ...


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
        completion_verifier: Callable[[], CompletionVerification] | None = None,
        progress_fingerprint: Callable[[], str] | None = None,
        verify_after_successful_tool: bool = False,
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
        self.completion_verifier = completion_verifier
        self.progress_fingerprint = progress_fingerprint
        self.verify_after_successful_tool = verify_after_successful_tool

    def run_once(self, user_input: str) -> AgentRunResult:
        return self.run(user_input)

    def run(self, user_input: str) -> AgentRunResult:
        all_events: list[AgentEvent] = []
        if self.max_tool_calls <= 0:
            return self._finish_tool_budget(user_input, all_events, None)
        final_text: str | None = None
        specs = self.tool_runtime.registry.list_specs()
        failure_streak = 0
        last_failure_key: tuple[str, str] | None = None
        last_action_result_key: str | None = None
        consecutive_repeat_count = 0
        last_progress_fingerprint: str | None = None
        tool_call_count = 0
        current_blocker: str | None = None
        tool_budget_warning_at = max(1, self.max_tool_calls - 2)

        for _step in range(self.max_steps):
            messages = self.context_builder.build(user_input, specs)
            try:
                events = self.llm.generate(messages, specs)
            except Exception as exc:  # provider failure -> error event, defer to stop controller
                error_event = self._new_event(AgentEventType.ERROR, {"message": str(exc)})
                self._record(error_event)
                all_events.append(error_event)
                return AgentRunResult(final_text, all_events, "error", "provider_error")

            for event in events:
                normalized = event.model_copy(update={"session_id": self.session_store.session.id})
                self._record(normalized)
                all_events.append(normalized)

                if normalized.type == AgentEventType.ASSISTANT_FINAL:
                    candidate_text = str(normalized.payload.get("text", ""))
                    if self.completion_verifier is None:
                        return AgentRunResult(candidate_text, all_events, "final")
                    verification = self._verify_completion()
                    if verification.ok:
                        return AgentRunResult(candidate_text, all_events, "completed")
                    assert verification.feedback_event is not None
                    feedback_event = verification.feedback_event
                    self._record(feedback_event)
                    all_events.append(feedback_event)
                    if verification.fatal:
                        return AgentRunResult(
                            None,
                            all_events,
                            "artifact_verification_failed",
                            "artifact_verification_failed",
                        )
                    if _step + 1 >= self.max_steps:
                        return AgentRunResult(
                            None,
                            all_events,
                            "artifact_verification_failed",
                            "artifact_verification_failed",
                        )
                    last_action_result_key = None
                    consecutive_repeat_count = 0
                    last_progress_fingerprint = None
                    break

                if normalized.type in _TERMINAL_EVENT_REASONS:
                    return AgentRunResult(
                        final_text,
                        all_events,
                        _TERMINAL_EVENT_REASONS[normalized.type],
                        "provider_error",
                    )

                if normalized.type == AgentEventType.TOOL_CALL_REQUESTED:
                    tool_call_count += 1
                    tool_events = self._handle_tool_event(normalized)
                    all_events.extend(tool_events)
                    current_blocker = self._updated_blocker(
                        current_blocker,
                        normalized,
                        tool_events,
                    )
                    action_result_key = self._successful_action_result_key(normalized, tool_events)
                    if self.verify_after_successful_tool and action_result_key is not None:
                        verification = self._verify_completion()
                        if verification.ok:
                            return AgentRunResult(None, all_events, "completed")
                        if verification.fatal:
                            assert verification.feedback_event is not None
                            self._record(verification.feedback_event)
                            all_events.append(verification.feedback_event)
                            return AgentRunResult(
                                None,
                                all_events,
                                "artifact_verification_failed",
                                "artifact_verification_failed",
                            )
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
                        return self._finish_tool_budget(user_input, all_events, current_blocker)
                    if action_result_key is not None:
                        try:
                            progress_fingerprint = (
                                self.progress_fingerprint() if self.progress_fingerprint is not None else None
                            )
                        except Exception:
                            error_event = self._new_event(
                                AgentEventType.ERROR,
                                {"message": "Progress fingerprint failed"},
                            )
                            self._record(error_event)
                            all_events.append(error_event)
                            return AgentRunResult(None, all_events, "error", "provider_error")
                        if (
                            action_result_key == last_action_result_key
                            and progress_fingerprint == last_progress_fingerprint
                        ):
                            consecutive_repeat_count += 1
                        else:
                            consecutive_repeat_count = 1
                        last_action_result_key = action_result_key
                        last_progress_fingerprint = progress_fingerprint
                        if consecutive_repeat_count >= self.max_repeated_actions:
                            if (
                                self.completion_verifier is None
                                and self.progress_fingerprint is None
                            ):
                                return self._finalize_from_evidence(user_input, all_events)
                            return AgentRunResult(
                                None,
                                all_events,
                                "repeated_no_progress",
                                "repeated_no_progress",
                            )
                    else:
                        last_action_result_key = None
                        consecutive_repeat_count = 0
                        last_progress_fingerprint = None
                    failure_key = self._failure_key(normalized, tool_events)
                    if failure_key is None:
                        failure_streak = 0
                        last_failure_key = None
                    else:
                        failure_streak = failure_streak + 1 if failure_key == last_failure_key else 1
                        last_failure_key = failure_key
                        if failure_streak >= self.max_repeated_failures:
                            return AgentRunResult(
                                final_text,
                                all_events,
                                "repeated_failure",
                                current_blocker or "artifact_verification_failed",
                            )
        if self.completion_verifier is not None:
            verification = self._verify_completion()
            if verification.ok:
                return AgentRunResult(None, all_events, "completed")
            assert verification.feedback_event is not None
            self._record(verification.feedback_event)
            all_events.append(verification.feedback_event)
            return AgentRunResult(
                None,
                all_events,
                "artifact_verification_failed",
                "artifact_verification_failed",
            )
        return AgentRunResult(final_text, all_events, "max_steps")

    def _finish_tool_budget(
        self,
        user_input: str,
        all_events: list[AgentEvent],
        current_blocker: str | None,
    ) -> AgentRunResult:
        if self.completion_verifier is None:
            return self._finalize_from_evidence(user_input, all_events)
        verification = self._verify_completion()
        if verification.ok:
            return AgentRunResult(None, all_events, "completed")
        assert verification.feedback_event is not None
        self._record(verification.feedback_event)
        all_events.append(verification.feedback_event)
        if verification.fatal:
            return AgentRunResult(
                None,
                all_events,
                "artifact_verification_failed",
                "artifact_verification_failed",
            )
        return AgentRunResult(
            None,
            all_events,
            "artifact_verification_failed",
            current_blocker or "tool_budget_exhausted",
        )

    def _verify_completion(self) -> _VerificationOutcome:
        assert self.completion_verifier is not None
        try:
            verification = self.completion_verifier()
            if verification.ok:
                return _VerificationOutcome(ok=True)
            issues = [issue.model_dump(mode="json") for issue in verification.issues]
        except Exception:
            issues = [
                {
                    "code": "verifier_error",
                    "path": "",
                    "message": "Artifact verification could not be completed safely",
                }
            ]
            signal = artifact_verification_feedback(issues)
            event = self._new_event(AgentEventType.FEEDBACK_SIGNAL, signal.model_dump(mode="json"))
            return _VerificationOutcome(ok=False, feedback_event=event, fatal=True)
        signal = artifact_verification_feedback(issues)
        event = self._new_event(AgentEventType.FEEDBACK_SIGNAL, signal.model_dump(mode="json"))
        return _VerificationOutcome(ok=False, feedback_event=event)

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
            "result": {
                key: value
                for key, value in output.payload.items()
                if key != "tool_call_id"
            },
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
            for signal in classify_feedback(
                runtime_result.tool_result,
                profile=self.policy_context.profile_spec.profile,
                policy_rule_id=runtime_result.policy.rule_id,
            )
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

    def _updated_blocker(
        self,
        current: str | None,
        request: AgentEvent,
        tool_events: list[AgentEvent],
    ) -> str | None:
        output = next(
            (event for event in tool_events if event.type == AgentEventType.TOOL_CALL_OUTPUT),
            None,
        )
        if output is None:
            return current
        status = str(output.payload.get("status", ""))
        tool_name = str(request.payload.get("tool_name", ""))
        if status == "policy_blocked":
            return "policy_blocked"
        if status == "policy_requires_approval":
            return "approval_required"
        if tool_name == "process.run" and status in {
            "command_failed",
            "timeout",
            "tool_error",
            "invalid_tool_args",
        }:
            return "process_failed"
        if status == "ok":
            if current in {"policy_blocked", "approval_required"}:
                return None
            if current == "process_failed" and tool_name == "process.run":
                return None
        return current

    def _new_event(self, event_type: AgentEventType, payload: dict) -> AgentEvent:
        return AgentEvent(session_id=self.session_store.session.id, type=event_type, payload=payload)

    def _record(self, event: AgentEvent) -> None:
        projected = project_agent_event(event, self.policy_context.workspace_root.resolve())
        self.session_store.add_event(projected)
        self.trace_store.append(projected)
