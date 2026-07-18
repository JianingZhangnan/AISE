from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from phycode.agent import AgentLoop
from phycode.context import ContextBuilder, MemoryStore, SessionStore
from phycode.execution import ExecutionJournal
from phycode.llm import LLMClient, ReactiveLLM, ScriptedLLM
from phycode.models import (
    AgentEventType,
    AgentProfile,
    Session,
    SessionMode,
    ToolResult,
    ToolRiskLevel,
    ToolSpec,
)
from phycode.policy import PolicyContext
from phycode.prbench_contract import (
    ArtifactVerifier,
    TaskContract,
    VerificationIssue,
    VerificationResult,
)
from phycode.profiles import profile_spec
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.file_tools import register_file_tools
from phycode.trace import TraceStore


AUTO_APPROVE = lambda call, decision: True  # noqa: E731


def _artifact_fingerprint(workspace: Path, paths: tuple[str, ...]) -> str:
    snapshot: list[dict[str, object]] = []
    for relative_path in paths:
        path = workspace / relative_path
        snapshot.append(
            {
                "path": relative_path,
                "exists": path.is_file(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
            }
        )
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode("utf-8")).hexdigest()


def _build_loop(
    tmp_path: Path,
    llm: LLMClient,
    *,
    expected_files: tuple[str, ...],
    completion_verifier: Callable[[], VerificationResult] | None = None,
    progress_fingerprint: Callable[[], str] | None = None,
    configure_registry: Callable[[ToolRegistry], None] | None = None,
    max_steps: int = 12,
    max_tool_calls: int = 10,
    use_completion_verifier: bool = True,
) -> AgentLoop:
    session = Session(workspace_root=str(tmp_path), mode=SessionMode.NON_INTERACTIVE)
    session_store = SessionStore(session)
    registry = ToolRegistry()
    register_file_tools(registry)
    if configure_registry is not None:
        configure_registry(registry)
    if use_completion_verifier and completion_verifier is None:
        contract = TaskContract(
            instruction_file="instruction.md",
            paper_file="paper.md",
            expected_files=expected_files,
        )
        journal = ExecutionJournal(tmp_path, expected_files)
        completion_verifier = ArtifactVerifier(tmp_path, contract, journal).verify
    return AgentLoop(
        llm=llm,
        context_builder=ContextBuilder(
            session_store,
            MemoryStore(tmp_path / ".phycode" / "memory.jsonl"),
        ),
        tool_runtime=ToolRuntime(registry),
        policy_context=PolicyContext(
            tmp_path,
            [],
            interactive=False,
            profile_spec=profile_spec(AgentProfile.PRBENCH),
        ),
        trace_store=TraceStore(tmp_path / ".phycode" / "traces"),
        session_store=session_store,
        approval_handler=AUTO_APPROVE,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        completion_verifier=completion_verifier,
        progress_fingerprint=progress_fingerprint,
    )


def test_final_with_missing_artifact_feeds_back_and_continues(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            [{"type": "assistant_final", "payload": {"text": "done"}}],
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": "result.txt", "content": "ok"},
                    },
                }
            ],
            [{"type": "assistant_final", "payload": {"text": "verified"}}],
        ]
    )
    result = _build_loop(tmp_path, llm, expected_files=("result.txt",)).run("create result")

    assert result.stopped_reason == "completed"
    assert result.final_text == "verified"
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "ok"
    assert any(
        event.type == AgentEventType.FEEDBACK_SIGNAL
        and event.payload.get("kind") == "artifact_verification_failed"
        for event in result.events
    )


def test_interleaved_same_status_calls_do_not_trigger_repeat_stop(tmp_path: Path) -> None:
    (tmp_path / "status.txt").write_text("ready", encoding="utf-8")
    status = [
        {
            "type": "tool_call_requested",
            "payload": {"tool_name": "file.read", "args": {"path": "status.txt"}},
        }
    ]
    llm = ScriptedLLM(
        [
            status,
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": "first.txt", "content": "first"},
                    },
                }
            ],
            status,
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": "second.txt", "content": "second"},
                    },
                }
            ],
            status,
            [{"type": "assistant_final", "payload": {"text": "both created"}}],
        ]
    )
    tracked = ("first.txt", "second.txt")
    result = _build_loop(
        tmp_path,
        llm,
        expected_files=tracked,
        progress_fingerprint=lambda: _artifact_fingerprint(tmp_path, tracked),
    ).run("create two files")

    assert result.stopped_reason == "completed"
    assert (tmp_path / "first.txt").read_text(encoding="utf-8") == "first"
    assert (tmp_path / "second.txt").read_text(encoding="utf-8") == "second"


def test_three_consecutive_identical_no_progress_actions_stop_as_failure(tmp_path: Path) -> None:
    (tmp_path / "status.txt").write_text("unchanged", encoding="utf-8")
    repeated_status = [
        {
            "type": "tool_call_requested",
            "payload": {"tool_name": "file.read", "args": {"path": "status.txt"}},
        }
    ]
    llm = ScriptedLLM(
        [
            repeated_status,
            repeated_status,
            repeated_status,
            [{"type": "assistant_final", "payload": {"text": "should not be accepted"}}],
        ]
    )
    tracked = ("result.txt",)
    result = _build_loop(
        tmp_path,
        llm,
        expected_files=tracked,
        progress_fingerprint=lambda: _artifact_fingerprint(tmp_path, tracked),
    ).run("inspect workspace")

    assert result.stopped_reason == "repeated_no_progress"
    assert result.final_text is None
    assert len(
        [event for event in result.events if event.type == AgentEventType.TOOL_CALL_REQUESTED]
    ) == 3


def test_repeated_success_with_verifier_does_not_use_unverified_legacy_final(
    tmp_path: Path,
) -> None:
    (tmp_path / "status.txt").write_text("unchanged", encoding="utf-8")
    repeated_status = [
        {
            "type": "tool_call_requested",
            "payload": {"tool_name": "file.read", "args": {"path": "status.txt"}},
        }
    ]
    llm = ScriptedLLM(
        [
            repeated_status,
            repeated_status,
            repeated_status,
            [{"type": "assistant_final", "payload": {"text": "unverified legacy final"}}],
        ]
    )
    result = _build_loop(tmp_path, llm, expected_files=("result.txt",)).run("inspect workspace")

    assert result.stopped_reason == "repeated_no_progress"
    assert result.final_text is None
    assert not any(event.type == AgentEventType.ASSISTANT_FINAL for event in result.events)


def test_failed_final_skips_remaining_events_in_the_same_provider_batch(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            [
                {"type": "assistant_final", "payload": {"text": "not ready"}},
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": "ignored.txt", "content": "must not run"},
                    },
                },
            ],
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": "result.txt", "content": "ready"},
                    },
                }
            ],
            [{"type": "assistant_final", "payload": {"text": "verified"}}],
        ]
    )
    result = _build_loop(tmp_path, llm, expected_files=("result.txt",)).run("create result")

    assert result.stopped_reason == "completed"
    assert not (tmp_path / "ignored.txt").exists()


def test_non_successful_action_breaks_consecutive_successful_action_sequence(tmp_path: Path) -> None:
    (tmp_path / "status.txt").write_text("stable", encoding="utf-8")
    (tmp_path / "result.txt").write_text("already complete", encoding="utf-8")
    status = [
        {
            "type": "tool_call_requested",
            "payload": {"tool_name": "file.read", "args": {"path": "status.txt"}},
        }
    ]
    blocked = [
        {
            "type": "tool_call_requested",
            "payload": {
                "tool_name": "file.write",
                "args": {"path": "../outside.txt", "content": "denied"},
            },
        }
    ]
    llm = ScriptedLLM(
        [
            status,
            status,
            blocked,
            status,
            status,
            [{"type": "assistant_final", "payload": {"text": "verified"}}],
        ]
    )
    tracked = ("result.txt",)
    result = _build_loop(
        tmp_path,
        llm,
        expected_files=tracked,
        progress_fingerprint=lambda: _artifact_fingerprint(tmp_path, tracked),
    ).run("inspect safely")

    assert result.stopped_reason == "completed"
    assert not (tmp_path.parent / "outside.txt").exists()


def test_same_action_and_result_with_artifact_progress_does_not_stop(tmp_path: Path) -> None:
    counter = 0

    def configure_registry(registry: ToolRegistry) -> None:
        def advance(call):
            nonlocal counter
            counter += 1
            (tmp_path / "result.txt").write_text(str(counter), encoding="utf-8")
            return ToolResult(tool_call_id=call.id, status="ok", stdout="advanced")

        registry.register(
            ToolSpec(
                name="workspace.status",
                description="Advance a deterministic test artifact",
                input_schema={"type": "object", "properties": {}},
                risk_level=ToolRiskLevel.SAFE,
            ),
            advance,
        )

    advance = [
        {
            "type": "tool_call_requested",
            "payload": {"tool_name": "workspace.status", "args": {}},
        }
    ]
    llm = ScriptedLLM(
        [
            advance,
            advance,
            advance,
            [{"type": "assistant_final", "payload": {"text": "progress verified"}}],
        ]
    )
    tracked = ("result.txt",)
    result = _build_loop(
        tmp_path,
        llm,
        expected_files=tracked,
        configure_registry=configure_registry,
        progress_fingerprint=lambda: _artifact_fingerprint(tmp_path, tracked),
    ).run("make progress")

    assert result.stopped_reason == "completed"
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "3"


def test_same_action_with_different_structured_result_does_not_count_as_repeat(tmp_path: Path) -> None:
    (tmp_path / "result.txt").write_text("complete", encoding="utf-8")
    invocation = 0

    def configure_registry(registry: ToolRegistry) -> None:
        def status(call):
            nonlocal invocation
            invocation += 1
            return ToolResult(
                tool_call_id=call.id,
                status="ok",
                stdout="stable",
                artifact_refs=[f"observation-{invocation}"],
            )

        registry.register(
            ToolSpec(
                name="workspace.status",
                description="Return a changing structured observation",
                input_schema={"type": "object", "properties": {}},
                risk_level=ToolRiskLevel.SAFE,
            ),
            status,
        )

    status = [
        {
            "type": "tool_call_requested",
            "payload": {"tool_name": "workspace.status", "args": {}},
        }
    ]
    llm = ScriptedLLM(
        [
            status,
            status,
            status,
            [{"type": "assistant_final", "payload": {"text": "verified"}}],
        ]
    )
    tracked = ("result.txt",)
    result = _build_loop(
        tmp_path,
        llm,
        expected_files=tracked,
        configure_registry=configure_registry,
        progress_fingerprint=lambda: _artifact_fingerprint(tmp_path, tracked),
    ).run("inspect observations")

    assert result.stopped_reason == "completed"


def test_failed_verification_at_max_steps_returns_artifact_failure_and_redacts_evidence(
    tmp_path: Path,
) -> None:
    secret_key = "sk-verifier-secret-123456789"
    secret_env = "OPENAI_API_KEY=abc123SECRET"
    failed = VerificationResult(
        ok=False,
        issues=(
            VerificationIssue(
                code="missing_artifact",
                path=secret_key,
                message=secret_env,
            ),
        ),
    )
    llm = ScriptedLLM([[{"type": "assistant_final", "payload": {"text": "unsafe success"}}]])
    loop = _build_loop(
        tmp_path,
        llm,
        expected_files=("result.txt",),
        completion_verifier=lambda: failed,
        max_steps=1,
    )
    result = loop.run("finish")

    assert result.stopped_reason == "artifact_verification_failed"
    assert result.final_text is None
    feedback = next(
        event
        for event in result.events
        if event.type == AgentEventType.FEEDBACK_SIGNAL
        and event.payload.get("kind") == "artifact_verification_failed"
    )
    issues = feedback.payload["evidence"]["issues"]
    assert isinstance(issues, list)
    assert issues[0]["code"] == "missing_artifact"
    assert len(
        [
            event
            for event in result.events
            if event.type == AgentEventType.FEEDBACK_SIGNAL
            and event.payload.get("kind") == "artifact_verification_failed"
        ]
    ) == 1
    serialized_events = json.dumps([event.model_dump(mode="json") for event in result.events], default=str)
    serialized_trace = json.dumps(loop.trace_store.read_events_raw(loop.session_store.session.id))
    assert secret_key not in serialized_events + serialized_trace
    assert secret_env not in serialized_events + serialized_trace


def test_verifier_exception_fails_closed_without_leaking_exception(tmp_path: Path) -> None:
    secret = "sk-verifier-crash-123456789"

    def broken_verifier() -> VerificationResult:
        raise RuntimeError(secret)

    llm = ScriptedLLM([[{"type": "assistant_final", "payload": {"text": "unsafe success"}}]])
    result = _build_loop(
        tmp_path,
        llm,
        expected_files=("result.txt",),
        completion_verifier=broken_verifier,
    ).run("finish")

    assert result.stopped_reason == "artifact_verification_failed"
    assert result.final_text is None
    serialized = json.dumps([event.model_dump(mode="json") for event in result.events], default=str)
    assert secret not in serialized
    assert "verifier_error" in serialized


def test_progress_fingerprint_exception_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "status.txt").write_text("ready", encoding="utf-8")

    def broken_fingerprint() -> str:
        raise RuntimeError("sk-progress-crash-123456789")

    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {"tool_name": "file.read", "args": {"path": "status.txt"}},
                }
            ],
            [{"type": "assistant_final", "payload": {"text": "must not complete"}}],
        ]
    )
    result = _build_loop(
        tmp_path,
        llm,
        expected_files=("result.txt",),
        progress_fingerprint=broken_fingerprint,
    ).run("inspect")

    assert result.stopped_reason == "error"
    assert result.final_text is None
    error = next(event for event in result.events if event.type == AgentEventType.ERROR)
    assert error.payload == {"message": "Progress fingerprint failed"}


def test_tool_budget_with_failed_verifier_does_not_request_successful_final(tmp_path: Path) -> None:
    (tmp_path / "status.txt").write_text("ready", encoding="utf-8")
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {"tool_name": "file.read", "args": {"path": "status.txt"}},
                }
            ],
            [{"type": "assistant_final", "payload": {"text": "must remain unused"}}],
        ]
    )
    result = _build_loop(
        tmp_path,
        llm,
        expected_files=("result.txt",),
        max_tool_calls=1,
    ).run("finish")

    assert result.stopped_reason == "artifact_verification_failed"
    assert result.final_text is None
    assert not any(event.type == AgentEventType.ASSISTANT_FINAL for event in result.events)


def test_tool_budget_with_successful_verifier_completes_without_synthetic_final(tmp_path: Path) -> None:
    (tmp_path / "result.txt").write_text("ready", encoding="utf-8")
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {"tool_name": "file.read", "args": {"path": "result.txt"}},
                }
            ],
            [{"type": "assistant_final", "payload": {"text": "must remain unused"}}],
        ]
    )
    result = _build_loop(
        tmp_path,
        llm,
        expected_files=("result.txt",),
        max_tool_calls=1,
    ).run("finish")

    assert result.stopped_reason == "completed"
    assert not any(event.type == AgentEventType.ASSISTANT_FINAL for event in result.events)


@pytest.mark.parametrize(
    ("artifact_exists", "write_path", "expected_reason"),
    [
        (True, "unexpected.txt", "completed"),
        (False, "result.txt", "artifact_verification_failed"),
    ],
)
def test_zero_tool_budget_with_verifier_finishes_before_executing_tool(
    tmp_path: Path,
    artifact_exists: bool,
    write_path: str,
    expected_reason: str,
) -> None:
    if artifact_exists:
        (tmp_path / "result.txt").write_text("already complete", encoding="utf-8")
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": write_path, "content": "must not execute"},
                    },
                }
            ]
        ]
    )
    result = _build_loop(
        tmp_path,
        llm,
        expected_files=("result.txt",),
        max_tool_calls=0,
    ).run("finish without tools")

    assert result.stopped_reason == expected_reason
    assert not (tmp_path / write_path).exists()
    assert not any(event.type == AgentEventType.TOOL_CALL_REQUESTED for event in result.events)
    assert not any(event.type == AgentEventType.TOOL_CALL_OUTPUT for event in result.events)


def test_zero_tool_budget_without_verifier_uses_no_tool_evidence_finalization(
    tmp_path: Path,
) -> None:
    llm = ReactiveLLM(
        [
            (
                "Tool use is now disabled",
                [{"type": "assistant_final", "payload": {"text": "evidence only"}}],
            )
        ],
        default=[
            {
                "type": "tool_call_requested",
                "payload": {
                    "tool_name": "file.write",
                    "args": {"path": "unexpected.txt", "content": "must not execute"},
                },
            }
        ],
    )
    result = _build_loop(
        tmp_path,
        llm,
        expected_files=(),
        max_tool_calls=0,
        use_completion_verifier=False,
    ).run("answer without tools")

    assert result.stopped_reason == "final"
    assert result.final_text == "evidence only"
    assert not (tmp_path / "unexpected.txt").exists()
    assert not any(event.type == AgentEventType.TOOL_CALL_REQUESTED for event in result.events)
    assert not any(event.type == AgentEventType.TOOL_CALL_OUTPUT for event in result.events)


def test_repeated_policy_block_returns_structured_terminal_blocker(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-policy.txt"
    outside.write_text("outside", encoding="utf-8")
    denied = [
        {
            "type": "tool_call_requested",
            "payload": {"tool_name": "file.read", "args": {"path": str(outside)}},
        }
    ]

    result = _build_loop(
        tmp_path,
        ScriptedLLM([denied, denied, denied]),
        expected_files=("result.txt",),
        max_steps=3,
        max_tool_calls=8,
    ).run("read safely")

    assert result.stopped_reason == "repeated_failure"
    assert result.terminal_blocker == "policy_blocked"
