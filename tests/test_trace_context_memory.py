from pathlib import Path

from phycode.context import GAIA_SYSTEM_PROMPT, ContextBuilder, MemoryStore, SessionStore
from phycode.models import (
    AgentEvent,
    AgentEventType,
    MemoryCategory,
    MemoryEntry,
    Session,
    SessionMode,
)
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


def test_memory_store_secret_stays_valid_json(tmp_path: Path):
    # Redacting the serialized JSON line used to eat structural chars and produce
    # an unparseable line, crashing entries()/summary() on the next context build.
    memory = MemoryStore(tmp_path / "memory.jsonl")
    memory.append(
        MemoryEntry(
            category=MemoryCategory.PROJECT_FACT,
            content="OPENAI_API_KEY=sk-abcdef1234567890",
            source="user",
        )
    )
    entries = memory.entries()  # must not raise json.JSONDecodeError
    assert len(entries) == 1
    assert "sk-abcdef1234567890" not in memory.summary()


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


def test_context_preserves_question_and_both_ends_of_large_tool_output(tmp_path: Path):
    session = Session(workspace_root=str(tmp_path), mode=SessionMode.NON_INTERACTIVE)
    session_store = SessionStore(session)
    session_store.add_event(
        AgentEvent(
            session_id=session.id,
            type=AgentEventType.TOOL_CALL_OUTPUT,
            payload={"status": "ok", "stdout": "IMPORTANT TABLE\n" + ("x" * 10_000) + "\nLATEST END"},
        )
    )
    session_store.add_event(
        AgentEvent(
            session_id=session.id,
            type=AgentEventType.FEEDBACK_SIGNAL,
            payload={"kind": "success", "summary": "tool succeeded"},
        )
    )

    messages = ContextBuilder(
        session_store=session_store,
        memory_store=MemoryStore(tmp_path / "memory.jsonl"),
        max_chars=4_000,
    ).build("ORIGINAL QUESTION")
    rendered = str(messages)

    assert "IMPORTANT TABLE" in rendered
    assert "LATEST END" in rendered
    assert "ORIGINAL QUESTION" in rendered
    assert sum(len(str(message.get("content", ""))) for message in messages) <= 4_000


def test_context_accepts_gaia_system_prompt(tmp_path: Path):
    session = Session(workspace_root=str(tmp_path), mode=SessionMode.NON_INTERACTIVE)
    messages = ContextBuilder(
        session_store=SessionStore(session),
        memory_store=MemoryStore(tmp_path / "memory.jsonl"),
        system_prompt=GAIA_SYSTEM_PROMPT,
    ).build("research this")

    assert messages[0]["content"] == GAIA_SYSTEM_PROMPT
    assert "general AI assistant" in str(messages[0]["content"])


def test_context_projects_provider_tool_call_and_output_as_native_messages(tmp_path: Path) -> None:
    session = Session(workspace_root=str(tmp_path), mode=SessionMode.NON_INTERACTIVE)
    store = SessionStore(session)
    store.add_event(
        AgentEvent(
            session_id=session.id,
            type=AgentEventType.TOOL_CALL_REQUESTED,
            payload={
                "provider_call_id": "call_read_1",
                "tool_name": "file.read",
                "args": {"path": "README.md"},
            },
        )
    )
    store.add_event(
        AgentEvent(
            session_id=session.id,
            type=AgentEventType.TOOL_CALL_OUTPUT,
            payload={"tool_call_id": "internal-id", "status": "ok", "stdout": "hello"},
        )
    )

    messages = ContextBuilder(store, MemoryStore.ephemeral()).build("read the file")

    assistant = next(message for message in messages if message["role"] == "assistant")
    tool = next(message for message in messages if message["role"] == "tool")
    assert assistant["tool_calls"] == [
        {
            "id": "call_read_1",
            "type": "function",
            "function": {"name": "file.read", "arguments": '{"path": "README.md"}'},
        }
    ]
    assert tool["tool_call_id"] == "call_read_1"
    assert '"status": "ok"' in str(tool["content"])


def test_context_compacts_old_successes_and_latest_actionable_feedback(tmp_path: Path) -> None:
    session = Session(workspace_root=str(tmp_path), mode=SessionMode.NON_INTERACTIVE)
    store = SessionStore(session)

    def add_success(call_id: str, path: str) -> None:
        store.add_event(
            AgentEvent(
                session_id=session.id,
                type=AgentEventType.TOOL_CALL_REQUESTED,
                payload={
                    "provider_call_id": call_id,
                    "tool_name": "file.read",
                    "args": {"path": path},
                },
            )
        )
        store.add_event(
            AgentEvent(
                session_id=session.id,
                type=AgentEventType.POLICY_DECISION,
                payload={"decision": "allow", "rule_id": "safe", "reason": "safe"},
            )
        )
        store.add_event(
            AgentEvent(
                session_id=session.id,
                type=AgentEventType.TOOL_CALL_OUTPUT,
                payload={"tool_call_id": call_id, "status": "ok", "stdout": path},
            )
        )
        store.add_event(
            AgentEvent(
                session_id=session.id,
                type=AgentEventType.FEEDBACK_SIGNAL,
                payload={"kind": "success", "summary": "Tool completed successfully"},
            )
        )

    add_success("call_target_1", "target.txt")
    store.add_event(
        AgentEvent(
            session_id=session.id,
            type=AgentEventType.FEEDBACK_SIGNAL,
            payload={
                "kind": "policy_blocked",
                "summary": "Direct CSV mutation is blocked",
                "retryable": True,
                "suggested_next_step": "Rewrite the reproduction script and run it",
            },
        )
    )
    for index in range(5):
        add_success(f"call_other_{index}", f"other-{index}.txt")
    add_success("call_target_2", "target.txt")

    messages = ContextBuilder(store, MemoryStore.ephemeral(), max_chars=4_000).build("finish")
    rendered = str(messages)

    assert "Deterministic execution state" in rendered
    assert '"tool_name": "file.read"' in rendered
    assert '"path": "target.txt"' in rendered
    assert '"success_count": 2' in rendered
    assert "Rewrite the reproduction script and run it" in rendered


def test_context_projection_recursively_redacts_structured_state(tmp_path: Path) -> None:
    session = Session(workspace_root=str(tmp_path), mode=SessionMode.NON_INTERACTIVE)
    store = SessionStore(session)
    store.add_event(
        AgentEvent(
            session_id=session.id,
            type=AgentEventType.FEEDBACK_SIGNAL,
            payload={
                "kind": "tool_error",
                "summary": "nested provider error",
                "evidence": {"outer": [{"token": "sk-context-secret-123456789"}]},
                "retryable": True,
            },
        )
    )

    rendered = str(ContextBuilder(store, MemoryStore.ephemeral()).build("retry"))

    assert "sk-context-secret-123456789" not in rendered
    assert "REDACTED_SECRET" in rendered
