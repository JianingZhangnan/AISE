from __future__ import annotations

import difflib
from pathlib import Path

from phycode.models import ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from phycode.tools.base import ToolRegistry


def _read(path: Path, limit: int | None = None, offset: int = 0) -> tuple[str, bool]:
    text = path.read_text(encoding="utf-8")
    sliced = text[offset:]
    if limit is not None and len(sliced) > limit:
        return sliced[:limit], True
    return sliced, False


def _file_read(call: ToolCall) -> ToolResult:
    content, truncated = _read(Path(call.args["path"]), call.args.get("limit"), call.args.get("offset", 0))
    return ToolResult(tool_call_id=call.id, status="ok", stdout=content, truncated=truncated)


def _file_list(call: ToolCall) -> ToolResult:
    root = Path(call.args.get("path", "."))
    entries = sorted(item.name for item in root.iterdir())
    return ToolResult(tool_call_id=call.id, status="ok", stdout="\n".join(entries))


def _file_write(call: ToolCall) -> ToolResult:
    path = Path(call.args["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(call.args["content"]), encoding="utf-8")
    return ToolResult(tool_call_id=call.id, status="ok", stdout=f"wrote {path}")


def _file_edit(call: ToolCall) -> ToolResult:
    path = Path(call.args["path"])
    old = str(call.args["old"])
    new = str(call.args["new"])
    before = path.read_text(encoding="utf-8")
    count = before.count(old)
    if count == 0:
        return ToolResult(tool_call_id=call.id, status="tool_error", stderr="old text not found")
    if count > 1:
        return ToolResult(tool_call_id=call.id, status="tool_error", stderr="old text matches more than once")

    after = before.replace(old, new, 1)
    path.write_text(after, encoding="utf-8")
    diff = "\n".join(
        difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile=str(path), tofile=str(path), lineterm="")
    )
    return ToolResult(tool_call_id=call.id, status="ok", stdout=diff)


def register_file_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="file.read",
            description="Read a file",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
            risk_level=ToolRiskLevel.SAFE,
        ),
        _file_read,
    )
    registry.register(
        ToolSpec(
            name="file.list",
            description="List a directory",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
            risk_level=ToolRiskLevel.SAFE,
        ),
        _file_list,
    )
    registry.register(
        ToolSpec(
            name="file.write",
            description="Write a file",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            risk_level=ToolRiskLevel.RISKY,
        ),
        _file_write,
    )
    registry.register(
        ToolSpec(
            name="file.edit",
            description="Edit a file by exact replacement",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                },
                "required": ["path", "old", "new"],
            },
            risk_level=ToolRiskLevel.RISKY,
        ),
        _file_edit,
    )
