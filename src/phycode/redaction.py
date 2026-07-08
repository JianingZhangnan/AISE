from __future__ import annotations

import re

SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{10,}"), "[REDACTED_SECRET]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{10,}", re.IGNORECASE), "Bearer [REDACTED_SECRET]"),
    (re.compile(r"(OPENAI_API_KEY=)[^\s]+", re.IGNORECASE), r"\1[REDACTED_SECRET]"),
    (re.compile(r"(ANTHROPIC_API_KEY=)[^\s]+", re.IGNORECASE), r"\1[REDACTED_SECRET]"),
    (re.compile(r"(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[^'\"\s]+", re.IGNORECASE), r"\1[REDACTED_SECRET]"),
]


def redact_text(text: str) -> str:
    redacted = text
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted
