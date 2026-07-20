from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from phycode.context import ContextBuilder, SessionStore
from phycode.event_projection import project_agent_event
from phycode.feedback import artifact_verification_feedback, classify_feedback
from phycode.llm import LLMClient
from phycode.models import (
    AgentEvent,
    AgentEventType,
    PolicyAction,
    PolicyDecision,
    ToolCall,
    ToolResult,
)
from phycode.policy import PolicyContext
from phycode.redaction import redact_obj
from phycode.tools.base import ApprovalHandler, ToolRuntime
from phycode.trace import TraceStore

EventSink = Callable[[AgentEvent], None]

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
_NON_ACTIONABLE_FEEDBACK_KINDS = {"success", "stale_tool_batch"}
# Provider events that should terminate the loop and defer to the stop controller.
_TERMINAL_EVENT_REASONS = {
    AgentEventType.ERROR: "error",
    AgentEventType.INCOMPLETE: "incomplete",
    AgentEventType.USER_INTERRUPT: "user_interrupt",
}
_DISCOVERY_TOOL_NAMES = frozenset(
    {
        "file.read",
        "file.inspect",
        "file.list",
        "search.glob",
        "search.grep",
        "workspace.status",
    }
)


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


@dataclass(frozen=True)
class _ProcessTargetIdentity:
    cwd: Path
    script: Path
    trailing_argv: tuple[str, ...]


@dataclass(frozen=True)
class _ActionIdentity:
    tool_name: str
    args_sha256: str
    script_sha256: str | None = None
    process_target: _ProcessTargetIdentity | None = None


@dataclass(frozen=True)
class _CausalBlocker:
    reason: str
    rule_id: str
    action: _ActionIdentity


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
        max_repeated_calls: int = 5,
        max_discovery_tool_calls: int | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        if max_discovery_tool_calls is not None and (
            isinstance(max_discovery_tool_calls, bool)
            or not isinstance(max_discovery_tool_calls, int)
            or max_discovery_tool_calls < 0
        ):
            raise ValueError("max_discovery_tool_calls must be a non-negative integer or None")
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
        self.max_repeated_calls = max_repeated_calls
        self.max_discovery_tool_calls = max_discovery_tool_calls
        self.event_sink = event_sink

    def run_once(self, user_input: str) -> AgentRunResult:
        return self.run(user_input)

    def run(self, user_input: str) -> AgentRunResult:
        all_events: list[AgentEvent] = []
        user_event = self.context_builder.begin_turn(user_input)
        if user_event is not None:
            self.trace_store.append(user_event)
        if self.max_tool_calls <= 0:
            return self._finish_tool_budget(user_input, all_events, None)
        final_text: str | None = None
        specs = self.tool_runtime.registry.list_specs()
        failure_streak = 0
        last_failure_key: tuple[_ActionIdentity, str] | None = None
        last_action_result_key: str | None = None
        consecutive_repeat_count = 0
        last_progress_fingerprint: str | None = None
        progress_epoch_signatures: list[str] = []
        tool_call_count = 0
        discovery_tool_call_count = 0
        current_blocker: _CausalBlocker | None = None
        tool_budget_warning_at = max(1, self.max_tool_calls - 2)
        call_counts: dict[tuple[str, str], int] = {}

        for _step in range(self.max_steps):
            messages = self.context_builder.build(user_input, specs)
            try:
                events = self.llm.generate(messages, specs)
            except Exception as exc:  # provider failure -> error event, defer to stop controller
                error_event = self._new_event(AgentEventType.ERROR, {"message": str(exc)})
                self._record_and_collect(error_event, all_events)
                return AgentRunResult(final_text, all_events, "error", "provider_error")

            feedback_barrier = False
            for event in events:
                normalized = event.model_copy(update={"session_id": self.session_store.session.id})
                recorded = self._record(normalized)
                all_events.append(recorded)

                if recorded.type == AgentEventType.ASSISTANT_FINAL:
                    candidate_text = str(recorded.payload.get("text", ""))
                    if self.completion_verifier is None:
                        return AgentRunResult(candidate_text, all_events, "final")
                    verification = self._verify_completion()
                    if verification.ok:
                        return AgentRunResult(candidate_text, all_events, "completed")
                    assert verification.feedback_event is not None
                    feedback_event = verification.feedback_event
                    self._record_and_collect(feedback_event, all_events)
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
                    if self.progress_fingerprint is None:
                        last_action_result_key = None
                        consecutive_repeat_count = 0
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
                    action_identity = self._action_identity(normalized)
                    skipped_by_barrier = feedback_barrier
                    if skipped_by_barrier:
                        tool_events = self._skip_stale_tool_event(normalized)
                    elif self._discovery_budget_exhausted(
                        normalized,
                        discovery_tool_call_count,
                    ):
                        tool_events = self._deny_discovery_tool_event(normalized)
                    else:
                        if self._is_discovery_tool_event(normalized):
                            discovery_tool_call_count += 1
                        tool_events = self._handle_tool_event(normalized)
                    all_events.extend(tool_events)
                    current_blocker = self._updated_blocker(
                        current_blocker,
                        normalized,
                        tool_events,
                        action_identity,
                    )
                    action_result_key = self._successful_action_result_key(normalized, tool_events)
                    if not skipped_by_barrier and self._establishes_feedback_barrier(
                        normalized,
                        tool_events,
                    ):
                        feedback_barrier = True
                    if (
                        self.verify_after_successful_tool
                        and action_result_key is not None
                        and tool_call_count < self.max_tool_calls
                    ):
                        verification = self._verify_completion()
                        if verification.ok:
                            return AgentRunResult(None, all_events, "completed")
                        assert verification.feedback_event is not None
                        self._record_and_collect(verification.feedback_event, all_events)
                        feedback_barrier = True
                        if verification.fatal:
                            return AgentRunResult(
                                None,
                                all_events,
                                "artifact_verification_failed",
                                "artifact_verification_failed",
                            )
                    if tool_call_count == tool_budget_warning_at:
                        if self.completion_verifier is None:
                            summary = (
                                "Tool budget is nearly exhausted; synthesize the best-supported "
                                "final answer now"
                            )
                            retryable = False
                            suggested_next_step = (
                                "Return the final answer using the evidence already gathered"
                            )
                        else:
                            summary = (
                                "Tool budget is nearly exhausted and required artifacts are still "
                                "unverified"
                            )
                            retryable = True
                            suggested_next_step = (
                                "Use remaining calls only to fill missing artifacts, run required "
                                "steps, inspect outputs, and verify completion"
                            )
                        budget_event = self._new_event(
                            AgentEventType.FEEDBACK_SIGNAL,
                            {
                                "kind": "tool_budget_near_limit",
                                "cause": "tool_budget",
                                "summary": summary,
                                "evidence": {"used": tool_call_count, "limit": self.max_tool_calls},
                                "retryable": retryable,
                                "suggested_next_step": suggested_next_step,
                            },
                        )
                        self._record_and_collect(budget_event, all_events)
                    if skipped_by_barrier:
                        if tool_call_count >= self.max_tool_calls:
                            return self._finish_tool_budget(
                                user_input,
                                all_events,
                                current_blocker,
                            )
                        continue
                    if action_result_key is not None and (
                        self.completion_verifier is not None
                        or self.progress_fingerprint is not None
                    ):
                        try:
                            progress_fingerprint = (
                                self.progress_fingerprint() if self.progress_fingerprint is not None else None
                            )
                        except Exception:
                            error_event = self._new_event(
                                AgentEventType.ERROR,
                                {"message": "Progress fingerprint failed"},
                            )
                            self._record_and_collect(error_event, all_events)
                            return AgentRunResult(None, all_events, "error", "provider_error")
                        if self.progress_fingerprint is not None:
                            if progress_fingerprint != last_progress_fingerprint:
                                progress_epoch_signatures = [action_result_key]
                            else:
                                progress_epoch_signatures.append(action_result_key)
                            repeated_without_progress = self._progress_epoch_repeats(
                                progress_epoch_signatures
                            )
                        else:
                            if action_result_key == last_action_result_key:
                                consecutive_repeat_count += 1
                            else:
                                consecutive_repeat_count = 1
                            repeated_without_progress = (
                                consecutive_repeat_count >= self.max_repeated_actions
                            )
                        last_action_result_key = action_result_key
                        last_progress_fingerprint = progress_fingerprint
                        if repeated_without_progress:
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
                        if self.progress_fingerprint is None:
                            last_action_result_key = None
                            consecutive_repeat_count = 0
                    if tool_call_count >= self.max_tool_calls:
                        return self._finish_tool_budget(user_input, all_events, current_blocker)
                    failure_key = self._failure_key(action_identity, tool_events)
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
                                self._blocker_reason(current_blocker)
                                or "artifact_verification_failed",
                            )
                    if (
                        self.completion_verifier is None
                        and self.progress_fingerprint is None
                    ):
                        if self._made_progress(normalized, tool_events):
                            call_counts.clear()
                        else:
                            signature = self._call_signature(normalized)
                            call_counts[signature] = call_counts.get(signature, 0) + 1
                            if call_counts[signature] >= self.max_repeated_calls:
                                return AgentRunResult(
                                    final_text,
                                    all_events,
                                    "repeated_calls",
                                )
        if self.completion_verifier is not None:
            verification = self._verify_completion()
            if verification.ok:
                return AgentRunResult(None, all_events, "completed")
            assert verification.feedback_event is not None
            self._record_and_collect(verification.feedback_event, all_events)
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
        current_blocker: _CausalBlocker | None,
    ) -> AgentRunResult:
        if self.completion_verifier is None:
            return self._finalize_from_evidence(user_input, all_events)
        verification = self._verify_completion()
        if verification.ok:
            return AgentRunResult(None, all_events, "completed")
        assert verification.feedback_event is not None
        self._record_and_collect(verification.feedback_event, all_events)
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
            self._blocker_reason(current_blocker) or "tool_budget_exhausted",
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
            event = self._new_event(
                AgentEventType.FEEDBACK_SIGNAL,
                {**signal.model_dump(mode="json"), "cause": "completion_verifier"},
            )
            return _VerificationOutcome(ok=False, feedback_event=event, fatal=True)
        signal = artifact_verification_feedback(issues)
        event = self._new_event(
            AgentEventType.FEEDBACK_SIGNAL,
            {**signal.model_dump(mode="json"), "cause": "completion_verifier"},
        )
        return _VerificationOutcome(ok=False, feedback_event=event)

    def _finalize_from_evidence(self, user_input: str, all_events: list[AgentEvent]) -> AgentRunResult:
        finalization_input = (
            f"{user_input}\n\n"
            "Tool use is now disabled. Do not emit a tool call, search expression, or plan. "
            "Using only the evidence already gathered, return the best-supported answer now in the requested format."
        )
        self.context_builder.begin_turn(finalization_input, persist=False)
        messages = self.context_builder.build(finalization_input, [])
        try:
            events = self.llm.generate(messages, [])
        except Exception as exc:
            error_event = self._new_event(AgentEventType.ERROR, {"message": str(exc)})
            recorded_error = self._record(error_event)
            all_events.append(recorded_error)
            return AgentRunResult(None, all_events, "error")

        for event in events:
            normalized = event.model_copy(update={"session_id": self.session_store.session.id})
            recorded = self._record(normalized)
            all_events.append(recorded)
            if recorded.type == AgentEventType.ASSISTANT_FINAL:
                return AgentRunResult(str(recorded.payload.get("text", "")), all_events, "final")
            if recorded.type in _TERMINAL_EVENT_REASONS:
                return AgentRunResult(None, all_events, _TERMINAL_EVENT_REASONS[recorded.type])
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

    def _progress_epoch_repeats(self, signatures: list[str]) -> bool:
        if not signatures:
            return False
        required_repetitions = max(1, self.max_repeated_actions)
        for cycle_length in range(1, len(signatures) // required_repetitions + 1):
            cycle = signatures[-cycle_length:]
            if signatures[-cycle_length * required_repetitions :] == (
                cycle * required_repetitions
            ):
                return True
        return False

    def _made_progress(
        self,
        request: AgentEvent,
        tool_events: list[AgentEvent],
    ) -> bool:
        spec = self.tool_runtime.registry.spec_for(
            str(request.payload.get("tool_name", ""))
        )
        if spec is None or not spec.mutates_state:
            return False
        return any(
            item.type == AgentEventType.FEEDBACK_SIGNAL
            and item.payload.get("kind") == "success"
            for item in tool_events
        )

    @staticmethod
    def _call_signature(request: AgentEvent) -> tuple[str, str]:
        tool_name = str(request.payload.get("tool_name", ""))
        try:
            args_key = json.dumps(
                request.payload.get("args", {}),
                sort_keys=True,
                default=str,
            )
        except TypeError:
            args_key = str(request.payload.get("args", {}))
        return tool_name, args_key

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
            self._new_event(
                AgentEventType.FEEDBACK_SIGNAL,
                {
                    **signal.model_dump(mode="json"),
                    "cause": (
                        f"{call.tool_name}:{runtime_result.policy.rule_id}:{signal.kind.value}"
                    ),
                },
            )
            for signal in classify_feedback(
                runtime_result.tool_result,
                profile=self.policy_context.profile_spec.profile,
                policy_rule_id=runtime_result.policy.rule_id,
            )
        ]
        return [self._record(item) for item in [policy_event, result_event, *feedback_events]]

    @staticmethod
    def _is_discovery_tool_event(event: AgentEvent) -> bool:
        return str(event.payload.get("tool_name", "")) in _DISCOVERY_TOOL_NAMES

    def _discovery_budget_exhausted(
        self,
        event: AgentEvent,
        discovery_tool_call_count: int,
    ) -> bool:
        return (
            self.max_discovery_tool_calls is not None
            and self._is_discovery_tool_event(event)
            and discovery_tool_call_count >= self.max_discovery_tool_calls
        )

    def _deny_discovery_tool_event(self, event: AgentEvent) -> list[AgentEvent]:
        tool_name = str(event.payload.get("tool_name", ""))
        tool_call_id = str(event.payload.get("provider_call_id") or event.id)
        reason = (
            "PRBench discovery tool-call budget exhausted; switch to production tools"
        )
        decision = PolicyDecision(
            tool_call_id=tool_call_id,
            decision=PolicyAction.DENY,
            rule_id="runtime.discovery_budget",
            reason=reason,
            requires_user=False,
        )
        result = ToolResult(
            tool_call_id=tool_call_id,
            status="policy_blocked",
            stderr=reason,
        )
        policy_event = self._new_event(
            AgentEventType.POLICY_DECISION,
            decision.model_dump(mode="json"),
        )
        result_event = self._new_event(
            AgentEventType.TOOL_CALL_OUTPUT,
            result.model_dump(mode="json"),
        )
        feedback_event = self._new_event(
            AgentEventType.FEEDBACK_SIGNAL,
            {
                "kind": "policy_blocked",
                "cause": f"{tool_name}:runtime.discovery_budget:policy_blocked",
                "summary": reason,
                "evidence": {
                    "tool_name": tool_name,
                    "used": self.max_discovery_tool_calls,
                    "limit": self.max_discovery_tool_calls,
                },
                "retryable": True,
                "suggested_next_step": (
                    "Use file.write or file.edit to implement required artifacts, then "
                    "request process.run for each execution entrypoint"
                ),
            },
        )
        return [
            self._record(item)
            for item in (policy_event, result_event, feedback_event)
        ]

    def _skip_stale_tool_event(self, event: AgentEvent) -> list[AgentEvent]:
        result = ToolResult(
            tool_call_id=str(event.payload.get("provider_call_id") or event.id),
            status="stale_tool_batch",
            stderr=(
                "Skipped because an earlier call in the same provider response changed state "
                "or produced corrective feedback"
            ),
        )
        result_event = self._new_event(
            AgentEventType.TOOL_CALL_OUTPUT,
            result.model_dump(mode="json"),
        )
        feedback_events = [
            self._new_event(AgentEventType.FEEDBACK_SIGNAL, signal.model_dump(mode="json"))
            for signal in classify_feedback(
                result,
                profile=self.policy_context.profile_spec.profile,
            )
        ]
        return [self._record(item) for item in [result_event, *feedback_events]]

    def _establishes_feedback_barrier(
        self,
        request: AgentEvent,
        tool_events: list[AgentEvent],
    ) -> bool:
        if any(
            event.type == AgentEventType.FEEDBACK_SIGNAL
            and str(event.payload.get("kind", "")) not in _NON_ACTIONABLE_FEEDBACK_KINDS
            for event in tool_events
        ):
            return True
        output = next(
            (event for event in tool_events if event.type == AgentEventType.TOOL_CALL_OUTPUT),
            None,
        )
        if output is None or output.payload.get("status") != "ok":
            return True
        spec = self.tool_runtime.registry.spec_for(str(request.payload.get("tool_name", "")))
        return bool(spec is not None and spec.mutates_state)

    def _failure_key(
        self,
        action: _ActionIdentity,
        tool_events: list[AgentEvent],
    ) -> tuple[_ActionIdentity, str] | None:
        for item in tool_events:
            if item.type != AgentEventType.FEEDBACK_SIGNAL:
                continue
            kind = str(item.payload.get("kind", ""))
            if kind in _FAILURE_KINDS:
                return (action, kind)
        return None

    def _updated_blocker(
        self,
        current: _CausalBlocker | None,
        request: AgentEvent,
        tool_events: list[AgentEvent],
        action: _ActionIdentity,
    ) -> _CausalBlocker | None:
        output = next(
            (event for event in tool_events if event.type == AgentEventType.TOOL_CALL_OUTPUT),
            None,
        )
        if output is None:
            return current
        status = str(output.payload.get("status", ""))
        tool_name = str(request.payload.get("tool_name", ""))
        policy = next(
            (event for event in tool_events if event.type == AgentEventType.POLICY_DECISION),
            None,
        )
        rule_id = str(policy.payload.get("rule_id", "unknown")) if policy is not None else "unknown"
        if status == "policy_blocked":
            if rule_id == "runtime.discovery_budget":
                return current
            return _CausalBlocker("policy_blocked", rule_id, action)
        if status == "policy_requires_approval":
            return _CausalBlocker("approval_required", rule_id, action)
        if tool_name == "process.run" and status in {
            "command_failed",
            "timeout",
            "tool_error",
            "invalid_tool_args",
        }:
            return _CausalBlocker("process_failed", rule_id, action)
        if status == "ok":
            if (
                current is not None
                and current.reason == "policy_blocked"
                and current.rule_id == "prbench.direct_csv_mutation_blocked"
                and self._is_reproduction_script_mutation(request)
            ):
                return None
            if (
                current is not None
                and current.reason in {"approval_required", "process_failed"}
                and self._successful_process_supersedes(current.action, action)
            ):
                return None
        return current

    @staticmethod
    def _blocker_reason(blocker: _CausalBlocker | None) -> str | None:
        return blocker.reason if blocker is not None else None

    def _action_identity(self, request: AgentEvent) -> _ActionIdentity:
        tool_name = str(request.payload.get("tool_name", ""))
        args = dict(request.payload.get("args", {}))
        encoded = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        script_sha256 = self._process_script_sha256(args) if tool_name == "process.run" else None
        return _ActionIdentity(
            tool_name=tool_name,
            args_sha256=hashlib.sha256(encoded).hexdigest(),
            script_sha256=script_sha256,
            process_target=(
                self._process_script_target(args) if tool_name == "process.run" else None
            ),
        )

    @staticmethod
    def _successful_process_supersedes(
        blocked: _ActionIdentity,
        successful: _ActionIdentity,
    ) -> bool:
        if blocked == successful:
            return True
        return (
            blocked.tool_name == "process.run"
            and successful.tool_name == "process.run"
            and blocked.process_target is not None
            and blocked.process_target == successful.process_target
        )

    def _process_script_target(
        self,
        args: dict[str, Any],
    ) -> _ProcessTargetIdentity | None:
        argv = args.get("argv")
        cwd_value = args.get("cwd", ".")
        if not isinstance(argv, list) or len(argv) < 2 or not isinstance(argv[1], str):
            return None
        if not isinstance(cwd_value, str):
            return None
        trailing_argv = argv[2:]
        if any(not isinstance(item, str) for item in trailing_argv):
            return None
        workspace = self.policy_context.workspace_root.resolve()
        try:
            cwd = Path(cwd_value)
            if not cwd.is_absolute():
                cwd = workspace / cwd
            cwd = cwd.resolve(strict=False)
            cwd.relative_to(workspace)
            script = Path(argv[1])
            if not script.is_absolute():
                script = cwd / script
            script = script.resolve(strict=False)
            script.relative_to(workspace)
        except (OSError, RuntimeError, ValueError):
            return None
        if script.suffix.casefold() != ".py":
            return None
        return _ProcessTargetIdentity(cwd, script, tuple(trailing_argv))

    def _process_script_sha256(self, args: dict[str, Any]) -> str | None:
        target = self._process_script_target(args)
        if target is None:
            return None
        try:
            if not target.script.is_file():
                return None
            return hashlib.sha256(target.script.read_bytes()).hexdigest()
        except (OSError, RuntimeError, ValueError):
            return None

    def _is_reproduction_script_mutation(self, request: AgentEvent) -> bool:
        if str(request.payload.get("tool_name", "")) not in {"file.write", "file.edit"}:
            return False
        path_value = request.payload.get("args", {}).get("path")
        if not isinstance(path_value, str):
            return False
        workspace = self.policy_context.workspace_root.resolve()
        try:
            candidate = Path(path_value)
            if not candidate.is_absolute():
                candidate = workspace / candidate
            relative = candidate.resolve(strict=False).relative_to(workspace)
        except (OSError, RuntimeError, ValueError):
            return False
        return (
            len(relative.parts) >= 2
            and relative.parts[0].casefold() == "reproduction"
            and relative.suffix.casefold() == ".py"
        )

    def _new_event(self, event_type: AgentEventType, payload: dict) -> AgentEvent:
        return AgentEvent(
            session_id=self.session_store.session.id,
            type=event_type,
            payload=redact_obj(payload),
        )

    def _record(self, event: AgentEvent) -> AgentEvent:
        projected = project_agent_event(event, self.policy_context.workspace_root.resolve())
        safe = projected.model_copy(update={"payload": redact_obj(projected.payload)})
        self.session_store.add_event(safe)
        self.trace_store.append(safe)
        if self.event_sink is not None:
            self.event_sink(safe)
        return safe

    def _record_and_collect(
        self,
        event: AgentEvent,
        all_events: list[AgentEvent],
    ) -> AgentEvent:
        recorded = self._record(event)
        all_events.append(recorded)
        return recorded
