from __future__ import annotations

from pathlib import Path

from phycode.models import AgentEvent, AgentEventType
from phycode.visibility import VisibilityViolation, normalize_public_relative_path


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


def project_agent_event(event: AgentEvent, workspace_root: Path) -> AgentEvent:
    if event.type != AgentEventType.TOOL_CALL_REQUESTED:
        return event
    if event.payload.get("tool_name") != "process.run":
        return event
    payload = dict(event.payload)
    args = dict(payload.get("args", {}))
    argv = args.get("argv")
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
