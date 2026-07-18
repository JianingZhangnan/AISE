from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from phycode.models import AgentEvent, AgentEventType
from phycode.visibility import VisibilityViolation, normalize_public_relative_path

_REDACTED_PATH = "[REDACTED_PATH]"
_PATH_FIELD_NAMES = {
    "cwd",
    "directory",
    "file",
    "path",
    "trace",
    "workspace",
    "workspace_root",
}


def _public_script_path(argument: str, workspace_root: Path) -> str:
    if not argument.casefold().endswith(".py"):
        return "[REDACTED_ARG]"
    candidate = Path(argument)
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    try:
        resolved = candidate.resolve(strict=False)
        relative = resolved.relative_to(workspace_root).as_posix()
        return normalize_public_relative_path(relative)
    except (OSError, RuntimeError, ValueError, VisibilityViolation):
        return "[REDACTED_ARG]"


def _is_path_field(key: str | None) -> bool:
    if key is None:
        return False
    normalized = key.casefold()
    return (
        normalized in _PATH_FIELD_NAMES
        or normalized.endswith("_path")
        or normalized.endswith("_dir")
        or normalized.endswith("_file")
    )


def _replace_workspace_mentions(value: str, workspace_root: Path) -> str:
    projected = value
    variants = {str(workspace_root), workspace_root.as_posix()}
    for root_text in sorted(variants, key=len, reverse=True):
        projected = re.sub(
            re.escape(root_text + "\\"),
            "",
            projected,
            flags=re.IGNORECASE,
        )
        projected = re.sub(
            re.escape(root_text + "/"),
            "",
            projected,
            flags=re.IGNORECASE,
        )
        projected = re.sub(
            re.escape(root_text),
            ".",
            projected,
            flags=re.IGNORECASE,
        )
    return projected


def _project_string(value: str, key: str | None, workspace_root: Path) -> str:
    if _is_path_field(key):
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = workspace_root / candidate
        try:
            resolved = candidate.resolve(strict=False)
            relative = resolved.relative_to(workspace_root)
        except (OSError, RuntimeError, ValueError):
            return _REDACTED_PATH
        if not relative.parts:
            return "."
        try:
            return normalize_public_relative_path(relative.as_posix())
        except VisibilityViolation:
            return _REDACTED_PATH
    return _replace_workspace_mentions(value, workspace_root)


def _project_value(value: Any, key: str | None, workspace_root: Path) -> Any:
    if isinstance(value, dict):
        return {
            item_key: _project_value(item_value, str(item_key), workspace_root)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_project_value(item, key, workspace_root) for item in value]
    if isinstance(value, tuple):
        return tuple(_project_value(item, key, workspace_root) for item in value)
    if isinstance(value, str):
        return _project_string(value, key, workspace_root)
    return value


def project_agent_event(event: AgentEvent, workspace_root: Path) -> AgentEvent:
    payload = _project_value(dict(event.payload), None, workspace_root)
    if event.type != AgentEventType.TOOL_CALL_REQUESTED:
        return event.model_copy(update={"payload": payload})
    if payload.get("tool_name") != "process.run":
        return event.model_copy(update={"payload": payload})
    args = dict(payload.get("args", {}))
    argv = event.payload.get("args", {}).get("argv")
    if isinstance(argv, list) and argv and all(isinstance(item, str) for item in argv):
        projected = [Path(argv[0]).name]
        if len(argv) > 1:
            projected.append(_public_script_path(argv[1], workspace_root))
        projected.extend("[REDACTED_ARG]" for _ in argv[2:])
        args["argv"] = projected
    else:
        args["argv"] = ["[REDACTED_ARG]"]
    payload["args"] = args
    return event.model_copy(update={"payload": payload})
