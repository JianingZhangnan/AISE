from __future__ import annotations

import os
import re
from pathlib import Path

from phycode.models import ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from phycode.tools.base import ToolRegistry
from phycode.visibility import PathVisibilityPolicy, VisibilityViolation, is_sensitive_path

_SKIP_DIRS = {".git", ".phycode", ".venv", "__pycache__", "node_modules"}
_MAX_MATCHES = 200


def _display(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _iter_files(base: Path, visibility: PathVisibilityPolicy) -> list[Path]:
    files: list[Path] = []
    for current, directory_names, file_names in os.walk(base):
        current_path = Path(current)
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name not in _SKIP_DIRS and visibility.is_visible(current_path / name)
        )
        for name in sorted(file_names):
            path = current_path / name
            if not visibility.is_visible(path):
                continue
            if not path.is_file() or is_sensitive_path(str(path)):
                continue
            files.append(path)
    return sorted(files)


def register_search_tools(
    registry: ToolRegistry,
    workspace_root: Path,
    visibility: PathVisibilityPolicy | None = None,
) -> None:
    root = workspace_root.resolve()
    path_visibility = visibility if visibility is not None else PathVisibilityPolicy(root)

    def search_grep(call: ToolCall) -> ToolResult:
        try:
            pattern = re.compile(str(call.args["pattern"]))
        except re.error as exc:
            return ToolResult(tool_call_id=call.id, status="invalid_tool_args", stderr=f"bad regex: {exc}")
        try:
            base = path_visibility.resolve(str(call.args["path"])) if "path" in call.args else root
        except VisibilityViolation as exc:
            return ToolResult(tool_call_id=call.id, status="policy_blocked", stderr=str(exc))
        if base.is_file():
            files = [] if is_sensitive_path(str(base)) or not path_visibility.is_visible(base) else [base]
        else:
            files = _iter_files(base, path_visibility)
        matches: list[str] = []
        truncated = False
        for file in files:
            if not path_visibility.is_visible(file):
                continue
            try:
                lines = file.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            rel = _display(file, root)
            for lineno, line in enumerate(lines, start=1):
                if pattern.search(line):
                    matches.append(f"{rel}:{lineno}:{line}")
                    if len(matches) >= _MAX_MATCHES:
                        truncated = True
                        break
            if truncated:
                break
        return ToolResult(tool_call_id=call.id, status="ok", stdout="\n".join(matches), truncated=truncated)

    def search_glob(call: ToolCall) -> ToolResult:
        pattern = str(call.args["pattern"])
        results: list[str] = []
        for match in root.glob(pattern):
            try:
                resolved = path_visibility.resolve(match)
            except VisibilityViolation:
                continue
            if not resolved.is_file():
                continue
            if is_sensitive_path(str(resolved)):
                continue
            results.append(_display(resolved, root))
        return ToolResult(tool_call_id=call.id, status="ok", stdout="\n".join(sorted(results)))

    registry.register(
        ToolSpec(
            name="search.grep",
            description="Search workspace file contents by regular expression",
            input_schema={
                "type": "object",
                "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
                "required": ["pattern"],
            },
            risk_level=ToolRiskLevel.SAFE,
        ),
        search_grep,
    )
    registry.register(
        ToolSpec(
            name="search.glob",
            description="Locate workspace files by glob pattern",
            input_schema={
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
            risk_level=ToolRiskLevel.SAFE,
        ),
        search_glob,
    )
