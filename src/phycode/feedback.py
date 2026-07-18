from __future__ import annotations

from typing import Any

from phycode.models import AgentProfile, FeedbackKind, FeedbackSignal, ToolResult
from phycode.redaction import redact_obj

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


def classify_feedback(
    result: ToolResult,
    *,
    profile: AgentProfile | None = None,
    policy_rule_id: str | None = None,
) -> list[FeedbackSignal]:
    kind = STATUS_TO_KIND.get(result.status, FeedbackKind.TOOL_ERROR)
    evidence = {"stdout": result.stdout[:1000], "stderr": result.stderr[:1000], "truncated": result.truncated}
    signals = [
        FeedbackSignal(
            kind=kind,
            summary=_summary_for(kind, result),
            evidence=evidence,
            retryable=(
                kind
                in {
                    FeedbackKind.COMMAND_FAILED,
                    FeedbackKind.TEST_FAILED,
                    FeedbackKind.TOOL_ERROR,
                }
                or (
                    kind == FeedbackKind.POLICY_BLOCKED
                    and profile == AgentProfile.PRBENCH
                    and policy_rule_id == "prbench.direct_csv_mutation_blocked"
                )
            ),
            suggested_next_step=_next_step_for(kind, profile, policy_rule_id),
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


def artifact_verification_feedback(issues: list[dict[str, Any]]) -> FeedbackSignal:
    evidence = redact_obj({"issues": issues})
    return FeedbackSignal(
        kind=FeedbackKind.ARTIFACT_VERIFICATION_FAILED,
        summary="Required artifacts are incomplete or unverifiable",
        evidence=evidence,
        retryable=True,
        suggested_next_step="Create, run, and inspect the missing reproduction artifacts",
    )


def _summary_for(kind: FeedbackKind, result: ToolResult) -> str:
    if kind == FeedbackKind.SUCCESS:
        return "Tool completed successfully"
    if result.stderr:
        return result.stderr.splitlines()[0]
    if result.stdout:
        return result.stdout.splitlines()[0]
    return kind.value


def _next_step_for(
    kind: FeedbackKind,
    profile: AgentProfile | None = None,
    policy_rule_id: str | None = None,
) -> str | None:
    if (
        kind == FeedbackKind.POLICY_BLOCKED
        and profile == AgentProfile.PRBENCH
        and policy_rule_id == "prbench.direct_csv_mutation_blocked"
    ):
        return (
            "Modify or rewrite the reproduction script to generate the CSV, "
            "then request process.run"
        )
    if kind == FeedbackKind.TEST_FAILED:
        return "Inspect the failing test and edit the related file"
    if kind == FeedbackKind.POLICY_REQUIRES_APPROVAL:
        return "Ask the user for approval"
    if kind == FeedbackKind.COMMAND_FAILED:
        return "Inspect stderr and choose a smaller command"
    return None
