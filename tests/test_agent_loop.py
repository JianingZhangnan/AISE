from pathlib import Path

from phycode.agent import AgentLoop
from phycode.context import ContextBuilder, MemoryStore, SessionStore
from phycode.llm import FailingLLM, ReactiveLLM, ScriptedLLM
from phycode.models import AgentEvent, AgentEventType, Session, SessionMode, ToolSpec
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.file_tools import register_file_tools
from phycode.trace import TraceStore


def build_loop(tmp_path: Path, llm, approval_handler=None, max_steps: int = 5) -> AgentLoop:
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
        approval_handler=approval_handler,
    )


def test_agent_returns_final_text(tmp_path: Path):
    loop = build_loop(tmp_path, ScriptedLLM([[{"type": "assistant_final", "payload": {"text": "done"}}]]))
    result = loop.run("hello")
    assert result.final_text == "done"
    assert result.stopped_reason == "final"


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


AUTO_APPROVE = lambda call, decision: True  # noqa: E731


def _fix_bug_rules() -> list:
    corrective = [{"type": "tool_call_requested", "payload": {"tool_name": "file.edit", "args": {"path": "bug.py", "old": "value = 0", "new": "value = 1"}}}]
    failing = [{"type": "tool_call_requested", "payload": {"tool_name": "file.edit", "args": {"path": "bug.py", "old": "DOES_NOT_EXIST", "new": "x"}}}]
    done = [{"type": "assistant_final", "payload": {"text": "fixed"}}]
    return [
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
