from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from phycode.redaction import redact_obj
from phycode.visibility import (
    PRBENCH_HIDDEN_PATH_COMPONENTS,
    VisibilityViolation,
    is_sensitive_path,
    normalize_public_relative_path,
)


class ExecutionJournalError(RuntimeError):
    """Raised when process provenance cannot be captured safely."""


class UnsafeWorkspaceFileError(RuntimeError):
    """Raised when a workspace file is not a direct regular file."""


class WorkspaceFileReadError(RuntimeError):
    """Raised when a workspace file cannot be read with a stable identity."""


@dataclass(frozen=True)
class WorkspaceFileContent:
    data: bytes | None
    size: int
    sha256: str


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(file_stat, "st_file_attributes", 0) & reparse_flag)


def _file_identity(file_stat: os.stat_result) -> tuple[int, int, int]:
    return file_stat.st_dev, file_stat.st_ino, stat.S_IFMT(file_stat.st_mode)


def _file_version(file_stat: os.stat_result) -> tuple[int, int]:
    return file_stat.st_size, file_stat.st_mtime_ns


def _inspect_regular_file(
    workspace_root: Path,
    path: Path,
) -> tuple[tuple[int, int, int], ...] | None:
    try:
        relative = path.relative_to(workspace_root)
    except ValueError as exc:
        raise UnsafeWorkspaceFileError("workspace file escapes the workspace") from exc
    if not relative.parts:
        raise UnsafeWorkspaceFileError("workspace file is not a regular file")

    identities: list[tuple[int, int, int]] = []
    current = workspace_root
    for index, component in enumerate(relative.parts):
        current /= component
        try:
            file_stat = current.lstat()
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise WorkspaceFileReadError("workspace file cannot be inspected") from exc
        if stat.S_ISLNK(file_stat.st_mode) or _is_reparse_point(file_stat):
            raise UnsafeWorkspaceFileError("workspace file contains a link or reparse point")
        is_final = index == len(relative.parts) - 1
        expected_type = stat.S_ISREG if is_final else stat.S_ISDIR
        if not expected_type(file_stat.st_mode):
            raise UnsafeWorkspaceFileError("workspace file is not a regular file")
        identities.append(_file_identity(file_stat))
    return tuple(identities)


def _read_stable_pass(
    descriptor: int,
    workspace_root: Path,
    path: Path,
    expected_identities: tuple[tuple[int, int, int], ...],
    capture_limit: int | None,
) -> tuple[str, int, bytes | None]:
    opened_before = os.fstat(descriptor)
    identities_before = _inspect_regular_file(workspace_root, path)
    if identities_before is None or identities_before != expected_identities:
        raise WorkspaceFileReadError("workspace file identity changed before reading")
    if not stat.S_ISREG(opened_before.st_mode):
        raise UnsafeWorkspaceFileError("workspace file is not a regular file")
    if _file_identity(opened_before) != expected_identities[-1]:
        raise WorkspaceFileReadError("workspace file identity changed before reading")

    hasher = hashlib.sha256()
    total = 0
    captured = bytearray() if capture_limit is not None else None
    while chunk := os.read(descriptor, 1024 * 1024):
        total += len(chunk)
        if capture_limit is not None and total > capture_limit:
            raise WorkspaceFileReadError("workspace file exceeds the safe capture limit")
        hasher.update(chunk)
        if captured is not None:
            captured.extend(chunk)

    opened_after = os.fstat(descriptor)
    identities_after = _inspect_regular_file(workspace_root, path)
    if identities_after is None or identities_after != expected_identities:
        raise WorkspaceFileReadError("workspace file identity changed while being read")
    if (
        _file_identity(opened_after) != _file_identity(opened_before)
        or _file_version(opened_after) != _file_version(opened_before)
        or total != opened_after.st_size
    ):
        raise WorkspaceFileReadError("workspace file changed while being read")
    return hasher.hexdigest(), total, bytes(captured) if captured is not None else None


def read_workspace_regular_file(
    workspace_root: Path,
    path: Path,
    *,
    capture_limit: int | None = None,
) -> WorkspaceFileContent | None:
    """Snapshot a regular file with two bounded stability checks on one descriptor.

    The repeated reads detect changes visible during these checks; no finite userspace
    check can lock a file against a malicious writer that synchronizes between them.
    """
    if capture_limit is not None and capture_limit < 0:
        raise ValueError("capture_limit must not be negative")
    initial_identities = _inspect_regular_file(workspace_root, path)
    if initial_identities is None:
        return None

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except (OSError, ValueError) as exc:
        raise WorkspaceFileReadError("workspace file cannot be read") from exc

    try:
        first_hash, first_size, data = _read_stable_pass(
            descriptor,
            workspace_root,
            path,
            initial_identities,
            capture_limit,
        )
        os.lseek(descriptor, 0, os.SEEK_SET)
        second_hash, second_size, _ = _read_stable_pass(
            descriptor,
            workspace_root,
            path,
            initial_identities,
            None,
        )
        if first_hash != second_hash or first_size != second_size:
            raise WorkspaceFileReadError("workspace file was not stable across bounded reads")
    except (UnsafeWorkspaceFileError, WorkspaceFileReadError):
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    except (OSError, ValueError) as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise WorkspaceFileReadError("workspace file cannot be read") from exc
    try:
        os.close(descriptor)
    except OSError as exc:
        raise WorkspaceFileReadError("workspace file cannot be closed safely") from exc

    return WorkspaceFileContent(
        data=data,
        size=first_size,
        sha256=first_hash,
    )


class ArtifactSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    exists: bool
    size: int | None = None
    sha256: str | None = None


class ProcessExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str
    argv: tuple[str, ...]
    cwd: str
    script_path: str | None
    script_sha256: str | None
    started_at: datetime
    ended_at: datetime
    exit_code: int | None
    status: str
    artifacts_before: tuple[ArtifactSnapshot, ...]
    artifacts_after: tuple[ArtifactSnapshot, ...]
    changed_artifacts: tuple[str, ...]


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class ExecutionJournal:
    def __init__(self, workspace_root: Path, artifact_paths: tuple[str, ...]) -> None:
        self.workspace_root = workspace_root.expanduser().resolve()
        self._artifact_paths = self._normalize_artifact_paths(artifact_paths)
        self.records: list[ProcessExecutionRecord] = []
        self._journal_path = self._resolve_workspace_path(".phycode/prbench/execution.jsonl")

    def snapshot_artifacts(self) -> tuple[ArtifactSnapshot, ...]:
        return tuple(self._snapshot(relative_path) for relative_path in self._artifact_paths)

    def validate_cwd(self, cwd: Path) -> None:
        self._resolve_workspace_path(cwd)

    def snapshot_script(self, argv: tuple[str, ...], cwd: Path) -> tuple[str | None, str | None]:
        if len(argv) < 2 or not argv[1].casefold().endswith(".py"):
            return None, None
        script = self._lexical_workspace_path(argv[1], base=cwd)
        relative_path = script.relative_to(self.workspace_root).as_posix()
        try:
            content = read_workspace_regular_file(self.workspace_root, script)
        except UnsafeWorkspaceFileError as exc:
            raise ExecutionJournalError("tracked script is not a regular file") from exc
        except WorkspaceFileReadError as exc:
            raise ExecutionJournalError("tracked script cannot be snapshotted") from exc
        if content is None:
            return relative_path, None
        return relative_path, content.sha256

    def record_process(
        self,
        *,
        call_id: str,
        argv: tuple[str, ...],
        cwd: Path,
        status: str,
        exit_code: int | None,
        started_at: datetime,
        artifacts_before: tuple[ArtifactSnapshot, ...],
        script_path: str | None,
        script_sha256: str | None,
    ) -> ProcessExecutionRecord:
        artifacts_after = self.snapshot_artifacts()
        before_by_path = {snapshot.path: snapshot for snapshot in artifacts_before}
        changed = tuple(
            snapshot.path
            for snapshot in artifacts_after
            if snapshot.exists
            and (
                not before_by_path[snapshot.path].exists
                or snapshot.sha256 != before_by_path[snapshot.path].sha256
            )
        )
        record = ProcessExecutionRecord(
            call_id=call_id,
            argv=self._sanitize_argv(argv, script_path),
            cwd=self._relative_directory(cwd),
            script_path=script_path,
            script_sha256=script_sha256,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc),
            exit_code=exit_code,
            status=status,
            artifacts_before=artifacts_before,
            artifacts_after=artifacts_after,
            changed_artifacts=changed,
        )
        self._append(record)
        self.records.append(record)
        return record

    def _normalize_artifact_paths(self, artifact_paths: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        seen: set[str] = set()
        for artifact_path in artifact_paths:
            try:
                normalized_path = normalize_public_relative_path(artifact_path)
            except VisibilityViolation as exc:
                raise ExecutionJournalError("tracked artifact path must be workspace-relative") from exc
            key = os.path.normcase(normalized_path)
            if key not in seen:
                normalized.append(normalized_path)
                seen.add(key)
        return tuple(normalized)

    def _snapshot(self, relative_path: str) -> ArtifactSnapshot:
        path = self._lexical_workspace_path(relative_path)
        try:
            content = read_workspace_regular_file(self.workspace_root, path)
        except UnsafeWorkspaceFileError as exc:
            raise ExecutionJournalError("tracked artifact is not a regular file") from exc
        except WorkspaceFileReadError as exc:
            raise ExecutionJournalError("tracked artifact cannot be snapshotted") from exc
        if content is None:
            return ArtifactSnapshot(path=relative_path, exists=False)
        return ArtifactSnapshot(
            path=relative_path,
            exists=True,
            size=content.size,
            sha256=content.sha256,
        )

    def _lexical_workspace_path(self, path: str | Path, *, base: Path | None = None) -> Path:
        raw = str(path)
        if not raw or "\x00" in raw or is_sensitive_path(raw, PRBENCH_HIDDEN_PATH_COMPONENTS):
            raise ExecutionJournalError("unsafe journal path")
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = (base if base is not None else self.workspace_root) / candidate
        try:
            lexical = Path(os.path.abspath(candidate))
            relative_path = lexical.relative_to(self.workspace_root).as_posix()
        except (OSError, RuntimeError, ValueError) as exc:
            raise ExecutionJournalError("journal path escapes the workspace") from exc
        if is_sensitive_path(relative_path, PRBENCH_HIDDEN_PATH_COMPONENTS):
            raise ExecutionJournalError("unsafe journal path")
        return lexical

    def _resolve_workspace_path(self, path: str | Path, *, base: Path | None = None) -> Path:
        raw = str(path)
        if not raw or "\x00" in raw or is_sensitive_path(raw, PRBENCH_HIDDEN_PATH_COMPONENTS):
            raise ExecutionJournalError("unsafe journal path")
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = (base if base is not None else self.workspace_root) / candidate
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(self.workspace_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ExecutionJournalError("journal path escapes the workspace") from exc
        relative_path = resolved.relative_to(self.workspace_root).as_posix()
        if is_sensitive_path(relative_path, PRBENCH_HIDDEN_PATH_COMPONENTS):
            raise ExecutionJournalError("unsafe journal path")
        return resolved

    def _relative_directory(self, cwd: Path) -> str:
        resolved = self._resolve_workspace_path(cwd)
        relative = resolved.relative_to(self.workspace_root).as_posix()
        return relative or "."

    def _sanitize_argv(
        self,
        argv: tuple[str, ...],
        script_path: str | None,
    ) -> tuple[str, ...]:
        sanitized = [Path(argv[0]).name]
        for index, _argument in enumerate(argv[1:], start=1):
            if index == 1 and script_path is not None:
                sanitized.append(script_path)
                continue
            sanitized.append("[REDACTED_ARG]")
        return tuple(sanitized)

    def _append(self, record: ProcessExecutionRecord) -> None:
        journal_path = self._resolve_workspace_path(self._journal_path)
        try:
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            journal_path = self._resolve_workspace_path(journal_path)
            payload = redact_obj(record.model_dump(mode="json"))
            with journal_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError as exc:
            raise ExecutionJournalError("execution journal cannot be written") from exc
