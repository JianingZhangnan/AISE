from __future__ import annotations

from phycode.models import FeedbackKind, FeedbackSignal, ToolResult

STATUS_TO_KIND = {
    "ok": FeedbackKind.SUCCESS,
    "command_failed": FeedbackKind.COMMAND_FAILED,
    "test_failed": FeedbackKind.TEST_FAILED,
    "policy_blocked": FeedbackKind.POLICY_BLOCKED,
    "policy_requires_approval": FeedbackKind.POLICY_REQUIRES_APPROVAL,
    "invalid_tool_args": FeedbackKind.INVALID_TOOL_ARGS,
    "tool_error": FeedbackKind.TOOL_ERROR,
    "timeout": FeedbackKind.TIMEOUT,
}


def classify_feedback(result: ToolResult) -> list[FeedbackSignal]:
    kind = STATUS_TO_KIND.get(result.status, FeedbackKind.TOOL_ERROR)
    evidence = {"stdout": result.stdout[:1000], "stderr": result.stderr[:1000], "truncated": result.truncated}
    signals = [
        FeedbackSignal(
            kind=kind,
            summary=_summary_for(kind, result),
            evidence=evidence,
            retryable=kind in {FeedbackKind.COMMAND_FAILED, FeedbackKind.TEST_FAILED, FeedbackKind.TOOL_ERROR},
            suggested_next_step=_next_step_for(kind),
        )
    ]
    if result.truncated:
        signals.append(
            FeedbackSignal(
                kind=FeedbackKind.OUTPUT_TRUNCATED,
                summary="Tool output was truncated",
                evidence=evidence,
            )
        )
    return signals


def _summary_for(kind: FeedbackKind, result: ToolResult) -> str:
    if kind == FeedbackKind.SUCCESS:
        return "Tool completed successfully"
    if result.stderr:
        return result.stderr.splitlines()[0]
    if result.stdout:
        return result.stdout.splitlines()[0]
    return kind.value


def _next_step_for(kind: FeedbackKind) -> str | None:
    if kind == FeedbackKind.TEST_FAILED:
        return "Inspect the failing test and edit the related file"
    if kind == FeedbackKind.POLICY_REQUIRES_APPROVAL:
        return "Ask the user for approval"
    if kind == FeedbackKind.COMMAND_FAILED:
        return "Inspect stderr and choose a smaller command"
    return None
