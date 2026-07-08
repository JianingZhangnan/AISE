from pathlib import Path

from phycode.context import ContextBuilder, MemoryStore, SessionStore
from phycode.models import AgentEvent, AgentEventType, MemoryCategory, MemoryEntry, Session, SessionMode
from phycode.trace import TraceStore


def test_trace_store_redacts_before_write(tmp_path: Path):
    store = TraceStore(tmp_path)
    event = AgentEvent(session_id="s", type=AgentEventType.ERROR, payload={"message": "OPENAI_API_KEY=secret"})
    store.append(event)
    text = next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8")
    assert "secret" not in text
    assert "REDACTED" in text


def test_memory_store_redacts_before_write(tmp_path: Path):
    memory = MemoryStore(tmp_path / "memory.jsonl")
    memory.append(MemoryEntry(category=MemoryCategory.PROJECT_FACT, content="OPENAI_API_KEY=secret", source="user"))
    text = (tmp_path / "memory.jsonl").read_text(encoding="utf-8")
    assert "secret" not in text
    assert "REDACTED" in text


def test_context_redacts_current_input_before_llm_messages(tmp_path: Path):
    session = Session(workspace_root=str(tmp_path), mode=SessionMode.INTERACTIVE)
    messages = ContextBuilder(
        session_store=SessionStore(session),
        memory_store=MemoryStore(tmp_path / "memory.jsonl"),
        max_chars=4000,
    ).build("fix OPENAI_API_KEY=secret")
    rendered = str(messages)
    assert "secret" not in rendered
    assert "REDACTED" in rendered


def test_context_includes_recent_feedback_and_memory(tmp_path: Path):
    session = Session(workspace_root=str(tmp_path), mode=SessionMode.INTERACTIVE)
    session_store = SessionStore(session)
    memory = MemoryStore(tmp_path / "memory.jsonl")
    memory.append(MemoryEntry(category=MemoryCategory.TEST_COMMAND, content="Use uv run pytest", source="user"))
    session_store.add_event(
        AgentEvent(
            session_id=session.id,
            type=AgentEventType.FEEDBACK_SIGNAL,
            payload={"kind": "test_failed", "summary": "one test failed"},
        )
    )
    messages = ContextBuilder(session_store=session_store, memory_store=memory, max_chars=4000).build("fix it")
    rendered = str(messages)
    assert "Use uv run pytest" in rendered
    assert "one test failed" in rendered
    assert "fix it" in rendered
