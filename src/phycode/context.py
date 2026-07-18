from __future__ import annotations

import json
from pathlib import Path

from phycode.models import AgentEvent, MemoryEntry, Session, ToolSpec
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
        self.events.append(event)

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
        if self.path is None:
            self._ephemeral_entries.append(entry)
            return
        payload = redact_obj(entry.model_dump(mode="json"))
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

    def build(self, current_input: str, tools: list[ToolSpec] | None = None) -> list[dict[str, object]]:
        memory = _clip_text(self.memory_store.summary(), min(1_500, self.max_chars // 8))
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
            "Recent events:\n"
        )
        suffix = f"\nUser: {user_input}"
        recent_budget = max(0, self.max_chars - len(fixed) - len(suffix))
        recent_parts: list[str] = []
        remaining = recent_budget
        for event in reversed(self.session_store.recent_events()):
            rendered = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, default=str)
            separator_length = 1 if recent_parts else 0
            if remaining <= separator_length:
                break
            clipped = _clip_text(rendered, min(16_000, remaining - separator_length))
            recent_parts.append(clipped)
            remaining -= len(clipped) + separator_length
        recent_parts.reverse()
        content = fixed + "\n".join(recent_parts) + suffix
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": redact_text(content)},
        ]
