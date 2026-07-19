from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from phycode.config import load_project_config
from phycode.context import MemoryStore
from phycode.credentials import CredentialStore
from phycode.models import MemoryCategory, MemoryEntry, ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from phycode.tools.base import ToolRegistry

# Only non-sensitive scalar configuration may be written by a model-callable tool.
_ALLOWED_CONFIG_KEYS = {
    ("agent", "max_steps"),
    ("test", "command"),
    ("llm", "provider"),
    ("llm", "base_url"),
    ("llm", "model"),
}


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if not isinstance(value, str):
        # dict (subtable) / other shapes cannot be faithfully emitted here; refuse
        # rather than silently coerce to a string.
        raise TypeError(f"unsupported TOML value type: {type(value).__name__}")
    if any(ord(ch) < 0x20 and ch != "\t" for ch in value):
        raise ValueError("string value contains control characters")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _dump_toml(data: dict[str, dict[str, Any]]) -> str:
    blocks: list[str] = []
    for section, values in data.items():
        lines = [f"[{section}]"]
        lines.extend(f"{key} = {_toml_value(val)}" for key, val in values.items())
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def register_state_tools(
    registry: ToolRegistry,
    workspace_root: Path,
    memory_store: MemoryStore | None = None,
    credential_store: CredentialStore | None = None,
) -> None:
    root = workspace_root.resolve()
    memory = memory_store if memory_store is not None else MemoryStore(root / ".phycode" / "memory.jsonl")

    def workspace_status(call: ToolCall) -> ToolResult:
        return ToolResult(tool_call_id=call.id, status="ok", stdout=f"workspace_root={root}")

    def memory_read(call: ToolCall) -> ToolResult:
        return ToolResult(tool_call_id=call.id, status="ok", stdout=memory.summary())

    def memory_write(call: ToolCall) -> ToolResult:
        raw_category = str(call.args["category"])
        try:
            category = MemoryCategory(raw_category)
        except ValueError:
            allowed = ", ".join(item.value for item in MemoryCategory)
            return ToolResult(
                tool_call_id=call.id,
                status="invalid_tool_args",
                stderr=f"unknown category {raw_category!r}; allowed: {allowed}",
            )
        memory.append(MemoryEntry(category=category, content=str(call.args["content"]), source="agent"))
        return ToolResult(tool_call_id=call.id, status="ok", stdout=f"stored {category.value} memory")

    def config_read(call: ToolCall) -> ToolResult:
        return ToolResult(tool_call_id=call.id, status="ok", stdout=load_project_config(root).model_dump_json())

    def config_write(call: ToolCall) -> ToolResult:
        section = str(call.args["section"])
        key = str(call.args["key"])
        if (section, key) not in _ALLOWED_CONFIG_KEYS:
            allowed = ", ".join(f"{s}.{k}" for s, k in sorted(_ALLOWED_CONFIG_KEYS))
            return ToolResult(
                tool_call_id=call.id,
                status="invalid_tool_args",
                stderr=f"config key {section}.{key} is not writable; allowed: {allowed}",
            )
        value: Any = call.args["value"]
        if key == "max_steps":
            try:
                value = int(value)
            except (TypeError, ValueError):
                return ToolResult(tool_call_id=call.id, status="invalid_tool_args", stderr="max_steps must be an integer")
        config_path = root / "phycode.toml"
        # Render to a string first; only write if the whole file serializes cleanly,
        # so a bad value or an un-serializable existing file never corrupts config.
        try:
            existing = tomllib.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
            data: dict[str, dict[str, Any]] = {}
            for existing_key, existing_value in existing.items():
                if not isinstance(existing_value, dict):
                    raise ValueError(f"cannot rewrite phycode.toml: top-level key {existing_key!r} is not a table")
                data[existing_key] = dict(existing_value)
            data.setdefault(section, {})[key] = value
            rendered = _dump_toml(data)
        except (ValueError, TypeError, tomllib.TOMLDecodeError) as exc:
            return ToolResult(tool_call_id=call.id, status="invalid_tool_args", stderr=str(exc))
        config_path.write_text(rendered, encoding="utf-8")
        return ToolResult(tool_call_id=call.id, status="ok", stdout=f"set {section}.{key}")

    def keys_status(call: ToolCall) -> ToolResult:
        store = credential_store if credential_store is not None else CredentialStore()
        provider = str(call.args.get("provider", "openai-compatible"))
        return ToolResult(tool_call_id=call.id, status="ok", stdout=store.status(provider).model_dump_json())

    _register(registry, "workspace.status", "Show workspace status", ToolRiskLevel.SAFE, workspace_status)
    _register(registry, "memory.read", "Read curated project memory", ToolRiskLevel.SAFE, memory_read)
    _register(
        registry,
        "memory.write",
        "Append a long-term memory entry",
        ToolRiskLevel.RISKY,
        memory_write,
        properties={"category": {"type": "string"}, "content": {"type": "string"}},
        required=["category", "content"],
        mutates_state=True,
    )
    _register(registry, "config.read", "Read non-sensitive project configuration", ToolRiskLevel.SAFE, config_read)
    _register(
        registry,
        "config.write",
        "Update a non-sensitive configuration value",
        ToolRiskLevel.RISKY,
        config_write,
        properties={"section": {"type": "string"}, "key": {"type": "string"}, "value": {}},
        required=["section", "key", "value"],
        mutates_state=True,
    )
    _register(
        registry,
        "keys.status",
        "Report credential presence without revealing secrets",
        ToolRiskLevel.SAFE,
        keys_status,
        properties={"provider": {"type": "string"}},
    )


def _register(
    registry,
    name,
    description,
    risk_level,
    executor,
    properties=None,
    required=None,
    mutates_state=False,
) -> None:
    schema: dict[str, Any] = {"type": "object"}
    if properties is not None:
        schema["properties"] = properties
    if required is not None:
        schema["required"] = required
    registry.register(
        ToolSpec(
            name=name,
            description=description,
            input_schema=schema,
            risk_level=risk_level,
            mutates_state=mutates_state,
        ),
        executor,
    )
