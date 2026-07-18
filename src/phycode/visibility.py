from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath


PRBENCH_HIDDEN_PATH_COMPONENTS = frozenset({"_ground_truth"})
_CREDENTIAL_COMPONENTS = frozenset(
    {".ssh", ".aws", ".env", ".env.local", "id_rsa", "id_ed25519", "credentials"}
)


class VisibilityViolation(ValueError):
    def __init__(self, message: str, *, hidden: bool = False) -> None:
        super().__init__(message)
        self.hidden = hidden


def _path_parts(path: str | Path) -> tuple[str, ...]:
    return tuple(part.casefold() for part in PurePosixPath(str(path).replace("\\", "/")).parts)


def has_hidden_path_component(path: str | Path, hidden_components: Iterable[str]) -> bool:
    hidden = frozenset(component.casefold() for component in hidden_components)
    return any(part in hidden for part in _path_parts(path))


def is_credential_path(path: str | Path) -> bool:
    for part in _path_parts(path):
        if part in _CREDENTIAL_COMPONENTS or part.startswith(".env."):
            return True
        if part.endswith((".pem", ".key")):
            return True
    return False


def is_sensitive_path(
    path: str | Path,
    hidden_components: Iterable[str] = (),
) -> bool:
    return is_credential_path(path) or has_hidden_path_component(path, hidden_components)


def normalize_public_relative_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(path)
    if (
        not path
        or "\x00" in path
        or posix_path.is_absolute()
        or windows_path.drive
        or windows_path.root
        or any(part in {"", ".", ".."} for part in posix_path.parts)
        or is_sensitive_path(normalized, PRBENCH_HIDDEN_PATH_COMPONENTS)
    ):
        raise VisibilityViolation("path must be a safe public workspace-relative path")
    return posix_path.as_posix()


@dataclass(frozen=True, init=False)
class PathVisibilityPolicy:
    workspace_root: Path
    allowlist: tuple[Path, ...]
    hidden_components: frozenset[str]

    def __init__(
        self,
        workspace_root: Path,
        allowlist: Iterable[Path] = (),
        hidden_components: frozenset[str] = frozenset(),
    ) -> None:
        object.__setattr__(self, "workspace_root", workspace_root.expanduser().resolve())
        object.__setattr__(self, "allowlist", tuple(path.expanduser().resolve() for path in allowlist))
        object.__setattr__(self, "hidden_components", frozenset(hidden_components))

    @property
    def allowed_roots(self) -> tuple[Path, ...]:
        return (self.workspace_root, *self.allowlist)

    def resolve(self, path: str | Path) -> Path:
        raw_path = Path(path).expanduser()
        self._reject_hidden_components(raw_path)
        candidate = raw_path if raw_path.is_absolute() else self.workspace_root / raw_path
        resolved = candidate.resolve(strict=False)
        self._reject_hidden_components(resolved)
        if not any(resolved == root or root in resolved.parents for root in self.allowed_roots):
            raise VisibilityViolation(f"path escapes visible roots: {path}")
        return resolved

    def is_visible(self, path: str | Path) -> bool:
        try:
            self.resolve(path)
        except (OSError, RuntimeError, VisibilityViolation):
            return False
        return True

    def _reject_hidden_components(self, path: Path) -> None:
        if has_hidden_path_component(path, self.hidden_components):
            raise VisibilityViolation(f"path contains a hidden component: {path}", hidden=True)
