from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from phycode.visibility import PathVisibilityPolicy, VisibilityViolation


class ApprovalStoreError(RuntimeError):
    """Raised when an approval control file cannot be accessed without path ambiguity."""


def _lexical_workspace_path(visibility: PathVisibilityPolicy, path: str | Path) -> Path:
    raw = Path(path).expanduser()
    candidate = raw if raw.is_absolute() else visibility.workspace_root / raw
    lexical = Path(os.path.abspath(candidate))
    try:
        lexical.relative_to(visibility.workspace_root)
    except ValueError as exc:
        raise ApprovalStoreError("approval control path escapes the workspace") from exc
    return lexical


def _reject_symlink_components(visibility: PathVisibilityPolicy, path: Path) -> None:
    lexical = _lexical_workspace_path(visibility, path)
    current = visibility.workspace_root
    for part in lexical.relative_to(visibility.workspace_root).parts:
        current /= part
        try:
            if current.is_symlink():
                raise ApprovalStoreError("approval control path cannot contain symlinks")
        except OSError as exc:
            raise ApprovalStoreError("approval control path cannot be inspected") from exc


def _validate_safe_path(
    visibility: PathVisibilityPolicy,
    path: Path,
    *,
    require_file: bool,
) -> Path:
    lexical = _lexical_workspace_path(visibility, path)
    _reject_symlink_components(visibility, lexical)
    try:
        resolved = visibility.resolve(lexical)
    except (OSError, RuntimeError, VisibilityViolation) as exc:
        raise ApprovalStoreError("approval control path is not visible") from exc
    if os.path.normcase(str(resolved)) != os.path.normcase(str(lexical)):
        raise ApprovalStoreError("approval control path changed during validation")
    if require_file and not lexical.is_file():
        raise ApprovalStoreError("approval control file is unavailable")
    return lexical


def read_approval_document(
    visibility: PathVisibilityPolicy,
    path: Path,
) -> Any:
    lexical = _validate_safe_path(visibility, path, require_file=True)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lexical, flags)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ApprovalStoreError("approval control file is not regular")
            return json.load(handle)
    except ApprovalStoreError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApprovalStoreError("approval control file cannot be read") from exc


class ApprovalRequestStore:
    def __init__(self, visibility: PathVisibilityPolicy) -> None:
        self._visibility = visibility
        self.path = _lexical_workspace_path(
            visibility,
            visibility.workspace_root / ".phycode/prbench/approval-request.json",
        )

    def write(self, payload: dict[str, object]) -> None:
        parent = self.path.parent
        self._ensure_parent(parent)
        self._validate_destination()
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=parent,
                prefix=".approval-request-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            _validate_safe_path(self._visibility, parent, require_file=False)
            self._validate_destination()
            os.replace(temporary, self.path)
            temporary = None
            _validate_safe_path(self._visibility, self.path, require_file=True)
        except ApprovalStoreError:
            raise
        except OSError as exc:
            raise ApprovalStoreError("approval request cannot be written") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def cleanup(self) -> bool:
        try:
            _reject_symlink_components(self._visibility, self.path.parent)
            if not self.path.parent.exists():
                return True
            _validate_safe_path(self._visibility, self.path.parent, require_file=False)
            if self.path.is_symlink():
                return False
            if not self.path.exists():
                return True
            _validate_safe_path(self._visibility, self.path, require_file=True)
            self.path.unlink()
        except (OSError, ApprovalStoreError):
            return False
        return True

    def _ensure_parent(self, parent: Path) -> None:
        current = self._visibility.workspace_root
        for part in parent.relative_to(self._visibility.workspace_root).parts:
            current /= part
            try:
                if current.is_symlink():
                    raise ApprovalStoreError("approval runtime directory cannot be a symlink")
                current.mkdir(exist_ok=True)
            except ApprovalStoreError:
                raise
            except OSError as exc:
                raise ApprovalStoreError("approval runtime directory cannot be created") from exc
            _validate_safe_path(self._visibility, current, require_file=False)

    def _validate_destination(self) -> None:
        if self.path.is_symlink():
            raise ApprovalStoreError("approval request destination cannot be a symlink")
        if self.path.exists():
            _validate_safe_path(self._visibility, self.path, require_file=True)
