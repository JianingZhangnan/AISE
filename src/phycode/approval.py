from __future__ import annotations

import json
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
            return self
        if self.tool_name == "process.run":
            if self.path is not None or self.argv is None or self.cwd is None:
                raise ValueError("process.run approval grants require argv and cwd")
            return self
        raise ValueError(f"unsupported approval tool: {self.tool_name}")


class _ApprovalDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grants: tuple[ApprovalGrant, ...]


ApprovalKey = tuple[str, str] | tuple[str, tuple[str, ...], str]


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
            updates["path"] = str(visibility.resolve(grant.path))
        if grant.cwd is not None:
            updates["cwd"] = str(visibility.resolve(grant.cwd))
        return grant.model_copy(update=updates)

    def _call_key(self, call: ToolCall) -> ApprovalKey | None:
        if call.tool_name in {"file.write", "file.edit"}:
            path = call.args.get("path")
            if not isinstance(path, str):
                return None
            return call.tool_name, str(self._visibility.resolve(path))
        if call.tool_name == "process.run":
            argv = call.args.get("argv")
            cwd = call.args.get("cwd", ".")
            if (
                not isinstance(argv, list)
                or not all(isinstance(item, str) for item in argv)
                or not isinstance(cwd, str)
            ):
                return None
            return call.tool_name, tuple(argv), str(self._visibility.resolve(cwd))
        return None

    @staticmethod
    def _grant_key(grant: ApprovalGrant) -> ApprovalKey:
        if grant.path is not None:
            return grant.tool_name, grant.path
        assert grant.argv is not None
        assert grant.cwd is not None
        return grant.tool_name, grant.argv, grant.cwd
