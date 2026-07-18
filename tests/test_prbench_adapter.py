from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# `integrations/` is intentionally not part of the distributable PhyCode wheel.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import integrations.prbench.apply_adapter as adapter_module
from integrations.prbench.apply_adapter import (
    EXPECTED_EVALUATOR_COMMIT,
    AdapterError,
    apply_adapter,
)
from phycode.prbench_contract import TaskContract


def _commit_file(repository: Path, path: str, content: str) -> str:
    subprocess.run(
        ["git", "init"], cwd=repository, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repository, check=True
    )
    (repository / path).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", path], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-m", "fixture"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_adapter_rejects_wrong_evaluator_commit(tmp_path: Path) -> None:
    _commit_file(tmp_path, "README.md", "wrong")

    with pytest.raises(AdapterError, match=EXPECTED_EVALUATOR_COMMIT):
        apply_adapter(tmp_path, tmp_path / "phycode.whl")

    assert not (tmp_path / ".phycode-adapter").exists()


def test_expected_evaluator_commit_is_fixed() -> None:
    assert EXPECTED_EVALUATOR_COMMIT == "3e5bee4545cad2138832f06302e9c98bd81f5216"


def test_adapter_checks_wheel_before_changing_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = _commit_file(tmp_path, "README.md", "base\n")
    monkeypatch.setattr(adapter_module, "EXPECTED_EVALUATOR_COMMIT", head)

    with pytest.raises(AdapterError, match="wheel"):
        apply_adapter(tmp_path, tmp_path / "missing.whl")

    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""


def test_adapter_rejects_non_pep427_wheel_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = _commit_file(tmp_path, "README.md", "base\n")
    invalid_wheel = tmp_path / "phycode.whl"
    invalid_wheel.write_bytes(b"wheel")
    monkeypatch.setattr(adapter_module, "EXPECTED_EVALUATOR_COMMIT", head)

    with pytest.raises(AdapterError, match="filename"):
        apply_adapter(tmp_path, invalid_wheel)

    assert not (tmp_path / ".phycode-adapter").exists()


def test_adapter_rejects_tracked_changes_at_expected_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = _commit_file(tmp_path, "README.md", "base\n")
    (tmp_path / "README.md").write_text("locally changed\n", encoding="utf-8")
    wheel = tmp_path / "phycode-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    monkeypatch.setattr(adapter_module, "EXPECTED_EVALUATOR_COMMIT", head)

    with pytest.raises(AdapterError, match="tracked changes"):
        apply_adapter(tmp_path, wheel)

    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "locally changed\n"
    assert not (tmp_path / ".phycode-adapter").exists()


def test_adapter_checks_applies_and_copies_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "evaluator"
    repository.mkdir()
    head = _commit_file(repository, "README.md", "base\n")
    patch_path = tmp_path / "adapter.patch"
    patch_path.write_text(
        "diff --git a/README.md b/README.md\n"
        "index df967b9..67be85f 100644\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1,2 @@\n"
        " base\n"
        "+adapted\n",
        encoding="utf-8",
    )
    wheel = tmp_path / "phycode-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")
    monkeypatch.setattr(adapter_module, "EXPECTED_EVALUATOR_COMMIT", head)
    monkeypatch.setattr(adapter_module, "PATCH_PATH", patch_path)

    apply_adapter(repository, wheel)

    assert (repository / "README.md").read_text(encoding="utf-8") == "base\nadapted\n"
    assert (repository / ".phycode-adapter/phycode.whl").read_bytes() == b"wheel-bytes"


def test_adapter_subprocess_errors_are_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "evaluator"
    repository.mkdir()
    head = _commit_file(repository, "README.md", "base\n")
    secret = "sensitive-provider-value"
    invalid_patch = tmp_path / "invalid.patch"
    invalid_patch.write_text(secret, encoding="utf-8")
    wheel = tmp_path / "phycode-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    monkeypatch.setattr(adapter_module, "EXPECTED_EVALUATOR_COMMIT", head)
    monkeypatch.setattr(adapter_module, "PATCH_PATH", invalid_patch)

    with pytest.raises(AdapterError) as error:
        apply_adapter(repository, wheel)

    assert secret not in str(error.value)
    assert not (repository / ".phycode-adapter/phycode.whl").exists()


def test_adapter_rolls_back_when_wheel_staging_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "evaluator"
    repository.mkdir()
    head = _commit_file(repository, "README.md", "base\n")
    patch_path = tmp_path / "adapter.patch"
    patch_path.write_text(
        "diff --git a/README.md b/README.md\n"
        "index df967b9..67be85f 100644\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1,2 @@\n"
        " base\n"
        "+adapted\n",
        encoding="utf-8",
    )
    wheel = tmp_path / "phycode-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    monkeypatch.setattr(adapter_module, "EXPECTED_EVALUATOR_COMMIT", head)
    monkeypatch.setattr(adapter_module, "PATCH_PATH", patch_path)

    def fail_copy(_source: Path, _destination: Path) -> None:
        raise OSError("sensitive-provider-value")

    monkeypatch.setattr(adapter_module.shutil, "copyfile", fail_copy)

    with pytest.raises(AdapterError) as error:
        apply_adapter(repository, wheel)

    assert "sensitive-provider-value" not in str(error.value)
    assert (repository / "README.md").read_text(encoding="utf-8") == "base\n"
    assert not (repository / ".phycode-adapter").exists()


def test_adapter_reverses_patch_when_atomic_wheel_publish_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "evaluator"
    repository.mkdir()
    head = _commit_file(repository, "README.md", "base\n")
    patch_path = tmp_path / "adapter.patch"
    patch_path.write_text(
        "diff --git a/README.md b/README.md\n"
        "index df967b9..67be85f 100644\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1,2 @@\n"
        " base\n"
        "+adapted\n",
        encoding="utf-8",
    )
    wheel = tmp_path / "phycode-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    monkeypatch.setattr(adapter_module, "EXPECTED_EVALUATOR_COMMIT", head)
    monkeypatch.setattr(adapter_module, "PATCH_PATH", patch_path)

    original_replace = Path.replace

    def fail_adapter_publish(source: Path, destination: Path) -> Path:
        if destination.name == "phycode.whl":
            raise OSError("sensitive-provider-value")
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", fail_adapter_publish)

    with pytest.raises(AdapterError) as error:
        apply_adapter(repository, wheel)

    assert "sensitive-provider-value" not in str(error.value)
    assert (repository / "README.md").read_text(encoding="utf-8") == "base\n"
    assert not (repository / ".phycode-adapter").exists()


def test_patch_does_not_change_ground_truth_copy_order() -> None:
    patch = Path("integrations/prbench/phycode-evaluator.patch").read_text(
        encoding="utf-8"
    )

    assert "_copy_ground_truth_to_workspace" not in patch
    assert "grading.dimensions" not in patch
    assert "phycode prbench run" in patch


def test_patch_changes_only_white_runtime_files() -> None:
    patch = Path("integrations/prbench/phycode-evaluator.patch").read_text(
        encoding="utf-8"
    )
    changed_paths = {
        line.split(" b/", maxsplit=1)[1]
        for line in patch.splitlines()
        if line.startswith("diff --git a/")
    }

    assert changed_paths == {
        "main.py",
        "src/launcher.py",
        "src/my_util/agent_env.py",
        "src/my_util/docker_manager.py",
        "src/white_agent/agent.py",
    }
    assert "src/green_agent/" not in patch


def test_patch_uses_explicit_controls_and_minimal_provider_environment() -> None:
    patch = Path("integrations/prbench/phycode-evaluator.patch").read_text(
        encoding="utf-8"
    )

    assert '"grants": []' in patch
    assert "--phycode-contract" in patch
    assert "--phycode-approvals" in patch
    assert "env -i" in patch
    assert "PHYCODE_API_KEY" in patch
    assert "PHYCODE_BASE_URL" in patch
    assert "PHYCODE_MODEL" in patch
    assert "metadata_file" not in patch
    assert "ground_truth_data_dir" not in patch


def test_patch_uses_pinned_uv_without_pip_bootstrap() -> None:
    patch = Path("integrations/prbench/phycode-evaluator.patch").read_text(
        encoding="utf-8"
    )
    added = "\n".join(
        line[1:]
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )

    assert (
        "uv pip install --system /tmp/phycode-0.1.0-py3-none-any.whl" in added
    )
    assert '"/tmp/phycode.whl"' not in added
    assert "python -m pip install" not in added
    assert "pip install uv" not in added
    assert (
        "sha256:5c3ab83183a73c5d319a77009eb425b60d5bb937f339fb7876788ebf567baf48"
        in added
    )


def test_patch_does_not_persist_white_credentials_for_green() -> None:
    patch = Path("integrations/prbench/phycode-evaluator.patch").read_text(
        encoding="utf-8"
    )
    added = "\n".join(
        line[1:]
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )

    assert 'if t != "phycode":' in added
    assert "_start_without_phycode_environment" in added
    assert "process_env.update(self._provider_env)" in added
    assert "phycode_provider_env.clear()" in added
    assert 'os.environ.pop(name, None)' in added
    assert 'env_vars.update(resolve_env("phycode"))' not in added


def test_patch_verifies_contract_names_match_copied_public_files() -> None:
    patch = Path("integrations/prbench/phycode-evaluator.patch").read_text(
        encoding="utf-8"
    )
    added = "\n".join(
        line[1:]
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )

    assert 'os.path.basename(paper_file)' in added
    assert 'os.path.basename(path) for path in task_config.get("input_files", [])' in added
    assert "Declared public paper/input file was not copied to the workspace" in added


def test_patch_registers_phycode_only_for_full_white_runs() -> None:
    patch = Path("integrations/prbench/phycode-evaluator.patch").read_text(
        encoding="utf-8"
    )
    added = "\n".join(
        line[1:]
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )

    assert 'if effective_green_type == "phycode":' in added
    assert 'if effective_white_type == "phycode" and code_only:' in added


@pytest.mark.parametrize(
    ("task_id", "expected_files", "header", "rows"),
    [
        (
            "aaatest_helloworld",
            ("reproduction/hello.py", "data/output.csv"),
            ("a", "b", "c"),
            (("1", "1", "1"),),
        ),
        (
            "bbbtest_alphabet",
            ("reproduction/alphabet.py", "data/letters.csv"),
            ("letter", "position"),
            (("a", "1"), ("b", "2"), ("c", "3")),
        ),
    ],
)
def test_public_contracts_contain_only_instruction_declared_constraints(
    task_id: str,
    expected_files: tuple[str, ...],
    header: tuple[str, ...],
    rows: tuple[tuple[str, ...], ...],
) -> None:
    path = Path("integrations/prbench/public_contracts") / f"{task_id}.json"
    raw = path.read_text(encoding="utf-8")
    contract = TaskContract.model_validate(json.loads(raw))

    assert contract.instruction_file == "instruction.md"
    assert contract.paper_file == "paper.md"
    assert contract.input_files == ()
    assert contract.expected_files == expected_files
    assert len(contract.constraints) == 1
    assert contract.constraints[0].csv_header == header
    assert contract.constraints[0].csv_rows == rows
    assert "metadata" not in raw.casefold()
    assert "ground_truth" not in raw.casefold()
