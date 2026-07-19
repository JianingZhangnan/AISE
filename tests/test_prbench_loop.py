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
    AgentEvent,
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
    max_repeated_actions: int = 3,
    use_completion_verifier: bool = True,
    verify_after_successful_tool: bool = False,
    approval_handler: Callable[..., bool] | None = AUTO_APPROVE,
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
        approval_handler=approval_handler,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        max_repeated_actions=max_repeated_actions,
        completion_verifier=completion_verifier,
        progress_fingerprint=progress_fingerprint,
        verify_after_successful_tool=verify_after_successful_tool,
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


def test_successful_tool_auto_completes_only_when_opted_in_verifier_passes(
    tmp_path: Path,
) -> None:
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": "result.txt", "content": "complete"},
                    },
                }
            ],
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": "must-not-run.txt", "content": "late"},
                    },
                }
            ],
        ]
    )

    result = _build_loop(
        tmp_path,
        llm,
        expected_files=("result.txt",),
        verify_after_successful_tool=True,
    ).run("create result")

    assert result.stopped_reason == "completed"
    assert result.final_text is None
    assert llm.index == 1
    assert not (tmp_path / "must-not-run.txt").exists()


def test_successful_tool_failed_verification_is_recorded_and_visible_next_turn(
    tmp_path: Path,
) -> None:
    failed = VerificationResult(
        ok=False,
        issues=(
            VerificationIssue(
                code="missing_artifact",
                path="data/output.csv",
                message="Run the reproduction script to create the CSV",
            ),
        ),
    )

    class RecordingLLM:
        def __init__(self) -> None:
            self.messages: list[list[dict[str, object]]] = []

        def generate(self, messages, tools):
            del tools
            self.messages.append(messages)
            if len(self.messages) == 1:
                return [
                    AgentEvent(
                        session_id="provider",
                        type=AgentEventType.TOOL_CALL_REQUESTED,
                        payload={
                            "provider_call_id": "call_script",
                            "tool_name": "file.write",
                            "args": {"path": "reproduction.py", "content": "print('ready')\n"},
                        },
                    )
                ]
            return [
                AgentEvent(
                    session_id="provider",
                    type=AgentEventType.INCOMPLETE,
                    payload={"reason": "test observed feedback"},
                )
            ]

    llm = RecordingLLM()
    result = _build_loop(
        tmp_path,
        llm,
        expected_files=("data/output.csv",),
        completion_verifier=lambda: failed,
        verify_after_successful_tool=True,
    ).run("reproduce")

    assert result.stopped_reason == "incomplete"
    feedback = [
        event
        for event in result.events
        if event.type == AgentEventType.FEEDBACK_SIGNAL
        and event.payload.get("kind") == "artifact_verification_failed"
    ]
    assert len(feedback) == 1
    assert "missing_artifact" in str(llm.messages[1])
    assert "Run the reproduction script" in str(llm.messages[1])


def test_mutation_establishes_feedback_barrier_for_remaining_provider_batch(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "provider_call_id": "call_first",
                        "tool_name": "file.write",
                        "args": {"path": "first.txt", "content": "first"},
                    },
                },
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "provider_call_id": "call_stale",
                        "tool_name": "file.write",
                        "args": {"path": "stale.txt", "content": "stale"},
                    },
                },
            ],
            [{"type": "incomplete", "payload": {"reason": "barrier observed"}}],
        ]
    )

    loop = _build_loop(
        tmp_path,
        llm,
        expected_files=("not-complete.txt",),
        verify_after_successful_tool=False,
    )
    result = loop.run("write serially")

    assert result.stopped_reason == "incomplete"
    assert (tmp_path / "first.txt").read_text(encoding="utf-8") == "first"
    assert not (tmp_path / "stale.txt").exists()
    outputs = [event for event in result.events if event.type == AgentEventType.TOOL_CALL_OUTPUT]
    assert len(outputs) == 2
    assert outputs[1].payload["status"] == "stale_tool_batch"
    tool_messages = [
        message
        for message in loop.context_builder.build("continue from feedback")
        if message["role"] == "tool"
    ]
    assert [message["tool_call_id"] for message in tool_messages] == [
        "call_first",
        "call_stale",
    ]


def test_denied_call_establishes_feedback_barrier_for_remaining_provider_batch(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside-denied.txt"
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "provider_call_id": "call_denied",
                        "tool_name": "file.write",
                        "args": {"path": str(outside), "content": "denied"},
                    },
                },
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "provider_call_id": "call_after_deny",
                        "tool_name": "file.write",
                        "args": {"path": "must-not-run.txt", "content": "stale"},
                    },
                },
            ],
            [{"type": "incomplete", "payload": {"reason": "barrier observed"}}],
        ]
    )

    result = _build_loop(
        tmp_path,
        llm,
        expected_files=("not-complete.txt",),
    ).run("respect denial")

    assert result.stopped_reason == "incomplete"
    assert not outside.exists()
    assert not (tmp_path / "must-not-run.txt").exists()
    outputs = [event for event in result.events if event.type == AgentEventType.TOOL_CALL_OUTPUT]
    assert [event.payload["status"] for event in outputs] == [
        "policy_blocked",
        "stale_tool_batch",
    ]


def test_skipped_stale_calls_do_not_trigger_repeated_failure_before_feedback_turn(
    tmp_path: Path,
) -> None:
    batch = [
        {
            "type": "tool_call_requested",
            "payload": {
                "provider_call_id": "call_mutation",
                "tool_name": "file.write",
                "args": {"path": "first.txt", "content": "first"},
            },
        }
    ]
    batch.extend(
        {
            "type": "tool_call_requested",
            "payload": {
                "provider_call_id": f"call_stale_{index}",
                "tool_name": "file.read",
                "args": {"path": "first.txt"},
            },
        }
        for index in range(3)
    )
    llm = ScriptedLLM(
        [
            batch,
            [{"type": "incomplete", "payload": {"reason": "feedback turn reached"}}],
        ]
    )

    result = _build_loop(
        tmp_path,
        llm,
        expected_files=("not-complete.txt",),
    ).run("serialize stale calls")

    assert result.stopped_reason == "incomplete"
    assert llm.index == 2
    outputs = [event for event in result.events if event.type == AgentEventType.TOOL_CALL_OUTPUT]
    assert [event.payload["status"] for event in outputs] == [
        "ok",
        "stale_tool_batch",
        "stale_tool_batch",
        "stale_tool_batch",
    ]


def test_verifier_backed_budget_warning_requires_only_missing_artifact_work(
    tmp_path: Path,
) -> None:
    (tmp_path / "status.txt").write_text("ready", encoding="utf-8")
    failed = VerificationResult(
        ok=False,
        issues=(
            VerificationIssue(
                code="missing_artifact",
                path="result.txt",
                message="result is missing",
            ),
        ),
    )
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "provider_call_id": "call_status",
                        "tool_name": "file.read",
                        "args": {"path": "status.txt"},
                    },
                }
            ],
            [{"type": "incomplete", "payload": {"reason": "warning observed"}}],
        ]
    )

    result = _build_loop(
        tmp_path,
        llm,
        expected_files=("result.txt",),
        completion_verifier=lambda: failed,
        verify_after_successful_tool=True,
        max_tool_calls=3,
    ).run("finish")

    warning = next(
        event
        for event in result.events
        if event.type == AgentEventType.FEEDBACK_SIGNAL
        and event.payload.get("kind") == "tool_budget_near_limit"
    )
    rendered = str(warning.payload)
    assert "final answer" not in rendered.casefold()
    assert "missing" in rendered.casefold()
    assert "run" in rendered.casefold()
    assert "verify" in rendered.casefold()


def test_failed_tool_does_not_auto_complete_from_preexisting_artifact(tmp_path: Path) -> None:
    (tmp_path / "result.txt").write_text("preexisting", encoding="utf-8")
    outside = tmp_path.parent / "must-not-write.txt"
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": str(outside), "content": "denied"},
                    },
                }
            ],
            [{"type": "incomplete", "payload": {"reason": "stop test"}}],
        ]
    )

    result = _build_loop(
        tmp_path,
        llm,
        expected_files=("result.txt",),
        verify_after_successful_tool=True,
    ).run("do not trust a failed action")

    assert result.stopped_reason == "incomplete"
    assert llm.index == 2
    assert not outside.exists()


def test_explicit_final_remains_compatible_with_tool_auto_verification(tmp_path: Path) -> None:
    (tmp_path / "result.txt").write_text("complete", encoding="utf-8")
    llm = ScriptedLLM([[{"type": "assistant_final", "payload": {"text": "done"}}]])

    result = _build_loop(
        tmp_path,
        llm,
        expected_files=("result.txt",),
        verify_after_successful_tool=True,
    ).run("finish")

    assert result.stopped_reason == "completed"
    assert result.final_text == "done"


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


def test_interleaved_success_cycle_stops_within_unchanged_progress_epoch(
    tmp_path: Path,
) -> None:
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    def read(name: str) -> list[dict[str, object]]:
        return [
            {
                "type": "tool_call_requested",
                "payload": {"tool_name": "file.read", "args": {"path": name}},
            }
        ]

    llm = ScriptedLLM(
        [
            read("a.txt"),
            read("b.txt"),
            read("c.txt"),
            read("a.txt"),
            read("b.txt"),
            read("c.txt"),
            [{"type": "assistant_final", "payload": {"text": "must not run"}}],
        ]
    )
    tracked = ("result.txt",)

    result = _build_loop(
        tmp_path,
        llm,
        expected_files=tracked,
        progress_fingerprint=lambda: _artifact_fingerprint(tmp_path, tracked),
        max_tool_calls=6,
    ).run("inspect without looping")

    assert result.stopped_reason == "repeated_no_progress"
    assert result.terminal_blocker == "repeated_no_progress"
    assert len(
        [event for event in result.events if event.type == AgentEventType.TOOL_CALL_REQUESTED]
    ) == 6


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


def test_non_successful_action_does_not_reset_unchanged_progress_epoch(tmp_path: Path) -> None:
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

    assert result.stopped_reason == "repeated_no_progress"
    assert result.final_text is None
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


def test_read_only_success_does_not_clear_direct_csv_policy_blocker(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": "data/output.csv", "content": "value\n1\n"},
                    },
                }
            ],
            [
                {
                    "type": "tool_call_requested",
                    "payload": {"tool_name": "file.list", "args": {"path": "."}},
                }
            ],
        ]
    )

    result = _build_loop(
        tmp_path,
        llm,
        expected_files=("data/output.csv",),
        max_tool_calls=2,
    ).run("generate the CSV through a script")

    assert result.stopped_reason == "artifact_verification_failed"
    assert result.terminal_blocker == "policy_blocked"
    assert not (tmp_path / "data" / "output.csv").exists()


def test_successful_script_mutation_advances_past_direct_csv_policy_blocker(
    tmp_path: Path,
) -> None:
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": "data/output.csv", "content": "value\n1\n"},
                    },
                }
            ],
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {
                            "path": "reproduction/run.py",
                            "content": "print('generate data')\n",
                        },
                    },
                }
            ],
        ]
    )

    result = _build_loop(
        tmp_path,
        llm,
        expected_files=("data/output.csv",),
        max_tool_calls=2,
    ).run("generate the CSV through a script")

    assert result.stopped_reason == "artifact_verification_failed"
    assert result.terminal_blocker == "tool_budget_exhausted"
    assert (tmp_path / "reproduction" / "run.py").is_file()


def test_approval_required_survives_read_only_and_unrelated_mutation_success(
    tmp_path: Path,
) -> None:
    def configure_registry(registry: ToolRegistry) -> None:
        registry.register(
            ToolSpec(
                name="process.run",
                description="Test process execution",
                input_schema={"type": "object", "properties": {}},
                risk_level=ToolRiskLevel.RISKY,
                mutates_state=True,
            ),
            lambda call: ToolResult(tool_call_id=call.id, status="ok", stdout="ran"),
        )

    process = [
        {
            "type": "tool_call_requested",
            "payload": {"tool_name": "process.run", "args": {}},
        }
    ]
    llm = ScriptedLLM(
        [
            process,
            [
                {
                    "type": "tool_call_requested",
                    "payload": {"tool_name": "file.list", "args": {"path": "."}},
                }
            ],
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": "reproduction/run.py", "content": "print('ready')\n"},
                    },
                }
            ],
        ]
    )

    result = _build_loop(
        tmp_path,
        llm,
        expected_files=("data/output.csv",),
        configure_registry=configure_registry,
        approval_handler=lambda call, decision: call.tool_name == "file.write",
        max_tool_calls=3,
    ).run("request the approved process")

    assert result.stopped_reason == "artifact_verification_failed"
    assert result.terminal_blocker == "approval_required"
    assert (tmp_path / "reproduction" / "run.py").is_file()


def test_only_successful_process_run_clears_approval_required(tmp_path: Path) -> None:
    def configure_registry(registry: ToolRegistry) -> None:
        registry.register(
            ToolSpec(
                name="process.run",
                description="Test process execution",
                input_schema={"type": "object", "properties": {}},
                risk_level=ToolRiskLevel.RISKY,
                mutates_state=True,
            ),
            lambda call: ToolResult(tool_call_id=call.id, status="ok", stdout="ran"),
        )

    approvals = iter((False, True))
    process = [
        {
            "type": "tool_call_requested",
            "payload": {"tool_name": "process.run", "args": {}},
        }
    ]
    result = _build_loop(
        tmp_path,
        ScriptedLLM([process, process]),
        expected_files=("data/output.csv",),
        configure_registry=configure_registry,
        approval_handler=lambda call, decision: next(approvals),
        max_tool_calls=2,
    ).run("run after approval")

    assert result.stopped_reason == "artifact_verification_failed"
    assert result.terminal_blocker == "tool_budget_exhausted"


def test_fatal_verifier_overrides_tool_budget_blocker(tmp_path: Path) -> None:
    (tmp_path / "instruction.md").write_text("public", encoding="utf-8")
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.read",
                        "args": {"path": "instruction.md"},
                    },
                }
            ]
        ]
    )

    def fatal_verifier() -> VerificationResult:
        raise RuntimeError("verifier unavailable")

    result = _build_loop(
        tmp_path,
        llm,
        expected_files=("result.txt",),
        completion_verifier=fatal_verifier,
        max_tool_calls=1,
    ).run("inspect public input")

    assert result.stopped_reason == "artifact_verification_failed"
    assert result.terminal_blocker == "artifact_verification_failed"
