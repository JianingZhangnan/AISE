from __future__ import annotations

import re
from pathlib import Path

from phycode.models import ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from phycode.policy import is_credential_path
from phycode.tools.base import ToolRegistry

_SKIP_DIRS = {".git", ".phycode", ".venv", "__pycache__", "node_modules"}
_MAX_MATCHES = 200


def _display(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _iter_files(base: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(base).parts):
            continue
        if is_credential_path(str(path)):  # never read secrets back through grep
            continue
        files.append(path)
    return files


def register_search_tools(registry: ToolRegistry, workspace_root: Path) -> None:
    root = workspace_root.resolve()

    def search_grep(call: ToolCall) -> ToolResult:
        try:
            pattern = re.compile(str(call.args["pattern"]))
        except re.error as exc:
            return ToolResult(tool_call_id=call.id, status="invalid_tool_args", stderr=f"bad regex: {exc}")
        base = Path(call.args["path"]) if "path" in call.args else root
        base = (base if base.is_absolute() else root / base).resolve()
        if base.is_file():
            files = [] if is_credential_path(str(base)) else [base]
        else:
            files = _iter_files(base)
        matches: list[str] = []
        truncated = False
        for file in files:
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
            if not match.is_file():
                continue
            resolved = match.resolve()
            if not _within(resolved, root):  # confine to the workspace, no ../ escape
                continue
            if is_credential_path(str(resolved)):
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
