from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from phycode.models import PolicyAction, PolicyDecision, ToolCall
from phycode.redaction import redact_text
from phycode.visibility import (
    PRBENCH_HIDDEN_PATH_COMPONENTS,
    PathVisibilityPolicy,
    VisibilityViolation,
    is_sensitive_path,
)


class ApprovalGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str
    path: str | None = None
    argv: tuple[str, ...] | None = None
    cwd: str | None = None
    script_sha256: str | None = None

    @model_validator(mode="after")
    def validate_target(self) -> ApprovalGrant:
        if self.tool_name in {"file.write", "file.edit"}:
            if (
                self.path is None
                or self.argv is not None
                or self.cwd is not None
                or self.script_sha256 is not None
            ):
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
                raise ValueError(
                    "process.run approval argv must contain an absolute executable and valid strings"
                )
            if not self.cwd or "\x00" in self.cwd:
                raise ValueError("process.run approval cwd must be non-empty and contain no NUL")
            if self.script_sha256 is not None and re.fullmatch(
                r"[0-9a-f]{64}", self.script_sha256
            ) is None:
                raise ValueError("process.run script_sha256 must be a lowercase SHA-256 digest")
            return self
        raise ValueError(f"unsupported approval tool: {self.tool_name}")


class _ApprovalDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grants: tuple[ApprovalGrant, ...]


FileApprovalKey = tuple[str, str]
ProcessApprovalBaseKey = tuple[str, tuple[str, ...], str]
ProcessApprovalKey = tuple[str, tuple[str, ...], str, str | None]
ApprovalKey = FileApprovalKey | ProcessApprovalKey
Clock = Callable[[], float]
Sleeper = Callable[[float], None]


def _canonical_path(visibility: PathVisibilityPolicy, path: str | Path) -> str:
    return os.path.normcase(str(visibility.resolve(path)))


def _canonical_relative_path(visibility: PathVisibilityPolicy, path: Path) -> str:
    resolved = visibility.resolve(path)
    relative = resolved.relative_to(visibility.workspace_root).as_posix()
    return relative.casefold() if os.name == "nt" else relative


def _canonical_process_argv(
    visibility: PathVisibilityPolicy,
    argv: tuple[str, ...],
    cwd: str,
) -> tuple[str, ...]:
    executable = os.path.normcase(str(Path(argv[0]).expanduser().resolve(strict=False)))
    canonical = [executable]
    resolved_cwd = visibility.resolve(cwd)
    for index, argument in enumerate(argv[1:], start=1):
        raw_path = Path(argument).expanduser()
        if index == 1 and argument.casefold().endswith(".py"):
            candidate = raw_path if raw_path.is_absolute() else resolved_cwd / raw_path
            canonical.append(_canonical_relative_path(visibility, candidate))
            continue
        if raw_path.is_absolute():
            canonical.append(_canonical_relative_path(visibility, raw_path))
            continue
        canonical.append(argument)
    return tuple(canonical)


def _file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class ApprovalManifest:
    def __init__(
        self,
        grants: tuple[ApprovalGrant, ...],
        visibility: PathVisibilityPolicy,
        *,
        manifest_path: Path,
        approval_wait_seconds: float = 0,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = time.sleep,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        if (
            isinstance(approval_wait_seconds, bool)
            or not isinstance(approval_wait_seconds, (int, float))
            or not 0 <= approval_wait_seconds <= 900
        ):
            raise ValueError("approval wait must be from 0 through 900 seconds")
        if (
            isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, (int, float))
            or poll_interval_seconds <= 0
        ):
            raise ValueError("approval poll interval must be positive")
        self._grants = grants
        self._visibility = visibility
        self._manifest_path = manifest_path
        self._approval_wait_seconds = float(approval_wait_seconds)
        self._clock = clock
        self._sleeper = sleeper
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._consumed: Counter[ApprovalKey] = Counter()
        self._request_path = visibility.workspace_root / ".phycode/prbench/approval-request.json"

    @classmethod
    def from_json(
        cls,
        path: Path,
        workspace_root: Path,
        *,
        approval_wait_seconds: float = 0,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = time.sleep,
        poll_interval_seconds: float = 0.05,
    ) -> ApprovalManifest:
        visibility = PathVisibilityPolicy(
            workspace_root,
            hidden_components=PRBENCH_HIDDEN_PATH_COMPONENTS,
        )
        manifest_path = visibility.resolve(path)
        grants = cls._read_grants(manifest_path, visibility)
        return cls(
            grants,
            visibility,
            manifest_path=manifest_path,
            approval_wait_seconds=approval_wait_seconds,
            clock=clock,
            sleeper=sleeper,
            poll_interval_seconds=poll_interval_seconds,
        )

    def __call__(self, call: ToolCall, decision: PolicyDecision) -> bool:
        if decision.decision != PolicyAction.ASK or decision.tool_call_id != call.id:
            return False
        if not self._cleanup_request():
            return False
        try:
            call_key = self._call_key(call)
            self._refresh()
        except Exception:
            self._cleanup_request()
            return False
        if call_key is None:
            return False
        if self._consume_matching(call_key):
            return True
        if call.tool_name != "process.run" or self._approval_wait_seconds == 0:
            return False

        try:
            request = self._approval_request(call_key)
            self._write_request(request)
        except Exception:
            self._cleanup_request()
            return False

        deadline = self._clock() + self._approval_wait_seconds
        try:
            while True:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return False
                self._sleeper(min(self._poll_interval_seconds, remaining))
                try:
                    self._refresh()
                except Exception:
                    return False
                if self._consume_matching(call_key, require_script_hash=True):
                    return self._cleanup_request()
        finally:
            self._cleanup_request()

    @staticmethod
    def _read_grants(
        manifest_path: Path,
        visibility: PathVisibilityPolicy,
    ) -> tuple[ApprovalGrant, ...]:
        payload: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
        document = _ApprovalDocument.model_validate(payload)
        return tuple(
            ApprovalManifest._canonicalize_grant(grant, visibility)
            for grant in document.grants
        )

    def _refresh(self) -> None:
        self._grants = self._read_grants(self._manifest_path, self._visibility)

    @staticmethod
    def _canonicalize_grant(
        grant: ApprovalGrant,
        visibility: PathVisibilityPolicy,
    ) -> ApprovalGrant:
        updates: dict[str, object] = {}
        if grant.path is not None:
            updates["path"] = _canonical_path(visibility, grant.path)
        if grant.cwd is not None:
            canonical_cwd = _canonical_path(visibility, grant.cwd)
            updates["cwd"] = canonical_cwd
            assert grant.argv is not None
            updates["argv"] = _canonical_process_argv(
                visibility,
                grant.argv,
                canonical_cwd,
            )
        return grant.model_copy(update=updates)

    def _call_key(self, call: ToolCall) -> FileApprovalKey | ProcessApprovalBaseKey | None:
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
            canonical_cwd = _canonical_path(self._visibility, cwd)
            return (
                call.tool_name,
                _canonical_process_argv(self._visibility, tuple(argv), canonical_cwd),
                canonical_cwd,
            )
        return None

    def _consume_matching(
        self,
        call_key: FileApprovalKey | ProcessApprovalBaseKey,
        *,
        require_script_hash: bool = False,
    ) -> bool:
        occurrences: Counter[ApprovalKey] = Counter()
        for grant in self._grants:
            grant_key = self._grant_key(grant)
            occurrences[grant_key] += 1
            if occurrences[grant_key] <= self._consumed[grant_key]:
                continue
            if grant.path is not None:
                if grant_key != call_key:
                    continue
            else:
                assert grant.argv is not None
                assert grant.cwd is not None
                base_key: ProcessApprovalBaseKey = (
                    grant.tool_name,
                    grant.argv,
                    grant.cwd,
                )
                if base_key != call_key:
                    continue
                if require_script_hash and grant.script_sha256 is None:
                    continue
                if grant.script_sha256 is not None:
                    snapshot = self._script_snapshot(call_key)
                    if snapshot is None or snapshot[1] != grant.script_sha256:
                        continue
            self._consumed[grant_key] += 1
            return True
        return False

    def _script_snapshot(
        self,
        call_key: FileApprovalKey | ProcessApprovalBaseKey,
    ) -> tuple[str, str] | None:
        if len(call_key) != 3:
            return None
        _tool_name, argv, _cwd = call_key
        if len(argv) < 2 or not argv[1].casefold().endswith(".py"):
            return None
        script_path = argv[1]
        try:
            resolved = self._visibility.resolve(script_path)
            if not resolved.is_file():
                return None
            return script_path, _file_sha256(resolved)
        except (OSError, RuntimeError, VisibilityViolation):
            return None

    def _approval_request(
        self,
        call_key: FileApprovalKey | ProcessApprovalBaseKey,
    ) -> dict[str, object]:
        snapshot = self._script_snapshot(call_key)
        if len(call_key) != 3 or snapshot is None:
            raise ValueError("process approval requires a visible Python script")
        tool_name, argv, canonical_cwd = call_key
        executable = Path(argv[0])
        try:
            executable.relative_to(self._visibility.workspace_root)
        except ValueError:
            pass
        else:
            raise ValueError("process approval request cannot disclose the workspace path")
        for argument in argv[1:]:
            if (
                is_sensitive_path(argument, PRBENCH_HIDDEN_PATH_COMPONENTS)
                or redact_text(argument) != argument
            ):
                raise ValueError("process approval request contains sensitive arguments")
        resolved_cwd = Path(canonical_cwd)
        relative_cwd = resolved_cwd.relative_to(self._visibility.workspace_root).as_posix()
        script_path, script_sha256 = snapshot
        script_from_cwd = Path(
            os.path.relpath(self._visibility.resolve(script_path), resolved_cwd)
        ).as_posix()
        public_argv = [*argv]
        public_argv[1] = script_from_cwd
        return {
            "tool_name": tool_name,
            "argv": public_argv,
            "cwd": relative_cwd or ".",
            "script_path": script_path,
            "script_sha256": script_sha256,
        }

    def _write_request(self, payload: dict[str, object]) -> None:
        request_parent = self._visibility.resolve(self._request_path.parent)
        request_parent.mkdir(parents=True, exist_ok=True)
        request_parent = self._visibility.resolve(request_parent)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=request_parent,
                prefix=".approval-request-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            temporary.replace(self._request_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _cleanup_request(self) -> bool:
        try:
            self._visibility.resolve(self._request_path)
            self._request_path.unlink(missing_ok=True)
        except (OSError, RuntimeError, VisibilityViolation):
            return False
        return True

    @staticmethod
    def _grant_key(grant: ApprovalGrant) -> ApprovalKey:
        if grant.path is not None:
            return grant.tool_name, grant.path
        assert grant.argv is not None
        assert grant.cwd is not None
        return grant.tool_name, grant.argv, grant.cwd, grant.script_sha256
