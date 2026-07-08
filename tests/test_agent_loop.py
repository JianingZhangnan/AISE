from pathlib import Path

from phycode.agent import AgentLoop
from phycode.context import ContextBuilder, MemoryStore, SessionStore
from phycode.llm import ScriptedLLM
from phycode.models import AgentEventType, Session, SessionMode
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.file_tools import register_file_tools
from phycode.trace import TraceStore


def build_loop(tmp_path: Path, llm: ScriptedLLM) -> AgentLoop:
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
        max_steps=5,
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
