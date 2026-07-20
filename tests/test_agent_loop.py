import json
from pathlib import Path

import pytest

from phycode.agent import AgentLoop
from phycode.context import ContextBuilder, MemoryStore, SessionStore
from phycode.llm import FailingLLM, ReactiveLLM, ScriptedLLM
from phycode.models import AgentEvent, AgentEventType, Session, SessionMode, ToolSpec
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.file_tools import register_file_tools
from phycode.trace import TraceStore


def build_loop(
    tmp_path: Path,
    llm,
    approval_handler=None,
    max_steps: int = 5,
    max_tool_calls: int = 40,
    max_discovery_tool_calls: int | None = None,
) -> AgentLoop:
    session = Session(workspace_root=str(tmp_path), mode=SessionMode.NON_INTERACTIVE)
    session_store = SessionStore(session)
    memory = MemoryStore(tmp_path / ".phycode" / "memory.jsonl")
    registry = ToolRegistry()
    register_file_tools(registry)
    return AgentLoop(
        llm=llm,
        context_builder=ContextBuilder(session_store, memory),
        tool_runtime=ToolRuntime(registry),
        policy_context=PolicyContext(tmp_path, [], interactive=False),
        trace_store=TraceStore(tmp_path / ".phycode" / "traces"),
        session_store=session_store,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        max_discovery_tool_calls=max_discovery_tool_calls,
        approval_handler=approval_handler,
    )


def test_agent_returns_final_text(tmp_path: Path):
    loop = build_loop(tmp_path, ScriptedLLM([[{"type": "assistant_final", "payload": {"text": "done"}}]]))
    result = loop.run("hello")
    assert result.final_text == "done"
    assert result.stopped_reason == "final"


def test_agent_result_uses_the_recorded_safe_final_event(tmp_path: Path) -> None:
    secret = "cafebabe"
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "assistant_final",
                    "payload": {
                        "text": f"password={secret}",
                        "nested": ({"api_key": "a1b2c3d4"},),
                    },
                }
            ]
        ]
    )

    result = build_loop(tmp_path, llm).run("finish safely")

    rendered = str([event.model_dump(mode="python") for event in result.events])
    assert secret not in str(result.final_text)
    assert secret not in rendered
    assert "a1b2c3d4" not in rendered
    assert "REDACTED_SECRET" in str(result.final_text) + rendered


def test_agent_routes_tool_call_and_then_final(tmp_path: Path):
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    llm = ScriptedLLM(
        [
            [{"type": "tool_call_requested", "payload": {"tool_name": "file.read", "args": {"path": "README.md"}}}],
            [{"type": "assistant_final", "payload": {"text": "read complete"}}],
        ]
    )
    result = build_loop(tmp_path, llm).run("read README")
    assert result.final_text == "read complete"
    assert any(event.type == AgentEventType.FEEDBACK_SIGNAL for event in result.events)


def test_agent_result_redacts_sensitive_keys_in_feedback_and_tool_args(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    result = build_loop(
        tmp_path,
        ScriptedLLM(
            [
                [
                    {
                        "type": "feedback_signal",
                        "payload": {"kind": "tool_error", "token": "aabbccdd"},
                    },
                    {
                        "type": "tool_call_requested",
                        "payload": {
                            "tool_name": "file.read",
                            "args": {"path": "README.md", "api_key": "1122aabb"},
                        },
                    },
                ],
                [{"type": "incomplete", "payload": {"reason": "done"}}],
            ]
        ),
    ).run("inspect")

    rendered = str([event.model_dump(mode="json") for event in result.events])
    assert "aabbccdd" not in rendered
    assert "1122aabb" not in rendered
    assert "REDACTED_SECRET" in rendered


def test_agent_preserves_original_user_input_after_tool_turn(tmp_path: Path):
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")

    class RecordingTwoTurnLLM:
        def __init__(self) -> None:
            self.messages = []

        def generate(self, messages, tools):
            del tools
            self.messages.append(messages)
            if len(self.messages) == 1:
                return [
                    AgentEvent(
                        session_id="rec",
                        type=AgentEventType.TOOL_CALL_REQUESTED,
                        payload={"tool_name": "file.read", "args": {"path": "README.md"}},
                    )
                ]
            return [AgentEvent(session_id="rec", type=AgentEventType.ASSISTANT_FINAL, payload={"text": "done"})]

    llm = RecordingTwoTurnLLM()
    result = build_loop(tmp_path, llm).run("original task")

    assert result.stopped_reason == "final"
    assert len(llm.messages) == 2
    assert "User: original task" in str(llm.messages[1])


def test_reused_loop_orders_new_user_turn_after_previous_assistant_history(tmp_path: Path) -> None:
    class RecordingFinalLLM:
        def __init__(self) -> None:
            self.messages: list[list[dict[str, object]]] = []

        def generate(self, messages, tools):
            del tools
            self.messages.append(messages)
            turn = len(self.messages)
            return [
                AgentEvent(
                    session_id="recording",
                    type=AgentEventType.ASSISTANT_FINAL,
                    payload={"text": f"answer-{turn}"},
                )
            ]

    llm = RecordingFinalLLM()
    loop = build_loop(tmp_path, llm)

    loop.run("first turn")
    loop.run("second turn")

    second_messages = llm.messages[1]
    first_user_index = next(
        index
        for index, message in enumerate(second_messages)
        if message["role"] == "user" and "first turn" in str(message.get("content", ""))
    )
    previous_assistant_index = next(
        index
        for index, message in enumerate(second_messages)
        if message["role"] == "assistant" and message.get("content") == "answer-1"
    )
    current_user_index = next(
        index
        for index, message in enumerate(second_messages)
        if message["role"] == "user" and "second turn" in str(message.get("content", ""))
    )
    assert first_user_index < previous_assistant_index < current_user_index
    assert [event.type.value for event in loop.session_store.events].count("user_message") == 2
    trace_events = loop.trace_store.read_events_raw(loop.session_store.session.id)
    assert [event["type"] for event in trace_events].count("user_message") == 2


AUTO_APPROVE = lambda call, decision: True  # noqa: E731


def _fix_bug_rules() -> list:
    corrective = [{"type": "tool_call_requested", "payload": {"tool_name": "file.edit", "args": {"path": "bug.py", "old": "value = 0", "new": "value = 1"}}}]
    failing = [{"type": "tool_call_requested", "payload": {"tool_name": "file.edit", "args": {"path": "bug.py", "old": "DOES_NOT_EXIST", "new": "x"}}}]
    done = [{"type": "assistant_final", "payload": {"text": "fixed"}}]
    return [
        ('"kind": "success"', done),
        ('"kind": "tool_error"', corrective),
        ("[feedback] success", done),
        ("[feedback] tool_error", corrective),
        ("__default__", failing),
    ]


def _reactive(rules) -> ReactiveLLM:
    default = next(events for trigger, events in rules if trigger == "__default__")
    real_rules = [(t, e) for t, e in rules if t != "__default__"]
    return ReactiveLLM(real_rules, default=default)


def test_reactive_llm_output_depends_on_feedback():
    llm = _reactive(_fix_bug_rules())
    # Without any failure in context the model does not emit the corrective edit.
    baseline = llm.generate([{"role": "user", "content": "please fix"}], [])
    assert baseline[0].payload["args"]["old"] == "DOES_NOT_EXIST"
    # Once a tool_error feedback appears the model changes its next action.
    reacted = llm.generate([{"role": "user", "content": "recent activity: [feedback] tool_error: nope"}], [])
    assert reacted[0].payload["args"]["old"] == "value = 0"


def test_agent_loop_recovers_after_failure_feedback(tmp_path: Path):
    (tmp_path / "bug.py").write_text("value = 0\n", encoding="utf-8")
    loop = build_loop(tmp_path, _reactive(_fix_bug_rules()), approval_handler=AUTO_APPROVE, max_steps=6)
    result = loop.run("fix the bug")
    assert result.stopped_reason == "final"
    assert (tmp_path / "bug.py").read_text(encoding="utf-8") == "value = 1\n"
    kinds = [e.payload.get("kind") for e in result.events if e.type == AgentEventType.FEEDBACK_SIGNAL]
    # A failure was observed, and a success followed it (the action changed).
    assert kinds.index("tool_error") < kinds.index("success")


def test_agent_loop_stops_on_provider_error(tmp_path: Path):
    result = build_loop(tmp_path, FailingLLM("provider down")).run("hello")
    assert result.stopped_reason == "error"
    assert any(e.type == AgentEventType.ERROR for e in result.events)


def test_agent_loop_stops_on_repeated_failure(tmp_path: Path):
    (tmp_path / "bug.py").write_text("value = 0\n", encoding="utf-8")
    only_failing = [(("__never__"), [{"type": "assistant_final", "payload": {"text": "x"}}])]
    failing = [{"type": "tool_call_requested", "payload": {"tool_name": "file.edit", "args": {"path": "bug.py", "old": "NOPE", "new": "x"}}}]
    llm = ReactiveLLM([], default=failing)
    loop = build_loop(tmp_path, llm, approval_handler=AUTO_APPROVE, max_steps=20)
    result = loop.run("keep failing")
    assert result.stopped_reason == "repeated_failure"


def test_agent_loop_allows_distinct_failed_actions_to_recover(tmp_path: Path) -> None:
    (tmp_path / "bug.py").write_text("value = 0\n", encoding="utf-8")
    failed_edits = [
        [
            {
                "type": "tool_call_requested",
                "payload": {
                    "tool_name": "file.edit",
                    "args": {
                        "path": "bug.py",
                        "old": f"missing = {attempt}",
                        "new": "value = 1",
                    },
                },
            }
        ]
        for attempt in range(3)
    ]
    successful_edit = [
        {
            "type": "tool_call_requested",
            "payload": {
                "tool_name": "file.edit",
                "args": {"path": "bug.py", "old": "value = 0", "new": "value = 1"},
            },
        }
    ]
    final = [{"type": "assistant_final", "payload": {"text": "fixed"}}]
    loop = build_loop(
        tmp_path,
        ScriptedLLM([*failed_edits, successful_edit, final]),
        approval_handler=AUTO_APPROVE,
        max_steps=6,
    )

    result = loop.run("try distinct corrections")

    assert result.stopped_reason == "final"
    assert result.final_text == "fixed"
    assert (tmp_path / "bug.py").read_text(encoding="utf-8") == "value = 1\n"
    failure_kinds = [
        event.payload.get("kind")
        for event in result.events
        if event.type == AgentEventType.FEEDBACK_SIGNAL
        and event.payload.get("kind") == "tool_error"
    ]
    assert failure_kinds == ["tool_error", "tool_error", "tool_error"]


def test_agent_loop_finalizes_on_repeated_successful_action(tmp_path: Path):
    (tmp_path / "README.md").write_text("same", encoding="utf-8")
    repeated = [
        {"type": "tool_call_requested", "payload": {"tool_name": "file.read", "args": {"path": "README.md"}}}
    ]
    final = [{"type": "assistant_final", "payload": {"text": "same"}}]
    result = build_loop(tmp_path, ScriptedLLM([repeated, repeated, repeated, final]), max_steps=10).run("read")

    assert result.stopped_reason == "final"
    assert result.final_text == "same"
    requests = [event for event in result.events if event.type == AgentEventType.TOOL_CALL_REQUESTED]
    assert len(requests) == 3


def test_agent_loop_warns_then_finalizes_without_tools_at_budget(tmp_path: Path):
    (tmp_path / "README.md").write_text("abcdef", encoding="utf-8")
    turns = [
        [
            {
                "type": "tool_call_requested",
                "payload": {"tool_name": "file.read", "args": {"path": "README.md", "offset": offset}},
            }
        ]
        for offset in range(4)
    ]
    turns.append([{"type": "assistant_final", "payload": {"text": "best supported answer"}}])
    result = build_loop(tmp_path, ScriptedLLM(turns), max_steps=10, max_tool_calls=4).run("research")

    assert result.stopped_reason == "final"
    assert result.final_text == "best supported answer"
    requests = [event for event in result.events if event.type == AgentEventType.TOOL_CALL_REQUESTED]
    assert len(requests) == 4
    assert any(event.payload.get("kind") == "tool_budget_near_limit" for event in result.events)


def test_tool_budget_finalization_prompt_disables_more_tool_calls(tmp_path: Path):
    class RecordingBudgetLLM:
        def __init__(self) -> None:
            self.messages = []

        def generate(self, messages, tools):
            self.messages.append((messages, tools))
            if len(self.messages) == 1:
                return [
                    AgentEvent(
                        session_id="rec",
                        type=AgentEventType.TOOL_CALL_REQUESTED,
                        payload={"tool_name": "file.read", "args": {"path": "README.md"}},
                    )
                ]
            return [AgentEvent(session_id="rec", type=AgentEventType.ASSISTANT_FINAL, payload={"text": "answer"})]

    (tmp_path / "README.md").write_text("evidence", encoding="utf-8")
    llm = RecordingBudgetLLM()
    result = build_loop(tmp_path, llm, max_tool_calls=1).run("question")

    assert result.final_text == "answer"
    assert llm.messages[1][1] == []
    assert "Tool use is now disabled" in str(llm.messages[1][0])


def test_discovery_budget_denies_excess_read_but_allows_following_write(
    tmp_path: Path,
) -> None:
    for name in ("first.txt", "second.txt", "third.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    turns = [
        [
            {
                "type": "tool_call_requested",
                "payload": {
                    "tool_name": "file.read",
                    "args": {"path": name, "offset": offset},
                },
            }
        ]
        for offset, name in enumerate(("first.txt", "second.txt", "third.txt"))
    ]
    turns.extend(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": "result.txt", "content": "produced"},
                    },
                }
            ],
            [{"type": "assistant_final", "payload": {"text": "done"}}],
        ]
    )

    result = build_loop(
        tmp_path,
        ScriptedLLM(turns),
        approval_handler=AUTO_APPROVE,
        max_steps=6,
        max_tool_calls=5,
        max_discovery_tool_calls=2,
    ).run("discover briefly, then produce")

    assert result.stopped_reason == "final"
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "produced"
    outputs = [
        event.payload["status"]
        for event in result.events
        if event.type == AgentEventType.TOOL_CALL_OUTPUT
    ]
    assert outputs == ["ok", "ok", "policy_blocked", "ok"]
    denied = next(
        event
        for event in result.events
        if event.type == AgentEventType.POLICY_DECISION
        and event.payload.get("rule_id") == "runtime.discovery_budget"
    )
    assert denied.payload["decision"] == "deny"
    feedback = next(
        event
        for event in result.events
        if event.type == AgentEventType.FEEDBACK_SIGNAL
        and event.payload.get("cause")
        == "file.read:runtime.discovery_budget:policy_blocked"
    )
    assert "file.write" in str(feedback.payload["suggested_next_step"])
    assert "process.run" in str(feedback.payload["suggested_next_step"])


def test_default_discovery_budget_preserves_ordinary_agent_semantics(
    tmp_path: Path,
) -> None:
    for index in range(3):
        (tmp_path / f"part-{index}.txt").write_text(str(index), encoding="utf-8")
    turns = [
        [
            {
                "type": "tool_call_requested",
                "payload": {
                    "tool_name": "file.read",
                    "args": {"path": f"part-{index}.txt"},
                },
            }
        ]
        for index in range(3)
    ]
    turns.append([{"type": "assistant_final", "payload": {"text": "done"}}])

    result = build_loop(
        tmp_path,
        ScriptedLLM(turns),
        max_steps=5,
        max_tool_calls=4,
    ).run("ordinary reads")

    assert result.stopped_reason == "final"
    assert all(
        event.payload.get("rule_id") != "runtime.discovery_budget"
        for event in result.events
    )


@pytest.mark.parametrize(
    ("event_type", "payload", "expected_reason"),
    [
        (
            "assistant_final",
            {
                "text": "password=cafebabe",
                "nested": ({"api_key": "a1b2c3d4"},),
            },
            "final",
        ),
        (
            "incomplete",
            {
                "reason": "synthetic stop",
                "nested": ({"client_secret": "11223344"},),
            },
            "incomplete",
        ),
    ],
)
def test_evidence_finalization_returns_only_recorded_safe_events(
    tmp_path: Path,
    event_type: str,
    payload: dict[str, object],
    expected_reason: str,
) -> None:
    loop = build_loop(
        tmp_path,
        ScriptedLLM([[{"type": event_type, "payload": payload}]]),
        max_tool_calls=0,
    )

    result = loop.run("finish from evidence")

    result_text = str(result.final_text) + str(
        [event.model_dump(mode="python") for event in result.events]
    )
    persisted_text = str(
        [event.model_dump(mode="python") for event in loop.session_store.events]
    ) + json.dumps(loop.trace_store.read_events_raw(loop.session_store.session.id))
    assert result.stopped_reason == expected_reason
    for secret in ("cafebabe", "a1b2c3d4", "11223344"):
        assert secret not in result_text
        assert secret not in persisted_text
    assert "REDACTED_SECRET" in result_text


def test_agent_loop_stops_on_repeated_readonly_calls(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    # A model that keeps re-reading the same file, making no progress.
    spin = [{"type": "tool_call_requested", "payload": {"tool_name": "file.read", "args": {"path": "a.txt"}}}]
    loop = build_loop(tmp_path, ReactiveLLM([], default=spin), max_steps=50)
    result = loop.run("spin forever")
    assert result.stopped_reason == "repeated_calls"


def test_repeated_mutating_calls_do_not_trip_call_guard(tmp_path: Path):
    # Repeated file.write with the SAME args is real progress each time -> never trips.
    write = [{"type": "tool_call_requested", "payload": {"tool_name": "file.write", "args": {"path": "a.txt", "content": "x"}}}]
    turns = [write for _ in range(7)] + [[{"type": "assistant_final", "payload": {"text": "done"}}]]
    result = build_loop(tmp_path, ScriptedLLM(turns), approval_handler=AUTO_APPROVE, max_steps=30).run("go")
    assert result.stopped_reason == "final"


def test_agent_loop_passes_tool_specs_to_llm(tmp_path: Path):
    class RecordingLLM:
        def __init__(self) -> None:
            self.tools_seen: list[ToolSpec] | None = None

        def generate(self, messages, tools):
            self.tools_seen = tools
            return [AgentEvent(session_id="rec", type=AgentEventType.ASSISTANT_FINAL, payload={"text": "ok"})]

    rec = RecordingLLM()
    build_loop(tmp_path, rec).run("hi")
    assert rec.tools_seen is not None
    assert {spec.name for spec in rec.tools_seen} >= {"file.read", "file.edit"}


def test_ask_tool_blocked_without_approval_handler(tmp_path: Path):
    llm = ScriptedLLM(
        [
            [{"type": "tool_call_requested", "payload": {"tool_name": "file.write", "args": {"path": "new.txt", "content": "hi"}}}],
            [{"type": "assistant_final", "payload": {"text": "done"}}],
        ]
    )
    result = build_loop(tmp_path, llm).run("write it")
    assert not (tmp_path / "new.txt").exists()
    statuses = [e.payload.get("status") for e in result.events if e.type == AgentEventType.TOOL_CALL_OUTPUT]
    assert "policy_requires_approval" in statuses


def test_ask_tool_executes_with_approval_handler(tmp_path: Path):
    llm = ScriptedLLM(
        [
            [{"type": "tool_call_requested", "payload": {"tool_name": "file.write", "args": {"path": "new.txt", "content": "hi"}}}],
            [{"type": "assistant_final", "payload": {"text": "done"}}],
        ]
    )
    result = build_loop(tmp_path, llm, approval_handler=AUTO_APPROVE).run("write it")
    assert result.stopped_reason == "final"
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "hi"
