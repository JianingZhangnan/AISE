from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from phycode.models import AgentEvent
from phycode.redaction import redact_obj


class TraceStore:
    def __init__(self, trace_dir: Path) -> None:
        self.trace_dir = trace_dir
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def append(self, event: AgentEvent) -> None:
        path = self.trace_dir / f"{event.session_id}.jsonl"
        payload = redact_obj(event.model_dump(mode="json"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def read_events_raw(self, session_id: str) -> list[dict[str, Any]]:
        path = self.trace_dir / f"{session_id}.jsonl"
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events
