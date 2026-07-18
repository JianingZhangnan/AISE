from __future__ import annotations

import hashlib
import json
import os
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
        script = self._resolve_workspace_path(argv[1], base=cwd)
        relative_path = script.relative_to(self.workspace_root).as_posix()
        if not script.is_file():
            return relative_path, None
        return relative_path, file_sha256(script)

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
            resolved = self._resolve_workspace_path(normalized_path)
            relative_path = resolved.relative_to(self.workspace_root).as_posix()
            key = os.path.normcase(relative_path)
            if key not in seen:
                normalized.append(relative_path)
                seen.add(key)
        return tuple(normalized)

    def _snapshot(self, relative_path: str) -> ArtifactSnapshot:
        path = self._resolve_workspace_path(relative_path)
        if not path.exists():
            return ArtifactSnapshot(path=relative_path, exists=False)
        if not path.is_file():
            raise ExecutionJournalError("tracked artifact is not a regular file")
        try:
            return ArtifactSnapshot(
                path=relative_path,
                exists=True,
                size=path.stat().st_size,
                sha256=file_sha256(path),
            )
        except OSError as exc:
            raise ExecutionJournalError("tracked artifact cannot be snapshotted") from exc

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
