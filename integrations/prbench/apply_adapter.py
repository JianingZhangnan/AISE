from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path


EXPECTED_EVALUATOR_COMMIT = "3e5bee4545cad2138832f06302e9c98bd81f5216"
EXPECTED_WHEEL_FILENAME = "phycode-0.1.0-py3-none-any.whl"
PATCH_PATH = Path(__file__).with_name("phycode-evaluator.patch")


class AdapterError(RuntimeError):
    """A fixed, non-sensitive evaluator adapter failure."""


def _git(repository: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        operation = " ".join(arguments[:2])
        raise AdapterError(f"git {operation} failed") from None
    return result.stdout.strip()


def _resolve_repository(repository: Path) -> Path:
    try:
        resolved = repository.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise AdapterError("evaluator repository is unavailable") from None
    if not resolved.is_dir():
        raise AdapterError("evaluator repository is not a directory")
    return resolved


def _resolve_wheel(wheel: Path) -> Path:
    try:
        resolved = wheel.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise AdapterError("PhyCode wheel is unavailable") from None
    if not resolved.is_file() or resolved.suffix.casefold() != ".whl":
        raise AdapterError("PhyCode wheel must be an existing .whl file")
    if resolved.name != EXPECTED_WHEEL_FILENAME:
        raise AdapterError(
            "unexpected PhyCode wheel filename; expected " + EXPECTED_WHEEL_FILENAME
        )
    return resolved


def _checked_adapter_destination(repository: Path) -> Path:
    adapter_dir = repository / ".phycode-adapter"
    destination = adapter_dir / "phycode.whl"
    try:
        for candidate in (adapter_dir, destination):
            if candidate.exists() or candidate.is_symlink():
                candidate.resolve(strict=False).relative_to(repository)
    except (OSError, RuntimeError, ValueError):
        raise AdapterError("adapter destination escapes the evaluator repository") from None
    return destination


def apply_adapter(repo: Path, wheel: Path) -> None:
    """Apply the pinned evaluator patch and stage one local PhyCode wheel."""

    repository = _resolve_repository(repo)
    head = _git(repository, "rev-parse", "HEAD")
    if head != EXPECTED_EVALUATOR_COMMIT:
        raise AdapterError(
            "evaluator commit mismatch; expected " + EXPECTED_EVALUATOR_COMMIT
        )
    if _git(repository, "status", "--porcelain", "--untracked-files=no"):
        raise AdapterError("evaluator checkout contains tracked changes")

    wheel_path = _resolve_wheel(wheel)
    destination = _checked_adapter_destination(repository)
    try:
        patch_path = PATCH_PATH.resolve(strict=True)
    except (OSError, RuntimeError):
        raise AdapterError("versioned evaluator patch is unavailable") from None
    if not patch_path.is_file():
        raise AdapterError("versioned evaluator patch is not a file")

    adapter_dir_existed = destination.parent.exists()
    staged_wheel: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=".phycode-wheel-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            staged_wheel = Path(handle.name)
        shutil.copyfile(wheel_path, staged_wheel)
    except OSError:
        if staged_wheel is not None:
            staged_wheel.unlink(missing_ok=True)
        if not adapter_dir_existed:
            try:
                destination.parent.rmdir()
            except OSError:
                pass
        raise AdapterError("failed to stage the PhyCode wheel") from None

    try:
        _git(repository, "apply", "--check", "--unidiff-zero", str(patch_path))
        _git(repository, "apply", "--unidiff-zero", str(patch_path))
    except AdapterError:
        staged_wheel.unlink(missing_ok=True)
        if not adapter_dir_existed:
            try:
                destination.parent.rmdir()
            except OSError:
                pass
        raise

    try:
        staged_wheel.replace(destination)
    except OSError:
        staged_wheel.unlink(missing_ok=True)
        try:
            _git(
                repository,
                "apply",
                "--reverse",
                "--unidiff-zero",
                str(patch_path),
            )
        except AdapterError:
            raise AdapterError(
                "failed to publish the PhyCode wheel and reverse the patch"
            ) from None
        if not adapter_dir_existed:
            try:
                destination.parent.rmdir()
            except OSError:
                pass
        raise AdapterError("failed to publish the PhyCode wheel") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply the pinned PhyCode adapter to PRBench-Eval-Handson."
    )
    parser.add_argument("repo", type=Path, help="fixed-commit evaluator checkout")
    parser.add_argument("wheel", type=Path, help="local PhyCode wheel")
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        apply_adapter(args.repo, args.wheel)
    except AdapterError as error:
        raise SystemExit(f"adapter error: {error}") from None


if __name__ == "__main__":
    main()
