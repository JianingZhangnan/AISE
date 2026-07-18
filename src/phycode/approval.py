from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from phycode.models import PolicyAction, PolicyDecision, ToolCall
from phycode.visibility import PathVisibilityPolicy, VisibilityViolation


class ApprovalGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str
    path: str | None = None
    argv: tuple[str, ...] | None = None
    cwd: str | None = None

    @model_validator(mode="after")
    def validate_target(self) -> ApprovalGrant:
        if self.tool_name in {"file.write", "file.edit"}:
            if self.path is None or self.argv is not None or self.cwd is not None:
                raise ValueError("file approval grants require only path")
            if not self.path or "\x00" in self.path:
                raise ValueError("file approval path must be non-empty and contain no NUL")
            return self
        if self.tool_name == "process.run":
            if self.path is not None or self.argv is None or self.cwd is None:
                raise ValueError("process.run approval grants require argv and cwd")
            if (
                not self.argv
                or any(not item or "\x00" in item for item in self.argv)
                or not Path(self.argv[0]).is_absolute()
            ):
                raise ValueError("process.run approval argv must contain an absolute executable and valid strings")
            if not self.cwd or "\x00" in self.cwd:
                raise ValueError("process.run approval cwd must be non-empty and contain no NUL")
            return self
        raise ValueError(f"unsupported approval tool: {self.tool_name}")


class _ApprovalDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grants: tuple[ApprovalGrant, ...]


ApprovalKey = tuple[str, str] | tuple[str, tuple[str, ...], str]


def _canonical_path(visibility: PathVisibilityPolicy, path: str) -> str:
    return os.path.normcase(str(visibility.resolve(path)))


class ApprovalManifest:
    def __init__(self, grants: tuple[ApprovalGrant, ...], visibility: PathVisibilityPolicy) -> None:
        self._remaining = list(grants)
        self._visibility = visibility

    @classmethod
    def from_json(cls, path: Path, workspace_root: Path) -> ApprovalManifest:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        document = _ApprovalDocument.model_validate(payload)
        visibility = PathVisibilityPolicy(workspace_root)
        canonical_grants = tuple(cls._canonicalize_grant(grant, visibility) for grant in document.grants)
        return cls(canonical_grants, visibility)

    def __call__(self, call: ToolCall, decision: PolicyDecision) -> bool:
        if decision.decision != PolicyAction.ASK or decision.tool_call_id != call.id:
            return False
        try:
            key = self._call_key(call)
        except (OSError, RuntimeError, VisibilityViolation):
            return False
        if key is None:
            return False
        for index, grant in enumerate(self._remaining):
            if self._grant_key(grant) == key:
                del self._remaining[index]
                return True
        return False

    @staticmethod
    def _canonicalize_grant(grant: ApprovalGrant, visibility: PathVisibilityPolicy) -> ApprovalGrant:
        updates: dict[str, object] = {}
        if grant.path is not None:
            updates["path"] = _canonical_path(visibility, grant.path)
        if grant.cwd is not None:
            updates["cwd"] = _canonical_path(visibility, grant.cwd)
        return grant.model_copy(update=updates)

    def _call_key(self, call: ToolCall) -> ApprovalKey | None:
        if call.tool_name == "file.write":
            if set(call.args) != {"path", "content"}:
                return None
            path = call.args.get("path")
            content = call.args.get("content")
            if not isinstance(path, str) or not path or "\x00" in path or not isinstance(content, str):
                return None
            return call.tool_name, _canonical_path(self._visibility, path)
        if call.tool_name == "file.edit":
            if set(call.args) != {"path", "old", "new"}:
                return None
            path = call.args.get("path")
            old = call.args.get("old")
            new = call.args.get("new")
            if (
                not isinstance(path, str)
                or not path
                or "\x00" in path
                or not isinstance(old, str)
                or not isinstance(new, str)
            ):
                return None
            return call.tool_name, _canonical_path(self._visibility, path)
        if call.tool_name == "process.run":
            if set(call.args) - {"argv", "cwd", "timeout"}:
                return None
            argv = call.args.get("argv")
            cwd = call.args.get("cwd", ".")
            timeout = call.args.get("timeout", 30)
            if (
                not isinstance(argv, list)
                or not argv
                or not all(isinstance(item, str) and item and "\x00" not in item for item in argv)
                or not Path(argv[0]).is_absolute()
                or not isinstance(cwd, str)
                or not cwd
                or "\x00" in cwd
                or isinstance(timeout, bool)
                or not isinstance(timeout, int)
                or not 1 <= timeout <= 300
            ):
                return None
            return call.tool_name, tuple(argv), _canonical_path(self._visibility, cwd)
        return None

    @staticmethod
    def _grant_key(grant: ApprovalGrant) -> ApprovalKey:
        if grant.path is not None:
            return grant.tool_name, grant.path
        assert grant.argv is not None
        assert grant.cwd is not None
        return grant.tool_name, grant.argv, grant.cwd
