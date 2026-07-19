from __future__ import annotations

import re
from typing import Any

SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{10,}"), "[REDACTED_SECRET]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{10,}", re.IGNORECASE), "Bearer [REDACTED_SECRET]"),
    (re.compile(r"(OPENAI_API_KEY=)[^\s]+", re.IGNORECASE), r"\1[REDACTED_SECRET]"),
    (re.compile(r"(ANTHROPIC_API_KEY=)[^\s]+", re.IGNORECASE), r"\1[REDACTED_SECRET]"),
    (re.compile(r"(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[^'\"\s]+", re.IGNORECASE), r"\1[REDACTED_SECRET]"),
    (
        re.compile(
            r"((?:password|token|secret|authorization)['\"]?\s*[:=]\s*['\"]?)[^'\"\s]+",
            re.IGNORECASE,
        ),
        r"\1[REDACTED_SECRET]",
    ),
]

_REDACTED_SECRET = "[REDACTED_SECRET]"


def _is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.casefold().replace("-", "_")
    if normalized == "credential_ref":
        return False
    compact = normalized.replace("_", "")
    if compact.endswith("apikey"):
        return True
    return normalized in {"token", "secret", "password", "authorization"} or normalized.endswith(
        ("_token", "_secret", "_password", "_authorization")
    )


def redact_text(text: str) -> str:
    redacted = text
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_obj(value: Any) -> Any:
    """Redact every string leaf of a JSON-like structure.

    Redacting each string *before* re-serializing keeps the output valid JSON,
    unlike running redact_text over an already-serialized line (a greedy secret
    pattern can otherwise eat the closing quote/brace and corrupt the record).
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            key: _REDACTED_SECRET if _is_sensitive_key(key) else redact_obj(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_obj(item) for item in value]
    return value
