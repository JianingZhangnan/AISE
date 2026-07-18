from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


class VisibilityViolation(ValueError):
    def __init__(self, message: str, *, hidden: bool = False) -> None:
        super().__init__(message)
        self.hidden = hidden


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
        if any(part in self.hidden_components for part in path.parts):
            raise VisibilityViolation(f"path contains a hidden component: {path}", hidden=True)
