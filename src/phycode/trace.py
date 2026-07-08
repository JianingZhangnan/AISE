from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from phycode.models import AgentEvent
from phycode.redaction import redact_text


class TraceStore:
    def __init__(self, trace_dir: Path) -> None:
        self.trace_dir = trace_dir
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def append(self, event: AgentEvent) -> None:
        path = self.trace_dir / f"{event.session_id}.jsonl"
        raw = event.model_dump_json()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(redact_text(raw) + "\n")

    def read_events_raw(self, session_id: str) -> list[dict[str, Any]]:
        path = self.trace_dir / f"{session_id}.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
