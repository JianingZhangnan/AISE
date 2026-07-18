from __future__ import annotations

import csv
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import BaseModel, ConfigDict, field_validator

from phycode.execution import ExecutionJournal, ProcessExecutionRecord


_HIDDEN_COMPONENTS = frozenset({"_ground_truth", ".ssh", ".aws"})
_CREDENTIAL_NAMES = frozenset({".env", ".env.local", "id_rsa", "id_ed25519", "credentials"})


def _public_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    windows = PureWindowsPath(path)
    parts = tuple(part.casefold() for part in pure.parts)
    if (
        not path
        or "\x00" in path
        or pure.is_absolute()
        or pure.drive
        or windows.drive
        or windows.root
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(part in _HIDDEN_COMPONENTS for part in parts)
        or any(
            part in _CREDENTIAL_NAMES or part.startswith(".env.") or part.endswith((".pem", ".key"))
            for part in parts
        )
    ):
        raise ValueError("path must be a safe public workspace-relative path")
    return pure.as_posix()


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class ArtifactConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    csv_header: tuple[str, ...] | None = None
    csv_rows: tuple[tuple[str, ...], ...] | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _public_path(value)


class TaskContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instruction_file: str
    paper_file: str
    input_files: tuple[str, ...] = ()
    expected_files: tuple[str, ...]
    constraints: tuple[ArtifactConstraint, ...] = ()

    @field_validator("instruction_file", "paper_file")
    @classmethod
    def validate_single_path(cls, value: str) -> str:
        return _public_path(value)

    @field_validator("input_files", "expected_files")
    @classmethod
    def validate_path_list(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_public_path(value) for value in values)


class VerificationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    path: str
    message: str


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    issues: tuple[VerificationIssue, ...] = ()


class ArtifactVerifier:
    def __init__(self, workspace_root: Path, contract: TaskContract, journal: ExecutionJournal) -> None:
        self.workspace_root = workspace_root.expanduser().resolve()
        if journal.workspace_root != self.workspace_root:
            raise ValueError("journal and verifier must use the same workspace")
        self.contract = contract
        self.journal = journal

    def verify(self) -> VerificationResult:
        issues: list[VerificationIssue] = []
        paths: dict[str, Path] = {}
        for relative_path in self.contract.expected_files:
            path = self._resolve(relative_path)
            if path is None:
                issues.append(self._issue("invalid_artifact_path", relative_path, "Artifact path is not visible"))
                continue
            paths[self._key(relative_path)] = path
            try:
                if not path.is_file():
                    code = "script_not_executed" if path.suffix.casefold() == ".py" else "missing_artifact"
                    issues.append(self._issue(code, relative_path, "Required artifact is missing"))
                    continue
                if path.stat().st_size == 0:
                    issues.append(self._issue("empty_artifact", relative_path, "Required artifact is empty"))
                if path.suffix.casefold() == ".py" and not self._script_has_provenance(relative_path, path):
                    issues.append(self._issue("script_not_executed", relative_path, "Script was not executed successfully"))
                if path.suffix.casefold() == ".csv" and not self._csv_has_provenance(relative_path, path):
                    issues.append(
                        self._issue(
                            "csv_without_provenance",
                            relative_path,
                            "CSV has no successful execution provenance",
                        )
                    )
            except (OSError, RuntimeError):
                issues.append(self._issue("artifact_read_error", relative_path, "Artifact cannot be read safely"))

        for constraint in self.contract.constraints:
            path = paths.get(self._key(constraint.path))
            if path is None:
                path = self._resolve(constraint.path)
            try:
                usable = path is not None and path.is_file() and path.stat().st_size > 0
            except (OSError, RuntimeError):
                usable = False
            if not usable:
                continue
            assert path is not None
            issues.extend(self._verify_csv_constraint(path, constraint))

        return VerificationResult(ok=not issues, issues=tuple(issues))

    def _script_has_provenance(self, relative_path: str, path: Path) -> bool:
        current_hash = _sha256(path)
        key = self._key(relative_path)
        return any(
            record.status == "ok"
            and record.script_path is not None
            and self._key(record.script_path) == key
            and record.script_sha256 == current_hash
            for record in self.journal.records
        )

    def _csv_has_provenance(self, relative_path: str, path: Path) -> bool:
        current_hash = _sha256(path)
        key = self._key(relative_path)
        for record in self.journal.records:
            if (
                record.status != "ok"
                or not record.script_path
                or not record.script_sha256
                or not any(self._key(item) == key for item in record.changed_artifacts)
            ):
                continue
            after = self._artifact_after(record, key)
            if after is not None and after.sha256 == current_hash:
                return True
        return False

    def _verify_csv_constraint(
        self,
        path: Path,
        constraint: ArtifactConstraint,
    ) -> list[VerificationIssue]:
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
        except (OSError, UnicodeError, csv.Error):
            return [self._issue("csv_read_error", constraint.path, "CSV cannot be read")]
        issues: list[VerificationIssue] = []
        if constraint.csv_header is not None and (not rows or tuple(rows[0]) != constraint.csv_header):
            issues.append(self._issue("csv_header_mismatch", constraint.path, "CSV header does not match"))
        actual_rows = tuple(tuple(row) for row in rows[1:]) if rows else ()
        if constraint.csv_rows is not None and actual_rows != constraint.csv_rows:
            issues.append(self._issue("csv_rows_mismatch", constraint.path, "CSV rows do not match"))
        return issues

    def _resolve(self, relative_path: str) -> Path | None:
        try:
            path = (self.workspace_root / relative_path).resolve(strict=False)
            path.relative_to(self.workspace_root)
            _public_path(path.relative_to(self.workspace_root).as_posix())
        except (OSError, RuntimeError, ValueError):
            return None
        return path

    @staticmethod
    def _artifact_after(record: ProcessExecutionRecord, key: str):
        return next((snapshot for snapshot in record.artifacts_after if ArtifactVerifier._key(snapshot.path) == key), None)

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(path.replace("\\", "/"))

    @staticmethod
    def _issue(code: str, path: str, message: str) -> VerificationIssue:
        return VerificationIssue(code=code, path=path, message=message)
