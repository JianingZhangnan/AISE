from __future__ import annotations

import csv
import io
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from phycode.execution import (
    ExecutionJournal,
    ProcessExecutionRecord,
    UnsafeWorkspaceFileError,
    WorkspaceFileContent,
    WorkspaceFileReadError,
    read_workspace_regular_file,
)
from phycode.visibility import normalize_public_relative_path


def _path_key(path: str) -> str:
    return os.path.normcase(path.replace("\\", "/"))


class ArtifactConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    csv_header: tuple[str, ...] | None = None
    csv_rows: tuple[tuple[str, ...], ...] | None = None
    csv_data_row_count: int | None = Field(default=None, ge=0)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_public_relative_path(value)


class TaskContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instruction_file: str
    paper_file: str
    input_files: tuple[str, ...] = ()
    expected_files: tuple[str, ...]
    execution_entrypoints: tuple[str, ...] = ()
    constraints: tuple[ArtifactConstraint, ...] = ()

    @field_validator("instruction_file", "paper_file")
    @classmethod
    def validate_single_path(cls, value: str) -> str:
        return normalize_public_relative_path(value)

    @field_validator("input_files", "expected_files", "execution_entrypoints")
    @classmethod
    def validate_path_list(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(normalize_public_relative_path(value) for value in values)

    @model_validator(mode="after")
    def validate_constraint_paths(self) -> TaskContract:
        expected_keys = tuple(_path_key(path) for path in self.expected_files)
        if len(set(expected_keys)) != len(expected_keys):
            raise ValueError("expected_files contains duplicate paths")
        expected_key_set = set(expected_keys)
        entrypoint_keys = tuple(_path_key(path) for path in self.execution_entrypoints)
        if len(set(entrypoint_keys)) != len(entrypoint_keys):
            raise ValueError("execution_entrypoints contains duplicate paths")
        if any(key not in expected_key_set for key in entrypoint_keys):
            raise ValueError("execution entrypoints must belong to expected_files")
        if any(Path(path).suffix.casefold() != ".py" for path in self.execution_entrypoints):
            raise ValueError("execution entrypoints must be Python files")
        constraint_keys = tuple(_path_key(constraint.path) for constraint in self.constraints)
        if len(set(constraint_keys)) != len(constraint_keys):
            raise ValueError("constraints contains duplicate paths")
        if any(key not in expected_key_set for key in constraint_keys):
            raise ValueError("constraint paths must belong to expected_files")
        return self


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
        contents: dict[str, WorkspaceFileContent] = {}
        entrypoint_keys = {_path_key(path) for path in self.contract.execution_entrypoints}
        for relative_path in self.contract.expected_files:
            path = self._resolve(relative_path)
            if path is None:
                issues.append(self._issue("invalid_artifact_path", relative_path, "Artifact path is not visible"))
                continue
            try:
                content = read_workspace_regular_file(self.workspace_root, path)
            except UnsafeWorkspaceFileError:
                issues.append(self._issue("invalid_artifact_path", relative_path, "Artifact path is not visible"))
                continue
            except WorkspaceFileReadError:
                issues.append(self._issue("artifact_read_error", relative_path, "Artifact cannot be read safely"))
                continue
            if content is None:
                issues.append(self._issue("missing_artifact", relative_path, "Required artifact is missing"))
                continue
            contents[self._key(relative_path)] = content
            if content.size == 0:
                issues.append(self._issue("empty_artifact", relative_path, "Required artifact is empty"))

        for relative_path in self.contract.expected_files:
            content = contents.get(self._key(relative_path))
            if content is None:
                continue
            if self._key(relative_path) in entrypoint_keys and not self._script_has_provenance(
                relative_path,
                content.sha256,
            ):
                issues.append(
                    self._issue(
                        "script_not_executed",
                        relative_path,
                        "Entrypoint was not executed successfully",
                    )
                )
            if Path(relative_path).suffix.casefold() == ".csv" and not self._csv_has_provenance(
                relative_path,
                content.sha256,
                contents,
            ):
                issues.append(
                    self._issue(
                        "csv_without_provenance",
                        relative_path,
                        "CSV has no successful execution provenance",
                    )
                )

        for constraint in self.contract.constraints:
            content = contents.get(self._key(constraint.path))
            if content is None or content.size == 0:
                continue
            issues.extend(self._verify_csv_constraint(content.data, constraint))

        return VerificationResult(ok=not issues, issues=tuple(issues))

    def _script_has_provenance(self, relative_path: str, current_hash: str) -> bool:
        key = self._key(relative_path)
        return any(
            record.status == "ok"
            and record.script_path is not None
            and self._key(record.script_path) == key
            and record.script_sha256 == current_hash
            for record in self.journal.records
        )

    def _csv_has_provenance(
        self,
        relative_path: str,
        current_hash: str,
        contents: dict[str, WorkspaceFileContent],
    ) -> bool:
        key = self._key(relative_path)
        expected_scripts = self._current_entrypoint_hashes(contents)
        for record in self.journal.records:
            if (
                record.status != "ok"
                or not record.script_path
                or not record.script_sha256
                or expected_scripts.get(self._key(record.script_path)) != record.script_sha256
                or not any(self._key(item) == key for item in record.changed_artifacts)
            ):
                continue
            after = self._artifact_after(record, key)
            if after is not None and after.sha256 == current_hash:
                return True
        return False

    def _current_entrypoint_hashes(
        self,
        contents: dict[str, WorkspaceFileContent],
    ) -> dict[str, str]:
        scripts: dict[str, str] = {}
        for relative_path in self.contract.execution_entrypoints:
            content = contents.get(self._key(relative_path))
            if content is not None:
                scripts[self._key(relative_path)] = content.sha256
        return scripts

    def _verify_csv_constraint(
        self,
        data: bytes,
        constraint: ArtifactConstraint,
    ) -> list[VerificationIssue]:
        try:
            rows = list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))
        except (UnicodeError, csv.Error):
            return [self._issue("csv_read_error", constraint.path, "CSV cannot be read")]
        issues: list[VerificationIssue] = []
        if constraint.csv_header is not None and (not rows or tuple(rows[0]) != constraint.csv_header):
            issues.append(self._issue("csv_header_mismatch", constraint.path, "CSV header does not match"))
        actual_rows = tuple(tuple(row) for row in rows[1:]) if rows else ()
        if constraint.csv_rows is not None and actual_rows != constraint.csv_rows:
            issues.append(self._issue("csv_rows_mismatch", constraint.path, "CSV rows do not match"))
        if constraint.csv_data_row_count is not None and len(actual_rows) != constraint.csv_data_row_count:
            issues.append(
                self._issue(
                    "csv_row_count_mismatch",
                    constraint.path,
                    "CSV data row count does not match",
                )
            )
        return issues

    def _resolve(self, relative_path: str) -> Path | None:
        try:
            normalized = normalize_public_relative_path(relative_path)
            path = Path(os.path.abspath(self.workspace_root / normalized))
            path.relative_to(self.workspace_root)
            normalize_public_relative_path(path.relative_to(self.workspace_root).as_posix())
        except (OSError, RuntimeError, ValueError):
            return None
        return path

    @staticmethod
    def _artifact_after(record: ProcessExecutionRecord, key: str):
        return next((snapshot for snapshot in record.artifacts_after if ArtifactVerifier._key(snapshot.path) == key), None)

    @staticmethod
    def _key(path: str) -> str:
        return _path_key(path)

    @staticmethod
    def _issue(code: str, path: str, message: str) -> VerificationIssue:
        return VerificationIssue(code=code, path=path, message=message)
