from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from phycode.config import load_project_config, write_config_value
from phycode.context import MemoryStore
from phycode.credentials import CredentialStore
from phycode.models import MemoryCategory, MemoryEntry, ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from phycode.tools.base import ToolRegistry


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
        try:
            write_config_value(root, section, key, call.args["value"])
        except (ValueError, TypeError, tomllib.TOMLDecodeError) as exc:
            return ToolResult(tool_call_id=call.id, status="invalid_tool_args", stderr=str(exc))
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
    )
    _register(
        registry,
        "keys.status",
        "Report credential presence without revealing secrets",
        ToolRiskLevel.SAFE,
        keys_status,
        properties={"provider": {"type": "string"}},
    )


def _register(registry, name, description, risk_level, executor, properties=None, required=None) -> None:
    schema: dict[str, Any] = {"type": "object"}
    if properties is not None:
        schema["properties"] = properties
    if required is not None:
        schema["required"] = required
    registry.register(
        ToolSpec(name=name, description=description, input_schema=schema, risk_level=risk_level),
        executor,
    )
