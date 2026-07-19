from __future__ import annotations

import json
from pathlib import Path

from phycode.conversation import project_conversation
from phycode.models import AgentEvent, AgentEventType, MemoryEntry, Session, ToolSpec
from phycode.redaction import redact_obj, redact_text

CODING_SYSTEM_PROMPT = (
    "You are PhyCode, a CLI coding agent harness. Use tools safely and follow policy feedback."
)
GAIA_SYSTEM_PROMPT = """You are PhyCode operating as a general AI assistant for GAIA tasks.
Answer the user's actual question; do not explore the coding workspace unless the task refers to an attached file.
For self-contained mathematics, logic, probability, or simulation questions, reason from the prompt and use the
calculator when useful; do not search the web for an answer that can be derived from the supplied rules.
Use web.search to find candidate sources and web.fetch to verify relevant source content. Search snippets are leads,
not proof. Pass a focused query to web.fetch when reading a long page. If a page blocks access, look for an official API
or another primary representation. Prefer primary sources,
gather enough evidence before answering, and do not guess when tools can verify a fact. Never repeat an identical
successful tool call; synthesize the evidence it already returned.
Finish with exactly: FINAL ANSWER: [answer]. The answer must be a number, as few words as possible, or a comma-separated
list of numbers and/or strings. Do not add thousands separators or units to numbers unless requested. For strings, avoid
articles and abbreviations, and use digits rather than spelling out numbers unless the question asks otherwise. Respect
the exact scale and unit requested: for example, "how many thousand hours" asks for the count of thousands, not hours."""


def _clip_text(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    marker = "\n...[clipped]...\n"
    if limit <= len(marker):
        return value[:limit]
    available = limit - len(marker)
    head = (available * 2) // 3
    return value[:head] + marker + value[-(available - head) :]


class SessionStore:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.events: list[AgentEvent] = []

    def add_event(self, event: AgentEvent) -> None:
        safe = event.model_copy(update={"payload": redact_obj(event.payload)})
        self.events.append(safe)

    def recent_events(self, limit: int = 12) -> list[AgentEvent]:
        return self.events[-limit:]


class MemoryStore:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._ephemeral_entries: list[MemoryEntry] = []
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def ephemeral(cls) -> MemoryStore:
        return cls(None)

    def append(self, entry: MemoryEntry) -> None:
        safe_entry = MemoryEntry.model_validate(redact_obj(entry.model_dump(mode="json")))
        if self.path is None:
            self._ephemeral_entries.append(safe_entry)
            return
        payload = safe_entry.model_dump(mode="json")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def entries(self) -> list[MemoryEntry]:
        if self.path is None:
            return list(self._ephemeral_entries)
        if not self.path.exists():
            return []
        result: list[MemoryEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                result.append(MemoryEntry.model_validate(json.loads(line)))
            except (json.JSONDecodeError, ValueError):
                continue  # tolerate a legacy corrupt line rather than crash context build
        return result

    def summary(self) -> str:
        return "\n".join(f"- {entry.category.value}: {entry.content}" for entry in self.entries())


def _render_message(message: dict[str, object]) -> str | None:
    """Render one already-projected provider message without changing its semantics."""
    role = message.get("role")
    if role == "assistant" and message.get("tool_calls"):
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            return None
        first = tool_calls[0]
        if not isinstance(first, dict):
            return None
        function = first.get("function")
        if not isinstance(function, dict):
            return None
        return (
            f"[tool call] {function.get('name', '')} "
            f"{function.get('arguments', '{}')}"
        )
    if role == "tool":
        content = message.get("content", "")
        try:
            payload = json.loads(content) if isinstance(content, str) else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        body = str(payload.get("stdout") or payload.get("stderr") or "").strip()[:600]
        return f"[tool result] status={payload.get('status', '')} {body}".rstrip()
    if role == "user":
        content = message.get("content", "")
        try:
            payload = json.loads(content) if isinstance(content, str) else {}
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        feedback = payload.get("runtime_feedback")
        if not isinstance(feedback, dict):
            return None
        return (
            f"[feedback] {feedback.get('kind', '')}: "
            f"{feedback.get('summary', '')}"
        ).rstrip()
    if role == "assistant" and message.get("content"):
        return f"[assistant] {message.get('content', '')}"
    return None


class ContextBuilder:
    def __init__(
        self,
        session_store: SessionStore,
        memory_store: MemoryStore,
        max_chars: int = 12000,
        system_prompt: str = CODING_SYSTEM_PROMPT,
        workspace_label: str | None = None,
    ) -> None:
        self.session_store = session_store
        self.memory_store = memory_store
        self.max_chars = max_chars
        self.system_prompt = system_prompt
        self.workspace_label = workspace_label
        self._turn_input: str | None = None
        self._turn_start_index = 0

    def begin_turn(self, current_input: str, *, persist: bool = True) -> AgentEvent | None:
        self._turn_input = current_input
        self._turn_start_index = len(self.session_store.events)
        if not persist:
            return None
        event = AgentEvent(
            session_id=self.session_store.session.id,
            type=AgentEventType.USER_MESSAGE,
            payload={"text": redact_text(current_input)},
        )
        self.session_store.add_event(event)
        return event

    def build(self, current_input: str, tools: list[ToolSpec] | None = None) -> list[dict[str, object]]:
        if self._turn_input != current_input:
            self.begin_turn(current_input)
        memory = _clip_text(self.memory_store.summary(), min(1_500, self.max_chars // 8))
        readable_projection = project_conversation(
            self.session_store.recent_events(),
            min(2_500, self.max_chars // 5),
        )
        recent = _clip_text(
            "\n".join(
                rendered
                for message in readable_projection.messages
                if (rendered := _render_message(message)) is not None
            ),
            min(2_500, self.max_chars // 5),
        )
        tool_lines = "\n".join(f"- {spec.name} ({spec.risk_level.value}): {spec.description}" for spec in (tools or []))
        tool_lines = _clip_text(tool_lines, min(2_500, self.max_chars // 5))
        user_input = _clip_text(current_input, max(1_000, self.max_chars // 3))
        workspace = _clip_text(
            self.workspace_label
            if self.workspace_label is not None
            else str(self.session_store.session.workspace_root),
            500,
        )
        fixed = (
            f"Workspace: {workspace}\n"
            f"Tools:\n{tool_lines}\n"
            f"Memory:\n{memory}\n"
            f"Recent activity:\n{recent}\n"
        )
        state_budget = min(4_500, max(600, self.max_chars // 3))
        initial_projection = project_conversation(self.session_store.events, 0)
        state = _clip_text(
            json.dumps(
                redact_obj(initial_projection.execution_state),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
            state_budget,
        )
        content = redact_text(
            fixed
            + "Deterministic execution state:\n"
            + state
        )
        current_user: dict[str, object] = {
            "role": "user",
            "content": f"User: {redact_text(user_input)}",
        }
        recent_budget = max(0, self.max_chars - len(content) - len(user_input))
        turn_start = min(self._turn_start_index, len(self.session_store.events))
        before_turn = self.session_store.events[:turn_start]
        after_start = turn_start + 1 if (
            turn_start < len(self.session_store.events)
            and self.session_store.events[turn_start].type == AgentEventType.USER_MESSAGE
        ) else turn_start
        after_turn = self.session_store.events[after_start:]
        if before_turn and after_turn:
            before_budget = recent_budget // 3
        elif before_turn:
            before_budget = recent_budget
        else:
            before_budget = 0
        after_budget = recent_budget - before_budget
        before_projection = project_conversation(before_turn, before_budget)
        after_projection = project_conversation(after_turn, after_budget)
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "system", "content": content},
            *before_projection.messages,
            current_user,
            *after_projection.messages,
        ]
