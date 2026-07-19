from __future__ import annotations

import os
import re
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from phycode.approval_store import ApprovalRequestStore, read_approval_document
from phycode.approval_types import (
    ApprovalDocument,
    ApprovalGrant,
    ApprovalKey,
    FileApprovalKey,
    ProcessApprovalBaseKey,
    canonical_path,
    canonical_process_argv,
    file_sha256,
)
from phycode.models import PolicyAction, PolicyDecision, ToolCall
from phycode.redaction import redact_text
from phycode.visibility import (
    PRBENCH_HIDDEN_PATH_COMPONENTS,
    PathVisibilityPolicy,
    VisibilityViolation,
    is_sensitive_path,
)

Clock = Callable[[], float]
Sleeper = Callable[[float], None]


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
        self._execution_expectations: dict[
            str,
            tuple[ProcessApprovalBaseKey, str | None],
        ] = {}
        self._request_store = ApprovalRequestStore(visibility)
        self._request_path = self._request_store.path

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
        raw_manifest_path = Path(path).expanduser()
        manifest_path = Path(
            os.path.abspath(
                raw_manifest_path
                if raw_manifest_path.is_absolute()
                else visibility.workspace_root / raw_manifest_path
            )
        )
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
        except Exception:
            self._cleanup_request()
            return False
        if call_key is None:
            return False
        if call.tool_name != "process.run" or self._approval_wait_seconds == 0:
            return self._consume_matching(call_key, call_id=call.id)

        try:
            request = self._approval_request(call_key)
            self._refresh()
        except Exception:
            return False
        if self._consume_matching(
            call_key,
            call_id=call.id,
            require_script_hash=True,
        ):
            return True
        try:
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
                    continue
                if self._consume_matching(
                    call_key,
                    call_id=call.id,
                    require_script_hash=True,
                ):
                    return self._cleanup_request()
        finally:
            self._cleanup_request()

    @staticmethod
    def _read_grants(
        manifest_path: Path,
        visibility: PathVisibilityPolicy,
    ) -> tuple[ApprovalGrant, ...]:
        payload = read_approval_document(visibility, manifest_path)
        document = ApprovalDocument.model_validate(payload)
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
            updates["path"] = canonical_path(visibility, grant.path)
        if grant.cwd is not None:
            canonical_cwd = canonical_path(visibility, grant.cwd)
            updates["cwd"] = canonical_cwd
            assert grant.argv is not None
            updates["argv"] = canonical_process_argv(
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
            return call.tool_name, canonical_path(self._visibility, path)
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
            return call.tool_name, canonical_path(self._visibility, path)
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
            canonical_cwd = canonical_path(self._visibility, cwd)
            return (
                call.tool_name,
                canonical_process_argv(self._visibility, tuple(argv), canonical_cwd),
                canonical_cwd,
            )
        return None

    def _consume_matching(
        self,
        call_key: FileApprovalKey | ProcessApprovalBaseKey,
        *,
        call_id: str,
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
            if grant.path is None:
                assert len(call_key) == 3
                self._execution_expectations[call_id] = (
                    call_key,
                    grant.script_sha256,
                )
            return True
        return False

    def validate_execution(self, call: ToolCall) -> bool:
        expectation = self._execution_expectations.pop(call.id, None)
        if expectation is None:
            return False
        expected_key, expected_hash = expectation
        try:
            call_key = self._call_key(call)
        except Exception:
            return False
        if call_key is None or call_key != expected_key:
            return False
        if expected_hash is None:
            return self._approval_wait_seconds == 0
        snapshot = self._script_snapshot(call_key)
        return snapshot is not None and snapshot[1] == expected_hash

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
            return script_path, file_sha256(resolved)
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
        if len(argv) != 2 or re.fullmatch(
            r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?",
            Path(argv[0]).name.casefold(),
        ) is None:
            raise ValueError("dynamic approval requires an exact Python script invocation")
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
        self._request_store.path = self._request_path
        self._request_store.write(payload)

    def _cleanup_request(self) -> bool:
        self._request_store.path = self._request_path
        return self._request_store.cleanup()

    @staticmethod
    def _grant_key(grant: ApprovalGrant) -> ApprovalKey:
        if grant.path is not None:
            return grant.tool_name, grant.path
        assert grant.argv is not None
        assert grant.cwd is not None
        return grant.tool_name, grant.argv, grant.cwd, grant.script_sha256
