from __future__ import annotations

import json
from pathlib import Path

from phycode.models import AgentEvent, MemoryEntry, Session, ToolSpec
from phycode.redaction import redact_obj, redact_text


class SessionStore:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.events: list[AgentEvent] = []

    def add_event(self, event: AgentEvent) -> None:
        self.events.append(event)

    def recent_events(self, limit: int = 12) -> list[AgentEvent]:
        return self.events[-limit:]


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: MemoryEntry) -> None:
        payload = redact_obj(entry.model_dump(mode="json"))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def entries(self) -> list[MemoryEntry]:
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


def _render_event(event: AgentEvent) -> str:
    """Render one event as a compact, LLM-friendly line (not a raw dict dump)."""
    kind = event.type.value
    payload = event.payload
    if kind == "tool_call_requested":
        args = json.dumps(payload.get("args", {}), ensure_ascii=False, sort_keys=True)
        return f"[tool call] {payload.get('tool_name', '')} {args}"
    if kind == "tool_call_output":
        body = (str(payload.get("stdout") or payload.get("stderr") or "")).strip()[:600]
        return f"[tool result] status={payload.get('status', '')} {body}".rstrip()
    if kind == "feedback_signal":
        return f"[feedback] {payload.get('kind', '')}: {payload.get('summary', '')}".rstrip()
    if kind == "policy_decision":
        return f"[policy] {payload.get('decision', '')} ({payload.get('rule_id', '')})"
    if kind in ("assistant_commentary", "assistant_final", "reasoning_summary"):
        return f"[assistant] {payload.get('text', '')}"
    if kind == "error":
        return f"[error] {payload.get('message', '')}"
    return f"[{kind}] {payload}"


class ContextBuilder:
    def __init__(self, session_store: SessionStore, memory_store: MemoryStore, max_chars: int = 12000) -> None:
        self.session_store = session_store
        self.memory_store = memory_store
        self.max_chars = max_chars

    def build(self, current_input: str, tools: list[ToolSpec] | None = None) -> list[dict[str, object]]:
        system = "You are PhyCode, a CLI coding agent harness. Use tools safely and follow policy feedback."
        memory = self.memory_store.summary()
        recent = "\n".join(_render_event(event) for event in self.session_store.recent_events())
        tool_lines = "\n".join(f"- {spec.name} ({spec.risk_level.value}): {spec.description}" for spec in (tools or []))
        content = (
            f"Workspace: {self.session_store.session.workspace_root}\n"
            f"Tools:\n{tool_lines}\n"
            f"Memory:\n{memory}\n"
            f"Recent activity:\n{recent}\n"
            f"User: {current_input}"
        )
        if len(content) > self.max_chars:
            content = content[-self.max_chars :]
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": redact_text(content)},
        ]
