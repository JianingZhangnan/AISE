from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, model_validator

from phycode.visibility import PathVisibilityPolicy


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


class ApprovalDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grants: tuple[ApprovalGrant, ...]


FileApprovalKey = tuple[str, str]
ProcessApprovalBaseKey = tuple[str, tuple[str, ...], str]
ProcessApprovalKey = tuple[str, tuple[str, ...], str, str | None]
ApprovalKey = FileApprovalKey | ProcessApprovalKey


def canonical_path(visibility: PathVisibilityPolicy, path: str | Path) -> str:
    return os.path.normcase(str(visibility.resolve(path)))


def canonical_process_argv(
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


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _canonical_relative_path(visibility: PathVisibilityPolicy, path: Path) -> str:
    resolved = visibility.resolve(path)
    relative = resolved.relative_to(visibility.workspace_root).as_posix()
    return relative.casefold() if os.name == "nt" else relative
