from __future__ import annotations

import asyncio
import ast
import importlib.util
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import types
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, cast

import pytest
import typer

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


def _configured_adapter_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
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
    wheel = tmp_path / "phycode-0.1.5-py3-none-any.whl"
    wheel.write_bytes(b"new-wheel")
    monkeypatch.setattr(adapter_module, "EXPECTED_EVALUATOR_COMMIT", head)
    monkeypatch.setattr(adapter_module, "PATCH_PATH", patch_path)
    return repository, wheel


def _patch_section(path: str) -> str:
    patch = Path("integrations/prbench/phycode-evaluator.patch").read_text(
        encoding="utf-8"
    )
    marker = f"diff --git a/{path} b/{path}"
    start = patch.index(marker)
    end = patch.find("\ndiff --git ", start + len(marker))
    return patch[start:] if end < 0 else patch[start:end]


def _added_hunks(path: str) -> tuple[str, ...]:
    section = _patch_section(path)
    hunks: list[str] = []
    current: list[str] | None = None
    for line in section.splitlines():
        if line.startswith("@@"):
            if current:
                hunks.append("\n".join(current) + "\n")
            current = []
            continue
        if current is not None and line.startswith("+") and not line.startswith("+++"):
            current.append(line[1:])
    if current:
        hunks.append("\n".join(current) + "\n")
    return tuple(hunks)


@pytest.fixture
def patched_official_evaluator(tmp_path: Path) -> Path:
    """Apply the real adapter to a fresh fixed-commit evaluator checkout."""
    source_value = os.environ.get("PHYCODE_PRBENCH_EVALUATOR_SOURCE")
    if not source_value:
        pytest.skip("set PHYCODE_PRBENCH_EVALUATOR_SOURCE for the official probe")

    source = Path(source_value).resolve(strict=True)
    repository = tmp_path / "official-evaluator"
    subprocess.run(
        ["git", "clone", "--quiet", str(source), str(repository)],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = tmp_path / "phycode-0.1.5-py3-none-any.whl"
    wheel.write_bytes(b"dynamic-adapter-probe")
    apply_adapter(repository, wheel)
    return repository


def _load_function(
    source: Path,
    function_name: str,
    globals_: dict[str, object],
    *,
    dependencies: tuple[str, ...] = (),
) -> Callable[..., object]:
    """Load one real function without importing the evaluator's optional stack."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    function.decorator_list = []
    dependency_functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in dependencies
    ]
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            ),
            *dependency_functions,
            function,
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace = dict(globals_)
    exec(compile(module, str(source), "exec"), namespace)
    loaded = namespace[function_name]
    assert callable(loaded)
    return loaded


def _load_launcher_output_helpers(
    launcher_path: Path,
    os_module: object,
    *,
    open_function: Callable[..., object] = open,
) -> dict[str, object]:
    path_is_within = _load_function(
        launcher_path, "_path_is_within", {"os": os_module}
    )
    helper_globals = {
        "_path_is_within": path_is_within,
        "json": json,
        "open": open_function,
        "os": os_module,
        "stat": stat,
    }
    read_verified_utf8 = _load_function(
        launcher_path,
        "_read_verified_utf8",
        helper_globals,
    )
    helper_globals["_read_verified_utf8"] = read_verified_utf8
    validate_phycode_contract = _load_function(
        launcher_path,
        "_validate_phycode_contract",
        helper_globals,
    )
    helper_globals["_validate_phycode_contract"] = validate_phycode_contract
    return {
        "_load_verified_json": _load_function(
            launcher_path, "_load_verified_json", helper_globals
        ),
        "_path_is_within": path_is_within,
        "_read_verified_utf8": read_verified_utf8,
        "_remove_stale_output": _load_function(
            launcher_path, "_remove_stale_output", helper_globals
        ),
        "_validate_phycode_contract": validate_phycode_contract,
    }


def _load_pinned_public_task_contract_metadata(
    pinned_evaluator: Path, task_id: str
) -> dict[str, object]:
    """Parse the narrow public task.yaml subset from a pinned evaluator fixture."""

    task_yaml = pinned_evaluator / "data" / "tasks" / task_id / "task.yaml"

    def scalar(value: str) -> str:
        value = value.strip()
        return cast(str, json.loads(value)) if value.startswith('"') else value

    task_config: dict[str, object] = {
        "input_files": [],
        "paper": {},
        "expected_outputs": {},
    }
    section: str | None = None
    output_group: str | None = None
    for raw_line in task_yaml.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            section = None
            output_group = None
            if stripped == "paper:":
                section = "paper"
            elif stripped == "input_files:":
                section = "input_files"
            elif stripped == "expected_outputs:":
                section = "expected_outputs"
            elif stripped.startswith("instruction_file:"):
                task_config["instruction_file"] = scalar(stripped.split(":", 1)[1])
            continue
        if section == "paper" and indent == 2 and stripped.startswith("paper_file:"):
            paper = cast(dict[str, object], task_config["paper"])
            paper["paper_file"] = scalar(stripped.split(":", 1)[1])
        elif section == "input_files" and indent == 2 and stripped.startswith("- "):
            inputs = cast(list[str], task_config["input_files"])
            inputs.append(scalar(stripped[2:]))
        elif section == "expected_outputs" and indent == 2 and stripped.endswith(":"):
            output_group = stripped[:-1]
            outputs = cast(dict[str, object], task_config["expected_outputs"])
            outputs[output_group] = []
        elif (
            section == "expected_outputs"
            and output_group is not None
            and indent == 4
            and stripped.startswith("- ")
        ):
            outputs = cast(dict[str, list[str]], task_config["expected_outputs"])
            outputs[output_group].append(scalar(stripped[2:]))

    assert isinstance(task_config.get("instruction_file"), str)
    assert isinstance(cast(dict[str, object], task_config["paper"]).get("paper_file"), str)
    expected_outputs = task_config["expected_outputs"]
    assert isinstance(expected_outputs, dict) and expected_outputs
    assert all(
        isinstance(group, str)
        and isinstance(paths, list)
        and all(isinstance(path, str) for path in paths)
        for group, paths in expected_outputs.items()
    )
    return task_config


def test_public_task_metadata_helper_locks_supported_pinned_yaml_subset(
    tmp_path: Path,
) -> None:
    task_yaml = tmp_path / "data" / "tasks" / "public-task" / "task.yaml"
    task_yaml.parent.mkdir(parents=True)
    task_yaml.write_text(
        "paper:\n"
        '  paper_file: "paper.md"\n'
        'instruction_file: "instruction.md"\n'
        "input_files:\n"
        '  - "input.csv"\n'
        "expected_outputs:\n"
        "  analysis:\n"
        '    - "reproduction/ANALYSIS.md"\n'
        "  code:\n"
        "    - reproduction/run.py\n",
        encoding="utf-8",
    )

    assert _load_pinned_public_task_contract_metadata(tmp_path, "public-task") == {
        "instruction_file": "instruction.md",
        "paper": {"paper_file": "paper.md"},
        "input_files": ["input.csv"],
        "expected_outputs": {
            "analysis": ["reproduction/ANALYSIS.md"],
            "code": ["reproduction/run.py"],
        },
    }


@pytest.mark.parametrize(
    "task_yaml_text",
    [
        "paper:\n"
        '  paper_file: "paper.md"\n'
        "expected_outputs:\n"
        "  data:\n"
        '    - "result.csv"\n',
        'instruction_file: "instruction.md"\n'
        "expected_outputs:\n"
        "  data:\n"
        '    - "result.csv"\n',
        "paper:\n"
        '  paper_file: "paper.md"\n'
        'instruction_file: "instruction.md"\n',
    ],
    ids=("instruction-file", "paper-file", "expected-outputs"),
)
def test_public_task_metadata_helper_requires_contract_fields(
    tmp_path: Path, task_yaml_text: str
) -> None:
    task_yaml = tmp_path / "data" / "tasks" / "public-task" / "task.yaml"
    task_yaml.parent.mkdir(parents=True)
    task_yaml.write_text(task_yaml_text, encoding="utf-8")

    with pytest.raises(AssertionError):
        _load_pinned_public_task_contract_metadata(tmp_path, "public-task")


def _text_open_calls_missing_utf8(source: Path) -> list[int]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    missing_utf8: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            mode_index = 1
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "open":
            mode_index = (
                1
                if isinstance(node.func.value, ast.Name)
                and node.func.value.id == "io"
                else 0
            )
        else:
            continue
        mode_node = node.args[mode_index] if len(node.args) > mode_index else None
        mode_node = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "mode"),
            mode_node,
        )
        if mode_node is None:
            mode = "r"
        elif isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str):
            mode = mode_node.value
        else:
            # A static scan cannot prove whether a dynamic mode is text or binary.
            continue
        if "b" in mode:
            continue
        encoding_node = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "encoding"),
            None,
        )
        if not (
            isinstance(encoding_node, ast.Constant)
            and encoding_node.value == "utf-8"
        ):
            missing_utf8.append(node.lineno)
    return missing_utf8


def _load_agent_env(source: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("patched_agent_env", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_class(
    source: Path, class_name: str, globals_: dict[str, object]
) -> Callable[..., object]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    class_index, class_node = next(
        (index, node)
        for index, node in enumerate(tree.body)
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    helpers = [
        node
        for node in tree.body[:class_index]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            ),
            *helpers,
            class_node,
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace = dict(globals_)
    exec(compile(module, str(source), "exec"), namespace)
    loaded = namespace[class_name]
    assert isinstance(loaded, type)
    return loaded


def _parameter_default(
    source: Path,
    function_name: str,
    parameter_name: str,
    *,
    class_name: str | None = None,
) -> ast.expr:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    scope: list[ast.stmt] = tree.body
    if class_name is not None:
        class_node = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        scope = class_node.body
    function = next(
        node
        for node in scope
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    positional = [*function.args.posonlyargs, *function.args.args]
    first_default = len(positional) - len(function.args.defaults)
    positional_defaults = {
        argument.arg: default
        for argument, default in zip(
            positional[first_default:], function.args.defaults, strict=True
        )
    }
    keyword_defaults = {
        argument.arg: default
        for argument, default in zip(
            function.args.kwonlyargs, function.args.kw_defaults, strict=True
        )
        if default is not None
    }
    default = {**positional_defaults, **keyword_defaults}[parameter_name]
    return default


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
    wheel = tmp_path / "phycode-0.1.5-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    monkeypatch.setattr(adapter_module, "EXPECTED_EVALUATOR_COMMIT", head)

    with pytest.raises(AdapterError, match="tracked changes"):
        apply_adapter(tmp_path, wheel)

    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "locally changed\n"
    assert not (tmp_path / ".phycode-adapter").exists()


def test_adapter_refuses_existing_wheel_without_touching_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, wheel = _configured_adapter_fixture(tmp_path, monkeypatch)
    destination = repository / ".phycode-adapter/phycode.whl"
    destination.parent.mkdir()
    destination.write_bytes(b"preserve-existing-wheel")

    with pytest.raises(AdapterError, match="already exists"):
        apply_adapter(repository, wheel)

    assert destination.read_bytes() == b"preserve-existing-wheel"
    assert (repository / "README.md").read_text(encoding="utf-8") == "base\n"


def test_adapter_refuses_existing_wheel_symlink_without_touching_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, wheel = _configured_adapter_fixture(tmp_path, monkeypatch)
    destination = repository / ".phycode-adapter/phycode.whl"
    destination.parent.mkdir()
    target = repository / "preserved-wheel.whl"
    target.write_bytes(b"preserve-symlink-target")
    try:
        destination.symlink_to(target)
    except OSError as error:
        pytest.skip(f"file symlink unavailable: {error}")

    with pytest.raises(AdapterError, match="already exists"):
        apply_adapter(repository, wheel)

    assert destination.is_symlink()
    assert target.read_bytes() == b"preserve-symlink-target"
    assert (repository / "README.md").read_text(encoding="utf-8") == "base\n"


def test_adapter_refuses_dangling_wheel_symlink_without_replacing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, wheel = _configured_adapter_fixture(tmp_path, monkeypatch)
    destination = repository / ".phycode-adapter/phycode.whl"
    destination.parent.mkdir()
    try:
        destination.symlink_to(repository / "missing-wheel.whl")
    except OSError as error:
        pytest.skip(f"file symlink unavailable: {error}")

    with pytest.raises(AdapterError, match="already exists"):
        apply_adapter(repository, wheel)

    assert destination.is_symlink()
    assert not destination.exists()
    assert (repository / "README.md").read_text(encoding="utf-8") == "base\n"


def test_adapter_refuses_dangling_wheel_symlink_via_path_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, wheel = _configured_adapter_fixture(tmp_path, monkeypatch)
    destination = repository / ".phycode-adapter/phycode.whl"
    original_is_symlink = Path.is_symlink

    def report_dangling_destination(path: Path) -> bool:
        if path == destination:
            return True
        return original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", report_dangling_destination)

    with pytest.raises(AdapterError, match="already exists"):
        apply_adapter(repository, wheel)

    assert not destination.exists()
    assert (repository / "README.md").read_text(encoding="utf-8") == "base\n"


def test_adapter_refuses_existing_wheel_symlink_via_path_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, wheel = _configured_adapter_fixture(tmp_path, monkeypatch)
    destination = repository / ".phycode-adapter/phycode.whl"
    target = repository / "preserved-wheel.whl"
    target.write_bytes(b"preserve-symlink-target")
    original_exists = Path.exists
    original_is_symlink = Path.is_symlink

    def report_link_exists(path: Path) -> bool:
        if path == destination:
            return True
        return original_exists(path)

    def report_link(path: Path) -> bool:
        if path == destination:
            return True
        return original_is_symlink(path)

    monkeypatch.setattr(Path, "exists", report_link_exists)
    monkeypatch.setattr(Path, "is_symlink", report_link)

    with pytest.raises(AdapterError, match="already exists"):
        apply_adapter(repository, wheel)

    assert target.read_bytes() == b"preserve-symlink-target"
    assert not destination.is_file()
    assert (repository / "README.md").read_text(encoding="utf-8") == "base\n"


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
    wheel = tmp_path / "phycode-0.1.5-py3-none-any.whl"
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
    wheel = tmp_path / "phycode-0.1.5-py3-none-any.whl"
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
    wheel = tmp_path / "phycode-0.1.5-py3-none-any.whl"
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
    wheel = tmp_path / "phycode-0.1.5-py3-none-any.whl"
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


def test_patch_changes_only_required_runtime_files() -> None:
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
        "src/green_agent/agent.py",
        "src/my_util/agent_env.py",
        "src/my_util/docker_manager.py",
        "src/white_agent/agent.py",
    }


def test_official_green_agent_text_open_calls_use_utf8(
    patched_official_evaluator: Path,
) -> None:
    source = patched_official_evaluator / "src/green_agent/agent.py"
    missing_utf8 = _text_open_calls_missing_utf8(source)

    assert missing_utf8 == [], (
        "green evaluator text open calls missing encoding='utf-8' at lines "
        f"{missing_utf8}"
    )


def test_official_green_agent_read_file_safe_round_trips_utf8(
    patched_official_evaluator: Path,
    tmp_path: Path,
) -> None:
    source = patched_official_evaluator / "src/green_agent/agent.py"
    read_file_safe = _load_function(source, "read_file_safe", {"os": os})
    expected = "replacement: \ufffd; Chinese: 中文; non-GBK: 🧪"
    target = tmp_path / "unicode.txt"
    target.write_text(expected, encoding="utf-8")

    assert read_file_safe(str(target)) == expected


def test_official_white_agent_text_open_calls_use_utf8(
    patched_official_evaluator: Path,
) -> None:
    source = patched_official_evaluator / "src/white_agent/agent.py"
    missing_utf8 = _text_open_calls_missing_utf8(source)

    assert missing_utf8 == [], (
        "white evaluator text open calls missing encoding='utf-8' at lines "
        f"{missing_utf8}"
    )


def test_official_white_instruction_writer_round_trips_utf8(
    patched_official_evaluator: Path,
    tmp_path: Path,
) -> None:
    source = patched_official_evaluator / "src/white_agent/agent.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    executor = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "ClaudeCodeWhiteAgentExecutor"
    )
    execute = next(
        node
        for node in executor.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "execute"
    )
    instruction_writer = next(
        node
        for node in ast.walk(execute)
        if isinstance(node, ast.With)
        and isinstance(node.items[0].context_expr, ast.Call)
        and isinstance(node.items[0].context_expr.func, ast.Name)
        and node.items[0].context_expr.func.id == "open"
        and isinstance(node.items[0].context_expr.args[0], ast.Name)
        and node.items[0].context_expr.args[0].id == "instruction_path"
    )
    writer = ast.FunctionDef(
        name="write_instruction",
        args=ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg="instruction_path"), ast.arg(arg="user_input")],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[],
        ),
        body=[instruction_writer],
        decorator_list=[],
    )
    module = ast.Module(body=[writer], type_ignores=[])
    ast.fix_missing_locations(module)

    def windows_text_open(
        path: str | os.PathLike[str],
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ) -> object:
        kwargs.setdefault("encoding", "gbk")
        return open(path, mode, *args, **kwargs)  # type: ignore[call-overload]

    namespace: dict[str, object] = {"open": windows_text_open}
    exec(compile(module, str(source), "exec"), namespace)
    write_instruction = namespace["write_instruction"]
    assert callable(write_instruction)
    expected = "minus: \u2212; replacement: \ufffd; Chinese: 中文; emoji: 🧪"
    target = tmp_path / "instruction.md"

    write_instruction(target, expected)

    assert target.read_text(encoding="utf-8") == expected


def test_official_launcher_text_open_calls_use_utf8(
    patched_official_evaluator: Path,
    tmp_path: Path,
) -> None:
    probe = tmp_path / "open_probe.py"
    probe.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import io",
                'open("builtin.txt", "r")',
                'Path("path.txt").open("w")',
                'io.open("io.txt", mode="a")',
                'open("binary.bin", "wb")',
                'mode = "r"',
                'open("dynamic.txt", mode)',
                'open("encoded.txt", "w", encoding="utf-8")',
            ]
        ),
        encoding="utf-8",
    )

    assert _text_open_calls_missing_utf8(probe) == [3, 4, 5]

    source = patched_official_evaluator / "src/launcher.py"
    missing_utf8 = _text_open_calls_missing_utf8(source)

    assert missing_utf8 == [], (
        "launcher text open calls missing encoding='utf-8' at lines "
        f"{missing_utf8}"
    )


def test_official_launcher_verified_json_round_trips_utf8_without_platform_default(
    patched_official_evaluator: Path,
    tmp_path: Path,
) -> None:
    def windows_text_open(
        path: str | os.PathLike[str],
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ) -> object:
        kwargs.setdefault("encoding", "gbk")
        return open(path, mode, *args, **kwargs)  # type: ignore[call-overload]

    launcher = patched_official_evaluator / "src/launcher.py"
    helpers = _load_launcher_output_helpers(
        launcher,
        os,
        open_function=windows_text_open,
    )
    load_verified_json = helpers["_load_verified_json"]
    expected = {"summary": "Chinese: 中文; non-GBK: 🧪"}
    output = tmp_path / "result.json"
    output.write_text(
        json.dumps(expected, ensure_ascii=False),
        encoding="utf-8",
    )

    valid, payload = load_verified_json(str(tmp_path), str(output))  # type: ignore[operator]

    assert valid is True
    assert payload == expected


@pytest.mark.parametrize("replacement_kind", ["symlink", "regular"])
def test_official_launcher_verified_json_rejects_lstat_to_open_replacement(
    patched_official_evaluator: Path,
    tmp_path: Path,
    replacement_kind: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = workspace / "result.json"
    output.write_text('{"status": "trusted"}', encoding="utf-8")
    untrusted = tmp_path / "untrusted.json"
    untrusted.write_text('{"status": "untrusted"}', encoding="utf-8")
    replacement = workspace / "replacement.json"
    replacement.write_text('{"status": "untrusted"}', encoding="utf-8")
    untrusted_reads: list[str] = []

    class RaceOsProxy:
        path = os.path

        def __init__(self) -> None:
            self.swapped = False

        def __getattr__(self, name: str) -> object:
            return getattr(os, name)

        def swap(self, path: str | os.PathLike[str]) -> None:
            if self.swapped or Path(path) != output:
                return
            self.swapped = True
            if replacement_kind == "regular":
                output.unlink()
                replacement.replace(output)

        def open(self, path: str | os.PathLike[str], flags: int) -> int:
            self.swap(path)
            if replacement_kind == "symlink":
                nofollow = getattr(os, "O_NOFOLLOW", 0)
                if nofollow and flags & nofollow:
                    raise OSError("synthetic O_NOFOLLOW rejection")
                return os.open(untrusted, flags)
            return os.open(path, flags)

        def fdopen(self, descriptor: int, *args: object, **kwargs: object) -> object:
            untrusted_reads.append("fdopen")
            return os.fdopen(descriptor, *args, **kwargs)  # type: ignore[call-overload]

    race_os = RaceOsProxy()

    def race_text_open(
        path: str | os.PathLike[str],
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ) -> object:
        race_os.swap(path)
        untrusted_reads.append("open")
        if replacement_kind == "symlink":
            return open(untrusted, mode, *args, **kwargs)  # type: ignore[call-overload]
        return open(path, mode, *args, **kwargs)  # type: ignore[call-overload]

    launcher = patched_official_evaluator / "src/launcher.py"
    helpers = _load_launcher_output_helpers(
        launcher,
        race_os,
        open_function=race_text_open,
    )
    load_verified_json = helpers["_load_verified_json"]

    valid, payload = load_verified_json(  # type: ignore[operator]
        str(workspace), str(output)
    )

    assert valid is False
    assert payload is None
    assert untrusted_reads == []


def test_official_phycode_setup_defers_green_credentials_until_grading(
    patched_official_evaluator: Path,
) -> None:
    calls: list[str] = []
    instances: list[object] = []
    opencode_install_options: list[bool] = []

    class FakeDockerEnvironment:
        def __init__(self, **kwargs: object) -> None:
            self.env_vars = kwargs["env_vars"]
            instances.append(self)

        def start(self) -> None:
            pass

        def check_health(self) -> bool:
            return True

        def install_phycode(self) -> bool:
            return True

        def check_phycode_health(self) -> bool:
            return True

        def install_opencode(self, install_openai_compatible: bool = False) -> bool:
            opencode_install_options.append(install_openai_compatible)
            return True

        def check_opencode_health(self) -> bool:
            return True

        def install_claude_code(self) -> bool:
            return True

        def check_claude_health(self) -> bool:
            return True

    def resolve_env(agent_type: str) -> dict[str, str]:
        calls.append(agent_type)
        return {f"{agent_type.upper()}_TOKEN": f"{agent_type}-secret"}

    setup = _load_function(
        patched_official_evaluator / "src/launcher.py",
        "setup_docker_environment",
        {
            "_copy_phycode_controls": lambda *_args: None,
            "DockerEnvironment": FakeDockerEnvironment,
            "PROJECT_ROOT": str(patched_official_evaluator),
            "logger": types.SimpleNamespace(info=lambda *_: None),
            "os": os,
            "resolve_env": resolve_env,
        },
    )
    config = {"docker": {}}

    setup(  # type: ignore[operator]
        config, "task", "workspace", "phycode", "opencode", b"{}", None
    )

    assert calls == []
    assert instances[-1].env_vars == {}  # type: ignore[attr-defined]
    assert opencode_install_options[-1] is True

    setup(config, "task", "workspace", "claude", "opencode")  # type: ignore[operator]

    assert set(calls) == {"claude", "opencode"}
    assert instances[-1].env_vars == {  # type: ignore[attr-defined]
        "CLAUDE_TOKEN": "claude-secret",
        "OPENCODE_TOKEN": "opencode-secret",
    }
    assert opencode_install_options[-1] is False


@pytest.mark.parametrize(
    ("failure_mode", "cleanup_raises"),
    [
        ("adapter_install", False),
        ("post_start_exception", False),
        ("post_start_exception", True),
    ],
)
def test_official_setup_cleans_only_its_started_container_and_preserves_error(
    patched_official_evaluator: Path,
    caplog: pytest.LogCaptureFixture,
    failure_mode: str,
    cleanup_raises: bool,
) -> None:
    original_error = RuntimeError("original setup synthetic-secret")
    owned_remove_calls: list[bool] = []
    unrelated_remove_calls: list[bool] = []
    stop_calls: list[str] = []
    instances: list[object] = []

    class FakeContainer:
        def __init__(self, identifier: str, calls: list[bool]) -> None:
            self.id = identifier
            self._calls = calls

        def remove(self, force: bool = False) -> None:
            self._calls.append(force)
            if cleanup_raises and self.id == "owned-container":
                raise RuntimeError("cleanup synthetic-secret")

    unrelated_container = FakeContainer("unrelated-container", unrelated_remove_calls)

    class FakeDockerEnvironment:
        def __init__(self, **_kwargs: object) -> None:
            self.container: FakeContainer | None = None
            self.container_id: str | None = None
            instances.append(self)

        def start(self) -> None:
            self.container = FakeContainer("owned-container", owned_remove_calls)
            self.container_id = self.container.id

        def check_health(self) -> bool:
            if failure_mode == "post_start_exception":
                raise original_error
            return True

        def install_phycode(self) -> bool:
            return True

        def check_phycode_health(self) -> bool:
            return True

        def install_opencode(self, install_openai_compatible: bool = False) -> bool:
            assert install_openai_compatible is True
            return False

        def check_opencode_health(self) -> bool:
            return True

        def stop(self) -> None:
            stop_calls.append(self.container_id or "none")
            try:
                if self.container is not None:
                    self.container.remove(force=True)
            finally:
                self.container = None
                self.container_id = None

    setup = _load_function(
        patched_official_evaluator / "src/launcher.py",
        "setup_docker_environment",
        {
            "_copy_phycode_controls": lambda *_args: None,
            "DockerEnvironment": FakeDockerEnvironment,
            "PROJECT_ROOT": str(patched_official_evaluator),
            "logger": logging.getLogger("test.patched.launcher-setup"),
            "os": os,
            "resolve_env": lambda _agent_type: {},
        },
    )
    caplog.set_level(logging.WARNING, logger="test.patched.launcher-setup")

    with pytest.raises(RuntimeError) as raised:
        setup(
            {"docker": {}},
            "task",
            "workspace",
            "phycode",
            "opencode",
            b"{}",
            None,
        )

    if failure_mode == "post_start_exception":
        assert raised.value is original_error
    else:
        assert str(raised.value) == "Failed to install OpenCode CLI in Docker"
    assert stop_calls == ["owned-container"]
    assert owned_remove_calls == [True]
    assert unrelated_remove_calls == []
    assert unrelated_container.id == "unrelated-container"
    assert len(instances) == 1
    assert instances[0].container is None  # type: ignore[attr-defined]
    assert instances[0].container_id is None  # type: ignore[attr-defined]
    assert "synthetic-secret" not in caplog.text


def test_official_launcher_does_not_double_remove_after_setup_self_cleanup(
    patched_official_evaluator: Path,
    tmp_path: Path,
) -> None:
    task_root = tmp_path / "tasks/public-task"
    task_root.mkdir(parents=True)
    (task_root / "task.yaml").write_text("public task", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text(
        json.dumps(
            {
                "instruction_file": "instruction.md",
                "paper_file": "paper.md",
                "input_files": [],
                "expected_files": [],
            }
        ),
        encoding="utf-8",
    )
    owned_remove_calls: list[bool] = []
    outer_remove_calls: list[str | None] = []
    setup_error = RuntimeError("setup failed after owned cleanup")

    def self_cleaning_setup(*_args: object, **_kwargs: object) -> object:
        owned_remove_calls.append(True)
        raise setup_error

    launcher_path = patched_official_evaluator / "src/launcher.py"
    output_helpers = _load_launcher_output_helpers(launcher_path, os)
    launch_evaluation = _load_function(
        launcher_path,
        "launch_evaluation",
        {
            "DATA_DIR": str(tmp_path / "tasks"),
            **output_helpers,
            "_archive_trace": lambda *_args: None,
            "_archive_workspace": lambda *_args: "archive-destination",
            "_copy_input_files": lambda *_args: None,
            "_copy_paper_images": lambda *_args: None,
            "_copy_paper_markdown": lambda *_args: None,
            "_copy_phycode_controls": lambda *_args: None,
            "_export_traces_for_type": lambda *_args: None,
            "_kill_process": lambda *_args: None,
            "_remove_container": lambda container_id: outer_remove_calls.append(
                container_id
            ),
            "find_free_port_pair": lambda: (9001, 9002),
            "json": json,
            "logger": types.SimpleNamespace(
                error=lambda *_: None,
                info=lambda *_: None,
                warning=lambda *_: None,
            ),
            "logging": types.SimpleNamespace(
                INFO=20,
                basicConfig=lambda **_kwargs: None,
                FileHandler=lambda *_args: object(),
                StreamHandler=lambda *_args: object(),
            ),
            "open": open,
            "os": os,
            "resolve_env": lambda _agent_type: {},
            "setup_docker_environment": self_cleaning_setup,
            "stat": stat,
            "yaml": types.SimpleNamespace(
                safe_load=lambda _handle: {
                    "instruction_file": "instruction.md",
                    "docker": {},
                    "paper": {
                        "title": "Public task",
                        "author": "Public author",
                        "paper_file": "paper.md",
                    },
                }
            ),
        },
    )

    with pytest.raises(RuntimeError) as raised:
        asyncio.run(
            cast(
                Coroutine[object, object, object],
                launch_evaluation(
                    task_id="public-task",
                    green_port=9001,
                    white_port=9002,
                    white_agent_type="phycode",
                    green_agent_type="opencode",
                    phycode_contract=str(contract),
                    archive=False,
                ),
            )
        )

    assert raised.value is setup_error
    assert owned_remove_calls == [True]
    assert outer_remove_calls == [None]


@pytest.mark.parametrize(
    ("adapter_exit", "expected_success"),
    [(0, True), (23, False)],
)
def test_official_deferred_opencode_setup_installs_adapter_without_persistent_provider(
    patched_official_evaluator: Path,
    adapter_exit: int,
    expected_success: bool,
) -> None:
    docker_environment = _load_class(
        patched_official_evaluator / "src/my_util/docker_manager.py",
        "DockerEnvironment",
        {
            "APIError": type("FakeAPIError", (Exception,), {}),
            "docker": types.SimpleNamespace(from_env=lambda: None),
            "json": json,
            "logger": types.SimpleNamespace(
                info=lambda *_: None,
                warning=lambda *_: None,
                error=lambda *_: None,
            ),
            "os": os,
        },
    )
    environment = object.__new__(docker_environment)  # type: ignore[arg-type]
    environment.container = object()
    environment.env_vars = {}
    environment.host_uid = 1000
    environment.host_gid = 1000
    commands: list[str] = []

    def exec_command(command: str, timeout: int | None = None) -> dict[str, object]:
        del timeout
        commands.append(command)
        exit_code = (
            adapter_exit
            if "npm install @ai-sdk/openai-compatible" in command
            else 0
        )
        return {"exit_code": exit_code, "stdout": "opencode 1.18.3", "stderr": ""}

    environment.exec_command = exec_command

    assert (
        environment.install_opencode(install_openai_compatible=True)
        is expected_success
    )

    adapter_installs = [
        command
        for command in commands
        if "npm install @ai-sdk/openai-compatible" in command
    ]
    assert len(adapter_installs) == 1
    assert all("green-sensitive-value" not in command for command in commands)
    assert not any("EOFCFG" in command for command in commands)


@pytest.mark.parametrize(
    ("identity", "expected_uid", "expected_gid"),
    [("windows", 1000, 1000), ("posix", 1201, 1301)],
)
def test_official_docker_start_selects_identity_before_container_creation(
    patched_official_evaluator: Path,
    identity: str,
    expected_uid: int,
    expected_gid: int,
) -> None:
    events: list[str] = []

    class FakeContainer:
        id = "container-id"
        short_id = "container"

    class FakeImages:
        def pull(self, _image: str) -> None:
            events.append("pull")

    class FakeContainers:
        def run(self, *_args: object, **_kwargs: object) -> FakeContainer:
            events.append("run")
            return FakeContainer()

    client = types.SimpleNamespace(images=FakeImages(), containers=FakeContainers())
    if identity == "posix":
        os_module = types.SimpleNamespace(
            getuid=lambda: events.append("getuid") or expected_uid,
            getgid=lambda: events.append("getgid") or expected_gid,
            path=os.path,
            makedirs=os.makedirs,
        )
    else:
        os_module = types.SimpleNamespace(path=os.path, makedirs=os.makedirs)

    docker_environment = _load_class(
        patched_official_evaluator / "src/my_util/docker_manager.py",
        "DockerEnvironment",
        {
            "APIError": type("FakeAPIError", (Exception,), {}),
            "docker": types.SimpleNamespace(from_env=lambda: client),
            "logger": types.SimpleNamespace(
                info=lambda *_: None,
                warning=lambda *_: None,
            ),
            "os": os_module,
        },
    )
    environment = docker_environment(pip_packages=["public-dependency"])
    commands: list[str] = []

    def exec_command(command: str, timeout: int | None = None) -> dict[str, object]:
        del timeout
        commands.append(command)
        return {"exit_code": 0, "stdout": "ok", "stderr": ""}

    environment.exec_command = exec_command  # type: ignore[attr-defined]
    environment.start()  # type: ignore[attr-defined]

    assert environment.host_uid == expected_uid  # type: ignore[attr-defined]
    assert environment.host_gid == expected_gid  # type: ignore[attr-defined]
    assert events.index("run") > events.index("pull")
    if identity == "posix":
        assert events[:2] == ["getuid", "getgid"]
    else:
        assert events[0] == "pull"
    assert f"groupadd -o -g {expected_gid} agent" in commands[0]
    assert f"useradd -o -m -s /bin/bash -u {expected_uid}" in commands[0]
    assert f"chown -R {expected_uid}:{expected_gid} /workspace" in commands[0]
    assert environment.check_health() is True  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("failure_call", "cleanup_raises"),
    [(0, False), (1, False), (2, False), (0, True)],
)
def test_official_docker_start_cleans_post_create_failures_without_masking_error(
    patched_official_evaluator: Path,
    caplog: pytest.LogCaptureFixture,
    failure_call: int,
    cleanup_raises: bool,
) -> None:
    original_error = RuntimeError("original setup failure")
    remove_calls: list[bool] = []

    class FakeContainer:
        id = "container-id"
        short_id = "container"

        def remove(self, force: bool = False) -> None:
            remove_calls.append(force)
            if cleanup_raises:
                raise RuntimeError("cleanup synthetic-secret")

    class FakeImages:
        def pull(self, _image: str) -> None:
            pass

    class FakeContainers:
        def run(self, *_args: object, **_kwargs: object) -> FakeContainer:
            return FakeContainer()

    client = types.SimpleNamespace(images=FakeImages(), containers=FakeContainers())
    os_module = types.SimpleNamespace(
        getuid=lambda: 1201,
        getgid=lambda: 1301,
        path=os.path,
        makedirs=os.makedirs,
    )
    docker_environment = _load_class(
        patched_official_evaluator / "src/my_util/docker_manager.py",
        "DockerEnvironment",
        {
            "APIError": type("FakeAPIError", (Exception,), {}),
            "docker": types.SimpleNamespace(from_env=lambda: client),
            "logger": logging.getLogger("test.patched.docker-manager"),
            "os": os_module,
        },
    )
    environment = docker_environment(
        env_vars={"PROVIDER_TOKEN": "synthetic-secret"},
        pip_packages=["public-dependency"],
    )
    call_count = 0

    def exec_command(_command: str, timeout: int | None = None) -> dict[str, object]:
        nonlocal call_count
        del timeout
        current = call_count
        call_count += 1
        if current == failure_call:
            raise original_error
        return {"exit_code": 0, "stdout": "ok", "stderr": ""}

    environment.exec_command = exec_command  # type: ignore[attr-defined]
    caplog.set_level(logging.WARNING, logger="test.patched.docker-manager")

    with pytest.raises(RuntimeError) as raised:
        environment.start()  # type: ignore[attr-defined]

    assert raised.value is original_error
    assert remove_calls == [True]
    assert environment.container is None  # type: ignore[attr-defined]
    assert environment.container_id is None  # type: ignore[attr-defined]
    if cleanup_raises:
        assert "container cleanup failed after startup error" in caplog.text
    assert "synthetic-secret" not in caplog.text


@pytest.mark.parametrize(
    ("failure_stage", "failure_kind"),
    [
        (None, None),
        ("stop", "exception"),
        ("stop", "base_exception"),
        ("remove", "exception"),
        ("remove", "base_exception"),
    ],
)
def test_official_docker_stop_is_secret_safe_and_base_exception_safe(
    patched_official_evaluator: Path,
    caplog: pytest.LogCaptureFixture,
    failure_stage: str | None,
    failure_kind: str | None,
) -> None:
    secret = "cleanup-provider-synthetic-secret"
    events: list[tuple[str, object]] = []
    unrelated_events: list[tuple[str, object]] = []

    def failure() -> BaseException:
        if failure_kind == "base_exception":
            if failure_stage == "remove":
                return SystemExit(secret)
            return KeyboardInterrupt(secret)
        return RuntimeError(secret)

    class FakeContainer:
        short_id = "owned-short-id"

        def stop(self, timeout: int) -> None:
            events.append(("stop", timeout))
            if failure_stage == "stop":
                raise failure()

        def remove(self, force: bool = False) -> None:
            events.append(("remove", force))
            if failure_stage == "remove":
                raise failure()

    class UnrelatedContainer:
        def stop(self, timeout: int) -> None:
            unrelated_events.append(("stop", timeout))

        def remove(self, force: bool = False) -> None:
            unrelated_events.append(("remove", force))

    unrelated_container = UnrelatedContainer()
    logger = logging.getLogger("test.patched.docker-stop")
    docker_environment = _load_class(
        patched_official_evaluator / "src/my_util/docker_manager.py",
        "DockerEnvironment",
        {
            "APIError": type("FakeAPIError", (Exception,), {}),
            "docker": types.SimpleNamespace(from_env=lambda: None),
            "logger": logger,
            "os": os,
        },
    )
    environment = object.__new__(docker_environment)  # type: ignore[arg-type]
    environment.container = FakeContainer()
    environment.container_id = "owned-container-id"
    environment.workspace_dir = None
    caplog.set_level(logging.INFO, logger="test.patched.docker-stop")

    leaked_error: BaseException | None = None
    try:
        environment.stop()
    except BaseException as error:
        leaked_error = error

    assert leaked_error is None
    assert events[0] == ("stop", 10)
    assert events.count(("stop", 10)) == 1
    assert events.count(("remove", True)) == 1
    assert unrelated_events == []
    assert unrelated_container is not environment.container
    assert environment.container is None
    assert environment.container_id is None
    assert secret not in caplog.text


def test_official_setup_preserves_original_error_when_real_stop_hits_base_exception(
    patched_official_evaluator: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "cleanup-provider-synthetic-secret"
    original_error = RuntimeError("original setup failure")
    events: list[tuple[str, object]] = []
    unrelated_events: list[tuple[str, object]] = []
    instances: list[object] = []
    logger = logging.getLogger("test.patched.real-stop-setup")
    docker_environment = _load_class(
        patched_official_evaluator / "src/my_util/docker_manager.py",
        "DockerEnvironment",
        {
            "APIError": type("FakeAPIError", (Exception,), {}),
            "docker": types.SimpleNamespace(from_env=lambda: None),
            "logger": logger,
            "os": os,
        },
    )
    real_stop = docker_environment.stop  # type: ignore[attr-defined]

    class FakeContainer:
        short_id = "owned-short-id"

        def stop(self, timeout: int) -> None:
            events.append(("stop", timeout))
            raise KeyboardInterrupt(secret)

        def remove(self, force: bool = False) -> None:
            events.append(("remove", force))

    class FakeDockerEnvironment:
        def __init__(self, **_kwargs: object) -> None:
            self.container = FakeContainer()
            self.container_id: str | None = "owned-container-id"
            self.workspace_dir = None
            instances.append(self)

        def start(self) -> None:
            pass

        def check_health(self) -> bool:
            raise original_error

        def stop(self) -> None:
            real_stop(self)

    unrelated_container = types.SimpleNamespace(events=unrelated_events)
    setup = _load_function(
        patched_official_evaluator / "src/launcher.py",
        "setup_docker_environment",
        {
            "_copy_phycode_controls": lambda *_args: None,
            "DockerEnvironment": FakeDockerEnvironment,
            "PROJECT_ROOT": str(patched_official_evaluator),
            "logger": logger,
            "os": os,
            "resolve_env": lambda _agent_type: {},
        },
    )
    caplog.set_level(logging.INFO, logger="test.patched.real-stop-setup")

    with pytest.raises(RuntimeError) as raised:
        setup(
            {"docker": {}},
            "task",
            "workspace",
            "phycode",
            "opencode",
            b"{}",
            None,
        )

    assert raised.value is original_error
    assert events == [("stop", 10), ("remove", True)]
    assert unrelated_container.events == []
    assert len(instances) == 1
    assert instances[0].container is None  # type: ignore[attr-defined]
    assert instances[0].container_id is None  # type: ignore[attr-defined]
    assert secret not in caplog.text


def test_official_launch_cli_exposes_and_bounds_approval_wait(
    patched_official_evaluator: Path,
) -> None:
    main = patched_official_evaluator / "main.py"
    cli_env = {
        name: os.environ[name]
        for name in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "WINDIR")
        if name in os.environ
    }
    cli_env["COLUMNS"] = "200"
    help_result = subprocess.run(
        [sys.executable, str(main), "launch", "--help"],
        cwd=patched_official_evaluator,
        check=False,
        capture_output=True,
        text=True,
        env=cli_env,
    )

    assert help_result.returncode == 0
    assert "--approval-wait-seconds" in help_result.stdout
    assert "--phycode-max-tool-calls" in help_result.stdout
    assert "--phycode-max-context-chars" in help_result.stdout

    invalid_result = subprocess.run(
        [sys.executable, str(main), "launch", "--approval-wait-seconds", "901"],
        cwd=patched_official_evaluator,
        check=False,
        capture_output=True,
        text=True,
        env=cli_env,
    )

    assert invalid_result.returncode != 0
    assert "900" in invalid_result.stderr

    for option, value, boundary in (
        ("--phycode-max-tool-calls", "0", "1"),
        ("--phycode-max-tool-calls", "101", "100"),
        ("--phycode-max-context-chars", "999", "1000"),
        ("--phycode-max-context-chars", "64001", "64000"),
    ):
        invalid_result = subprocess.run(
            [sys.executable, str(main), "launch", option, value],
            cwd=patched_official_evaluator,
            check=False,
            capture_output=True,
            text=True,
            env=cli_env,
        )

        assert invalid_result.returncode != 0
        assert boundary in invalid_result.stderr


def test_official_phycode_defaults_stay_consistent_and_reach_command(
    patched_official_evaluator: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    main_path = patched_official_evaluator / "main.py"
    launcher_path = patched_official_evaluator / "src/launcher.py"
    white_path = patched_official_evaluator / "src/white_agent/agent.py"
    expected_defaults = {
        "max_tool_calls": 40,
        "max_context_chars": 12_000,
    }

    for parameter_name, expected in (
        ("phycode_max_tool_calls", expected_defaults["max_tool_calls"]),
        ("phycode_max_context_chars", expected_defaults["max_context_chars"]),
    ):
        option = _parameter_default(main_path, "launch", parameter_name)
        assert isinstance(option, ast.Call)
        assert isinstance(option.func, ast.Attribute)
        assert isinstance(option.func.value, ast.Name)
        assert (option.func.value.id, option.func.attr) == ("typer", "Option")
        assert ast.literal_eval(option.args[0]) == expected
        assert ast.literal_eval(
            _parameter_default(
                launcher_path,
                "launch_evaluation",
                parameter_name,
            )
        ) == expected

    assert ast.literal_eval(
        _parameter_default(white_path, "start_white_agent", "max_tool_calls")
    ) == expected_defaults["max_tool_calls"]
    assert ast.literal_eval(
        _parameter_default(white_path, "start_white_agent", "max_context_chars")
    ) == expected_defaults["max_context_chars"]

    assert ast.literal_eval(
        _parameter_default(
            white_path,
            "__init__",
            "max_tool_calls",
            class_name="ClaudeCodeWhiteAgentExecutor",
        )
    ) == expected_defaults["max_tool_calls"]
    assert ast.literal_eval(
        _parameter_default(
            white_path,
            "__init__",
            "max_context_chars",
            class_name="ClaudeCodeWhiteAgentExecutor",
        )
    ) == expected_defaults["max_context_chars"]

    agent_env_module = types.ModuleType("src.my_util.agent_env")
    agent_env_module.build_docker_exec_env_flags = (  # type: ignore[attr-defined]
        lambda _agent_type: ["-e", "HOME=/home/agent"]
    )
    agent_env_module.resolve_env = lambda _agent_type: {}  # type: ignore[attr-defined]

    def run_with_cleanup(
        action: Callable[[], object],
        provider_env: dict[str, str],
        process_env: dict[str, str],
    ) -> object:
        try:
            return action()
        finally:
            provider_env.clear()
            process_env.clear()

    agent_env_module.run_with_phycode_cleanup = run_with_cleanup  # type: ignore[attr-defined]
    src_module = types.ModuleType("src")
    src_module.__path__ = []  # type: ignore[attr-defined]
    util_module = types.ModuleType("src.my_util")
    util_module.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src", src_module)
    monkeypatch.setitem(sys.modules, "src.my_util", util_module)
    monkeypatch.setitem(sys.modules, "src.my_util.agent_env", agent_env_module)

    commands: list[list[str]] = []

    def fake_popen(command: list[str], **_kwargs: object) -> types.SimpleNamespace:
        commands.append(command)
        return types.SimpleNamespace(pid=1234)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    class FakeRunningTask:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class FakeContext:
        context_id = None

        def get_user_input(self) -> str:
            return "perform the public task"

    class FakeEventQueue:
        async def enqueue_event(self, _event: object) -> None:
            pass

    executor_class = _load_class(
        white_path,
        "ClaudeCodeWhiteAgentExecutor",
        {
            "AgentExecutor": object,
            "RunningTask": FakeRunningTask,
            "json": json,
            "logger": types.SimpleNamespace(
                info=lambda *_: None,
                error=lambda *_: None,
            ),
            "new_agent_text_message": lambda *args, **kwargs: (args, kwargs),
            "os": os,
            "uuid": types.SimpleNamespace(
                uuid4=lambda: types.SimpleNamespace(hex="12345678abcdef")
            ),
        },
    )
    executor = executor_class(
        workspace_base=str(tmp_path),
        docker_container_id="container-id",
        agent_type="phycode",
        provider_env={"PHYCODE_API_KEY": "synthetic-value"},
    )

    assert executor.max_tool_calls == 40  # type: ignore[attr-defined]
    assert executor.max_context_chars == 12_000  # type: ignore[attr-defined]
    asyncio.run(
        executor.execute(FakeContext(), FakeEventQueue())  # type: ignore[attr-defined]
    )

    assert len(commands) == 1
    assert commands[0][-1].endswith(
        "--approval-wait-seconds 0 "
        "--max-tool-calls 40 "
        "--max-context-chars 12000"
    )
    assert "synthetic-value" not in str(commands[0])


@pytest.mark.parametrize("approval_wait_seconds", [-1, 901])
def test_official_launcher_rejects_invalid_approval_wait_before_container_setup(
    patched_official_evaluator: Path,
    approval_wait_seconds: int,
) -> None:
    launch_evaluation = _load_function(
        patched_official_evaluator / "src/launcher.py",
        "launch_evaluation",
        {},
    )

    with pytest.raises(ValueError, match="approval wait"):
        asyncio.run(
            launch_evaluation(approval_wait_seconds=approval_wait_seconds)  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("limits", "message"),
    [
        ({"phycode_max_tool_calls": 0}, "tool calls"),
        ({"phycode_max_tool_calls": 101}, "tool calls"),
        ({"phycode_max_context_chars": 999}, "context chars"),
        ({"phycode_max_context_chars": 64_001}, "context chars"),
    ],
)
def test_official_launcher_rejects_invalid_phycode_limits_before_container_setup(
    patched_official_evaluator: Path,
    limits: dict[str, int],
    message: str,
) -> None:
    launch_evaluation = _load_function(
        patched_official_evaluator / "src/launcher.py",
        "launch_evaluation",
        {},
    )

    with pytest.raises(ValueError, match=message):
        asyncio.run(launch_evaluation(**limits))  # type: ignore[arg-type]


def test_official_validator_accepts_every_tracked_public_contract(
    patched_official_evaluator: Path,
) -> None:
    validate_contract = cast(
        Callable[[dict[str, object], str | None, bool], bytes],
        _load_launcher_output_helpers(
            patched_official_evaluator / "src/launcher.py", os
        )["_validate_phycode_contract"],
    )
    contract_paths = sorted(Path("integrations/prbench/public_contracts").glob("*.json"))

    assert contract_paths
    for contract_path in contract_paths:
        task_yaml = (
            patched_official_evaluator
            / "data"
            / "tasks"
            / contract_path.stem
            / "task.yaml"
        )
        assert task_yaml.is_file(), contract_path.stem
        task_config = _load_pinned_public_task_contract_metadata(
            patched_official_evaluator, contract_path.stem
        )

        snapshot = validate_contract(task_config, str(contract_path.resolve()), False)

        assert snapshot == contract_path.read_bytes(), contract_path.stem


@pytest.mark.parametrize("fold_case", [False, True], ids=("posix-host", "windows-host"))
def test_official_validator_uses_case_sensitive_workspace_keys_on_every_host(
    patched_official_evaluator: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fold_case: bool,
) -> None:
    monkeypatch.setattr(
        os.path,
        "normcase",
        (lambda path: path.casefold()) if fold_case else (lambda path: path),
    )
    validate_contract = cast(
        Callable[[dict[str, object], str | None, bool], bytes],
        _load_launcher_output_helpers(
            patched_official_evaluator / "src/launcher.py",
            os,
        )["_validate_phycode_contract"],
    )
    task_config = {
        "instruction_file": "instruction.md",
        "paper": {"paper_file": "paper.md"},
        "input_files": [],
        "expected_outputs": {"data": ["data/Result.csv"]},
    }
    contract = tmp_path / "contract.json"
    contract.write_text(
        json.dumps(
            {
                "instruction_file": "instruction.md",
                "paper_file": "paper.md",
                "input_files": [],
                "expected_files": ["data/result.csv"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Explicit PhyCode contract is invalid"):
        validate_contract(task_config, str(contract), False)


def test_task_white_1993_missing_analysis_is_rejected_before_provider_or_docker(
    patched_official_evaluator: Path,
    tmp_path: Path,
) -> None:
    task_id = "task_white_1993"
    task_config = _load_pinned_public_task_contract_metadata(
        patched_official_evaluator, task_id
    )
    contract_payload = json.loads(
        Path(f"integrations/prbench/public_contracts/{task_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(contract_payload, dict)
    expected_files = contract_payload["expected_files"]
    assert isinstance(expected_files, list)
    expected_files.remove("reproduction/ANALYSIS.md")
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps(contract_payload), encoding="utf-8")
    side_effects: list[str] = []

    class ProviderReached(RuntimeError):
        pass

    def resolve_env(_agent_type: str) -> dict[str, str]:
        side_effects.append("resolve-env")
        raise ProviderReached

    def setup_docker_environment(*_args: object, **_kwargs: object) -> object:
        side_effects.append("docker-setup")
        raise AssertionError("contract validation must run before Docker setup")

    launch_evaluation = _load_function(
        patched_official_evaluator / "src/launcher.py",
        "launch_evaluation",
        {
            "DATA_DIR": str(patched_official_evaluator / "data" / "tasks"),
            "json": json,
            "logger": types.SimpleNamespace(error=lambda *_: None, info=lambda *_: None),
            "logging": types.SimpleNamespace(
                INFO=20,
                basicConfig=lambda **_kwargs: None,
                FileHandler=lambda *_args: object(),
                StreamHandler=lambda *_args: object(),
            ),
            "open": open,
            "os": os,
            "resolve_env": resolve_env,
            "setup_docker_environment": setup_docker_environment,
            "stat": stat,
            "yaml": types.SimpleNamespace(safe_load=lambda _handle: task_config),
        },
        dependencies=(
            "_path_is_within",
            "_read_verified_utf8",
            "_validate_phycode_contract",
        ),
    )

    with pytest.raises(ValueError, match="Explicit PhyCode contract is invalid"):
        asyncio.run(
            launch_evaluation(  # type: ignore[arg-type]
                task_id=task_id,
                green_port=9001,
                white_port=9002,
                white_agent_type="phycode",
                green_agent_type="opencode",
                phycode_contract=str(contract),
            )
        )

    assert side_effects == []


def test_official_launcher_validates_explicit_expected_file_superset_before_provider(
    patched_official_evaluator: Path,
    tmp_path: Path,
) -> None:
    side_effects: list[str] = []
    task_root = tmp_path / "tasks/public-task"
    task_root.mkdir(parents=True)
    (task_root / "task.yaml").write_text("public task", encoding="utf-8")
    task_config = {
        "instruction_file": "instruction.md",
        "paper": {"paper_file": "paper.md"},
        "input_files": [],
        "expected_outputs": {
            "analysis": ["analysis.md"],
            "code": ["reproduce.py"],
            "data": ["result.csv"],
            "report": ["report.md"],
        },
    }

    class ProviderReached(RuntimeError):
        pass

    def resolve_env(_agent_type: str) -> dict[str, str]:
        side_effects.append("resolve-env")
        raise ProviderReached

    def setup_docker_environment(*_args: object, **_kwargs: object) -> object:
        side_effects.append("docker-setup")
        raise AssertionError("contract validation must run before Docker setup")

    launcher = patched_official_evaluator / "src/launcher.py"
    launch_evaluation = _load_function(
        launcher,
        "launch_evaluation",
        {
            "DATA_DIR": str(tmp_path / "tasks"),
            "json": json,
            "logger": types.SimpleNamespace(error=lambda *_: None, info=lambda *_: None),
            "logging": types.SimpleNamespace(
                INFO=20,
                basicConfig=lambda **_kwargs: None,
                FileHandler=lambda *_args: object(),
                StreamHandler=lambda *_args: object(),
            ),
            "open": open,
            "os": os,
            "resolve_env": resolve_env,
            "setup_docker_environment": setup_docker_environment,
            "stat": stat,
            "yaml": types.SimpleNamespace(safe_load=lambda _handle: task_config),
        },
        dependencies=(
            "_path_is_within",
            "_read_verified_utf8",
            "_validate_phycode_contract",
        ),
    )
    contract = tmp_path / "contract.json"
    base_contract: dict[str, object] = {
        "instruction_file": "instruction.md",
        "paper_file": "paper.md",
        "input_files": [],
        "expected_files": [
            "analysis.md",
            "reproduce.py",
            "extras/entry.py",
            "result.csv",
            "extras/report.csv",
            "report.md",
        ],
        "execution_entrypoints": ["extras/entry.py"],
        "constraints": [
            {"path": "extras/report.csv", "csv_header": ["value"]}
        ],
    }

    contract.write_text(json.dumps(base_contract), encoding="utf-8")
    with pytest.raises(ProviderReached):
        asyncio.run(
            launch_evaluation(  # type: ignore[arg-type]
                task_id="public-task",
                green_port=9001,
                white_port=9002,
                white_agent_type="phycode",
                green_agent_type="opencode",
                phycode_contract=str(contract),
            )
        )
    assert side_effects == ["resolve-env"]

    declared_expected = ["analysis.md", "reproduce.py", "result.csv", "report.md"]
    invalid_expected_files = {
        "missing declared group": declared_expected[:-1],
        "reordered declared groups": [
            "reproduce.py",
            "analysis.md",
            "result.csv",
            "report.md",
        ],
        "duplicate": [*declared_expected, "report.md"],
        "empty": [*declared_expected, ""],
        "dot": [*declared_expected, "."],
        "dot-dot": [*declared_expected, ".."],
        "traversal": [*declared_expected, "extra/../escape.txt"],
        "posix absolute": [*declared_expected, "/absolute.txt"],
        "windows absolute": [*declared_expected, "C:\\absolute.txt"],
        "hidden fixture": [*declared_expected, "_ground_truth/data.csv"],
        "credential": [*declared_expected, ".env"],
        "non-string": [*declared_expected, 7],
    }
    for label, expected_files in invalid_expected_files.items():
        side_effects.clear()
        invalid_contract = {
            **base_contract,
            "expected_files": expected_files,
            "execution_entrypoints": [],
            "constraints": [],
        }
        contract.write_text(json.dumps(invalid_contract), encoding="utf-8")

        with pytest.raises(ValueError, match="PhyCode contract"):
            asyncio.run(
                launch_evaluation(  # type: ignore[arg-type]
                    task_id="public-task",
                    green_port=9001,
                    white_port=9002,
                    white_agent_type="phycode",
                    green_agent_type="opencode",
                    phycode_contract=str(contract),
                )
            )

        assert side_effects == [], label


def test_official_launcher_does_not_derive_approvals_from_safe_contract_superset(
    patched_official_evaluator: Path,
    tmp_path: Path,
) -> None:
    task_root = tmp_path / "tasks/public-task"
    task_root.mkdir(parents=True)
    (task_root / "task.yaml").write_text("public task", encoding="utf-8")
    task_config = {
        "instruction_file": "instruction.md",
        "paper": {
            "title": "Public task",
            "author": "Public author",
            "paper_file": "paper.md",
        },
        "input_files": [],
        "expected_outputs": {
            "code": ["reproduce.py"],
            "data": ["result.csv"],
        },
    }
    contract = tmp_path / "contract.json"
    contract.write_text(
        json.dumps(
            {
                "instruction_file": "instruction.md",
                "paper_file": "paper.md",
                "input_files": [],
                "expected_files": [
                    "reproduce.py",
                    "extras/entry.py",
                    "result.csv",
                    "extras/report.csv",
                ],
                "execution_entrypoints": ["extras/entry.py"],
                "constraints": [
                    {"path": "extras/report.csv", "csv_header": ["value"]}
                ],
            }
        ),
        encoding="utf-8",
    )
    side_effects: list[str] = []
    approval_snapshots: list[bytes] = []

    class ReachedDockerSetup(RuntimeError):
        pass

    def resolve_env(agent_type: str) -> dict[str, str]:
        assert agent_type == "phycode"
        side_effects.append("resolve-env")
        return {}

    def setup_docker_environment(*_args: object, **kwargs: object) -> object:
        side_effects.append("docker-setup")
        approval_snapshot = kwargs["phycode_approvals_snapshot"]
        assert isinstance(approval_snapshot, bytes)
        approval_snapshots.append(approval_snapshot)
        raise ReachedDockerSetup

    launch_evaluation = _load_function(
        patched_official_evaluator / "src/launcher.py",
        "launch_evaluation",
        {
            "DATA_DIR": str(tmp_path / "tasks"),
            "_archive_trace": lambda *_args: None,
            "_archive_workspace": lambda *_args: "archive-destination",
            "_copy_input_files": lambda *_args: None,
            "_copy_paper_images": lambda *_args: None,
            "_copy_paper_markdown": lambda *_args: None,
            "_export_traces_for_type": lambda *_args: None,
            "_kill_process": lambda *_args: None,
            "_path_is_within": lambda *_args: True,
            "_remove_container": lambda *_args: None,
            "_remove_stale_output": lambda *_args: None,
            "find_free_port_pair": lambda: (9001, 9002),
            "json": json,
            "logger": types.SimpleNamespace(
                error=lambda *_: None,
                info=lambda *_: None,
            ),
            "logging": types.SimpleNamespace(
                INFO=20,
                basicConfig=lambda **_kwargs: None,
                FileHandler=lambda *_args: object(),
                StreamHandler=lambda *_args: object(),
            ),
            "open": open,
            "os": os,
            "resolve_env": resolve_env,
            "setup_docker_environment": setup_docker_environment,
            "stat": stat,
            "yaml": types.SimpleNamespace(safe_load=lambda _handle: task_config),
        },
        dependencies=(
            "_path_is_within",
            "_read_verified_utf8",
            "_validate_phycode_contract",
            "_validate_phycode_approvals",
        ),
    )

    with pytest.raises(ReachedDockerSetup):
        asyncio.run(
            launch_evaluation(  # type: ignore[arg-type]
                task_id="public-task",
                green_port=9001,
                white_port=9002,
                white_agent_type="phycode",
                green_agent_type="opencode",
                phycode_contract=str(contract),
                archive=False,
            )
        )

    assert side_effects == ["resolve-env", "docker-setup"]
    assert approval_snapshots == [b'{\n  "grants": []\n}\n']


@pytest.mark.parametrize(
    "contract_case",
    [
        "malformed",
        "non_object",
        "schema",
        "filename",
        "input_files",
        "expected_files",
    ],
)
def test_official_launcher_rejects_untrusted_phycode_contract_before_agent_side_effects(
    patched_official_evaluator: Path,
    tmp_path: Path,
    contract_case: str,
) -> None:
    side_effects: list[str] = []
    task_root = tmp_path / "tasks/public-task"
    task_root.mkdir(parents=True)
    (task_root / "task.yaml").write_text("public task", encoding="utf-8")
    task_config = {
        "instruction_file": "instruction.md",
        "paper": {
            "title": "Public task",
            "author": "Public author",
            "paper_file": "paper.md",
        },
        "input_files": ["input.csv"],
        "expected_outputs": {
            "code": ["reproduce.py"],
            "data": ["result.csv"],
        },
    }
    contract_payload: dict[str, object] | list[object] = {
        "instruction_file": "instruction.md",
        "paper_file": "paper.md",
        "input_files": ["input.csv"],
        "expected_files": ["reproduce.py", "result.csv"],
    }
    if contract_case == "non_object":
        contract_payload = []
    elif contract_case == "schema":
        assert isinstance(contract_payload, dict)
        contract_payload["unexpected"] = True
    elif contract_case == "filename":
        assert isinstance(contract_payload, dict)
        contract_payload["instruction_file"] = "../instruction.md"
    elif contract_case == "input_files":
        assert isinstance(contract_payload, dict)
        contract_payload["input_files"] = []
    elif contract_case == "expected_files":
        assert isinstance(contract_payload, dict)
        contract_payload["expected_files"] = ["reproduce.py"]

    contract = tmp_path / "contract.json"
    contract.write_text(
        "{" if contract_case == "malformed" else json.dumps(contract_payload),
        encoding="utf-8",
    )

    def resolve_env(_agent_type: str) -> dict[str, str]:
        side_effects.append("resolve-env")
        return {}

    def setup_docker_environment(*_args: object, **_kwargs: object) -> object:
        side_effects.append("docker-setup")
        raise RuntimeError("agent side effect reached")

    launch_evaluation = _load_function(
        patched_official_evaluator / "src/launcher.py",
        "launch_evaluation",
        {
            "DATA_DIR": str(tmp_path / "tasks"),
            "_copy_input_files": lambda *_args: None,
            "_copy_paper_images": lambda *_args: None,
            "_copy_paper_markdown": lambda *_args: None,
            "_copy_phycode_controls": lambda *_args: None,
            "_archive_trace": lambda *_args: None,
            "_archive_workspace": lambda *_args: "archive-destination",
            "_export_traces_for_type": lambda *_args: None,
            "_kill_process": lambda *_args: None,
            "_path_is_within": lambda *_args: True,
            "_remove_container": lambda *_args: None,
            "_remove_stale_output": lambda *_args: None,
            "find_free_port_pair": lambda: (9001, 9002),
            "json": json,
            "logger": types.SimpleNamespace(
                error=lambda *_: None,
                info=lambda *_: None,
            ),
            "logging": types.SimpleNamespace(
                INFO=20,
                basicConfig=lambda **_kwargs: None,
                FileHandler=lambda *_args: object(),
                StreamHandler=lambda *_args: object(),
            ),
            "open": open,
            "os": os,
            "stat": stat,
            "resolve_env": resolve_env,
            "setup_docker_environment": setup_docker_environment,
            "yaml": types.SimpleNamespace(safe_load=lambda _handle: task_config),
        },
        dependencies=(
            "_path_is_within",
            "_read_verified_utf8",
            "_validate_phycode_contract",
        ),
    )

    with pytest.raises(ValueError, match="PhyCode contract"):
        asyncio.run(
            launch_evaluation(  # type: ignore[arg-type]
                task_id="public-task",
                white_agent_type="phycode",
                green_agent_type="opencode",
                phycode_contract=str(contract),
            )
        )

    assert side_effects == []


def test_official_phycode_control_writes_fail_closed_on_target_or_workspace_swap(
    patched_official_evaluator: Path,
    tmp_path: Path,
) -> None:
    launcher = patched_official_evaluator / "src/launcher.py"
    task_config = {
        "instruction_file": "instruction.md",
        "paper": {"paper_file": "paper.md"},
    }
    contract_snapshot = b'{"contract": "trusted"}'
    approval_snapshot = b'{"grants": []}'
    sentinel_bytes = b"outside-sentinel"
    race_results: list[tuple[str, bool, bytes, bool, bool, bool, bool, bool]] = []

    for race_kind in ("approval-target", "workspace-parent", "contract-close"):
        case_dir = tmp_path / race_kind
        task_dir = case_dir / "task"
        workspace = task_dir / "workspace"
        outside = case_dir / "outside"
        alternate_workspace = case_dir / "alternate-workspace"
        workspace.mkdir(parents=True)
        outside.mkdir()
        alternate_workspace.mkdir()
        (task_dir / "instruction.md").write_text(
            "public instruction", encoding="utf-8"
        )
        (workspace / "paper.md").write_text("public paper", encoding="utf-8")
        approvals = case_dir / "approvals.json"
        approvals.write_bytes(b'{"grants": []}')
        sentinel = outside / "sentinel.json"
        sentinel.write_bytes(sentinel_bytes)
        approvals_dst = workspace / "phycode-approvals.json"
        contract_dst = workspace / "task_contract.json"
        opened_descriptors: set[int] = set()
        closed_descriptors: set[int] = set()
        control_unlink_calls = 0

        class RaceOsProxy:
            path = os.path
            supports_dir_fd: set[object] = set()

            def __init__(self) -> None:
                self.workspace_swapped = False
                self.contract_descriptor: int | None = None
                self.close_failed = False

            def __getattr__(self, name: str) -> object:
                return getattr(os, name)

            def lstat(self, path: str | os.PathLike[str]) -> os.stat_result:
                if self.workspace_swapped and Path(path) == workspace:
                    return os.lstat(alternate_workspace)
                return os.lstat(path)

            def open(
                self,
                path: str | os.PathLike[str],
                flags: int,
                mode: int = 0o777,
                **kwargs: object,
            ) -> int:
                target = Path(path)
                is_control_create = bool(flags & os.O_CREAT) and target in {
                    contract_dst,
                    approvals_dst,
                }
                if is_control_create and (
                    (race_kind == "approval-target" and target == approvals_dst)
                    or (race_kind == "workspace-parent" and target == contract_dst)
                ):
                    descriptor = (
                        os.open(path, flags, mode, **kwargs)  # type: ignore[call-overload]
                        if race_kind == "workspace-parent"
                        else os.open(sentinel, os.O_WRONLY)
                    )
                    opened_descriptors.add(descriptor)
                    if race_kind == "workspace-parent":
                        self.workspace_swapped = True
                    return descriptor
                descriptor = os.open(path, flags, mode, **kwargs)  # type: ignore[call-overload]
                if is_control_create and race_kind == "contract-close":
                    opened_descriptors.add(descriptor)
                    if target == contract_dst:
                        self.contract_descriptor = descriptor
                return descriptor

            def close(self, descriptor: int) -> None:
                closed_descriptors.add(descriptor)
                os.close(descriptor)
                if (
                    race_kind == "contract-close"
                    and descriptor == self.contract_descriptor
                    and not self.close_failed
                ):
                    self.close_failed = True
                    raise OSError("synthetic close failure")

            def unlink(
                self, path: str | os.PathLike[str], **kwargs: object
            ) -> None:
                nonlocal control_unlink_calls
                if race_kind == "approval-target" and Path(path) == contract_dst:
                    displaced = outside / "displaced-contract.json"
                    os.replace(contract_dst, displaced)
                    os.link(sentinel, contract_dst)
                control_unlink_calls += 1
                os.unlink(path, **kwargs)  # type: ignore[call-overload]

        race_os = RaceOsProxy()

        copy_controls = _load_function(
            launcher,
            "_copy_phycode_controls",
            {
                "os": race_os,
                "shutil": shutil,
                "stat": stat,
            },
            dependencies=(
                "_path_is_within",
                "_read_verified_utf8",
                "_is_reparse_point",
                "_phycode_workspace_identity",
                "_verify_phycode_control",
                "_preflight_phycode_controls",
                "_write_phycode_control",
            ),
        )

        raised = False
        try:
            copy_controls(  # type: ignore[operator]
                task_config,
                str(task_dir),
                str(workspace),
                contract_snapshot,
                approval_snapshot,
            )
        except (OSError, RuntimeError):
            raised = True
        raced_target_cleaned = not (
            contract_dst if race_kind == "workspace-parent" else approvals_dst
        ).exists()
        contract_is_snapshot = (
            contract_dst.is_file()
            and contract_dst.read_bytes() == contract_snapshot
        )
        retry_succeeded = True
        if race_kind in {"approval-target", "contract-close"}:
            retry_copy = _load_function(
                launcher,
                "_copy_phycode_controls",
                {"os": os, "shutil": shutil, "stat": stat},
                dependencies=(
                    "_path_is_within",
                    "_read_verified_utf8",
                    "_is_reparse_point",
                    "_phycode_workspace_identity",
                    "_verify_phycode_control",
                    "_preflight_phycode_controls",
                    "_write_phycode_control",
                ),
            )
            try:
                retry_copy(  # type: ignore[operator]
                    task_config,
                    str(task_dir),
                    str(workspace),
                    contract_snapshot,
                    approval_snapshot,
                )
            except (OSError, RuntimeError):
                retry_succeeded = False
        race_results.append(
            (
                race_kind,
                raised,
                sentinel.read_bytes(),
                bool(opened_descriptors)
                and opened_descriptors <= closed_descriptors,
                raced_target_cleaned,
                contract_is_snapshot,
                retry_succeeded,
                control_unlink_calls == 0,
            )
        )

    expected_race_results = [
        ("approval-target", True, sentinel_bytes, True, True, True, True, True),
        ("workspace-parent", True, sentinel_bytes, True, False, False, True, True),
        ("contract-close", True, sentinel_bytes, True, True, True, True, True),
    ]

    preexisting_results: list[
        tuple[str, bool, bytes | None, bytes | None]
    ] = []
    for scenario in (
        "exact-contract",
        "exact-pair",
        "mismatch-contract",
        "mismatch-approval",
    ):
        case_dir = tmp_path / f"preexisting-{scenario}"
        task_dir = case_dir / "task"
        workspace = task_dir / "workspace"
        workspace.mkdir(parents=True)
        (task_dir / "instruction.md").write_text("instruction", encoding="utf-8")
        (workspace / "paper.md").write_text("paper", encoding="utf-8")
        contract_dst = workspace / "task_contract.json"
        approvals_dst = workspace / "phycode-approvals.json"
        if scenario in {"exact-contract", "exact-pair"}:
            contract_dst.write_bytes(contract_snapshot)
        elif scenario == "mismatch-contract":
            contract_dst.write_bytes(b"mismatched-contract")
        if scenario == "exact-pair":
            approvals_dst.write_bytes(approval_snapshot)
        elif scenario == "mismatch-approval":
            approvals_dst.write_bytes(b"mismatched-approval")
        copy_controls = _load_function(
            launcher,
            "_copy_phycode_controls",
            {"os": os, "shutil": shutil, "stat": stat},
            dependencies=(
                "_path_is_within",
                "_read_verified_utf8",
                "_is_reparse_point",
                "_phycode_workspace_identity",
                "_verify_phycode_control",
                "_preflight_phycode_controls",
                "_write_phycode_control",
            ),
        )

        raised = False
        try:
            copy_controls(  # type: ignore[operator]
                task_config,
                str(task_dir),
                str(workspace),
                contract_snapshot,
                approval_snapshot,
            )
        except (OSError, RuntimeError):
            raised = True

        preexisting_results.append(
            (
                scenario,
                raised,
                contract_dst.read_bytes() if contract_dst.is_file() else None,
                approvals_dst.read_bytes() if approvals_dst.is_file() else None,
            )
        )

    postwrite_results: list[tuple[str, bool, bool, bytes, bool]] = []
    for race_kind in ("target-after-write", "workspace-after-write"):
        case_dir = tmp_path / race_kind
        workspace = case_dir / "workspace"
        alternate_workspace = case_dir / "alternate-workspace"
        outside = case_dir / "outside"
        workspace.mkdir(parents=True)
        alternate_workspace.mkdir()
        outside.mkdir()
        target = workspace / "task_contract.json"
        sentinel = outside / "sentinel.json"
        sentinel.write_bytes(sentinel_bytes)
        opened_descriptors: set[int] = set()
        closed_descriptors: set[int] = set()

        class PostWriteRaceOsProxy:
            path = os.path
            supports_dir_fd: set[object] = set()

            def __init__(self) -> None:
                self.control_descriptor: int | None = None
                self.swapped = False

            def __getattr__(self, name: str) -> object:
                return getattr(os, name)

            def lstat(self, path: str | os.PathLike[str]) -> os.stat_result:
                if self.swapped and Path(path) == workspace:
                    return os.lstat(alternate_workspace)
                if self.swapped and Path(path) == target:
                    return os.lstat(sentinel)
                return os.lstat(path)

            def open(
                self,
                path: str | os.PathLike[str],
                flags: int,
                mode: int = 0o777,
                **kwargs: object,
            ) -> int:
                descriptor = os.open(path, flags, mode, **kwargs)  # type: ignore[call-overload]
                if Path(path) == target and flags & os.O_CREAT:
                    self.control_descriptor = descriptor
                    opened_descriptors.add(descriptor)
                return descriptor

            def write(self, descriptor: int, payload: object) -> int:
                written = os.write(descriptor, payload)  # type: ignore[arg-type]
                if descriptor == self.control_descriptor:
                    self.swapped = True
                return written

            def close(self, descriptor: int) -> None:
                closed_descriptors.add(descriptor)
                os.close(descriptor)

        race_os = PostWriteRaceOsProxy()
        writer = _load_function(
            launcher,
            "_write_phycode_control",
            {"os": race_os, "stat": stat},
            dependencies=(
                "_path_is_within",
                "_is_reparse_point",
                "_phycode_workspace_identity",
                "_verify_phycode_control",
            ),
        )
        raised = False
        publication_token: object | None = None
        try:
            publication_token = writer(
                str(workspace), target.name, b"trusted-payload"
            )
        except (OSError, RuntimeError):
            raised = True
        postwrite_results.append(
            (
                race_kind,
                raised,
                publication_token is None,
                sentinel.read_bytes(),
                bool(opened_descriptors)
                and opened_descriptors <= closed_descriptors,
            )
        )

    assert postwrite_results == [
        ("target-after-write", True, True, sentinel_bytes, True),
        ("workspace-after-write", True, True, sentinel_bytes, True),
    ]
    assert race_results == expected_race_results
    assert preexisting_results == [
        ("exact-contract", False, contract_snapshot, approval_snapshot),
        ("exact-pair", False, contract_snapshot, approval_snapshot),
        ("mismatch-contract", True, b"mismatched-contract", None),
        ("mismatch-approval", True, None, b"mismatched-approval"),
    ]


def test_official_launcher_copies_prevalidated_control_snapshots_after_source_swap(
    patched_official_evaluator: Path,
    tmp_path: Path,
) -> None:
    task_dir = tmp_path / "tasks" / "public-task"
    task_dir.mkdir(parents=True)
    (task_dir / "task.yaml").write_text("public task", encoding="utf-8")
    (task_dir / "instruction.md").write_text("public instruction", encoding="utf-8")
    (task_dir / "paper.md").write_text("public paper", encoding="utf-8")
    contract = tmp_path / "public-contract.json"
    contract_bytes = json.dumps(
        {
            "instruction_file": "instruction.md",
            "paper_file": "paper.md",
            "expected_files": ["result.csv"],
            "constraints": [
                {
                    "path": "result.csv",
                    "csv_header": ["结果"],
                    "csv_data_row_count": 1,
                }
            ],
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    contract.write_bytes(contract_bytes)
    approvals = tmp_path / "approvals.json"
    approval_bytes = b'{"grants": []}'
    approvals.write_bytes(approval_bytes)
    swapped_contract_bytes = b'{"unexpected": true}'
    swapped_approval_bytes = b'{"grants": [{"unexpected": true}]}'
    source_open_counts = {contract: 0, approvals: 0}
    setup_snapshots: list[tuple[bytes, bytes]] = []

    class ContractOsProxy:
        path = os.path

        def __getattr__(self, name: str) -> object:
            return getattr(os, name)

        def open(
            self,
            path: str | os.PathLike[str],
            flags: int,
            mode: int = 0o777,
            **kwargs: object,
        ) -> int:
            source = Path(path)
            if source in source_open_counts:
                source_open_counts[source] += 1
            return os.open(path, flags, mode, **kwargs)  # type: ignore[call-overload]

    contract_os = ContractOsProxy()

    def tracking_open(
        path: str | os.PathLike[str],
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ) -> object:
        return open(path, mode, *args, **kwargs)  # type: ignore[call-overload]

    launcher = patched_official_evaluator / "src/launcher.py"
    copy_controls = _load_function(
        launcher,
        "_copy_phycode_controls",
        {
            "os": contract_os,
            "shutil": shutil,
            "stat": stat,
        },
        dependencies=(
            "_path_is_within",
            "_read_verified_utf8",
            "_is_reparse_point",
            "_phycode_workspace_identity",
            "_verify_phycode_control",
            "_preflight_phycode_controls",
            "_write_phycode_control",
        ),
    )
    task_config = {
        "instruction_file": "instruction.md",
        "paper": {
            "title": "Public task",
            "author": "Public author",
            "paper_file": "paper.md",
        },
        "expected_outputs": {"data": ["result.csv"]},
    }

    def resolve_env(agent_type: str) -> dict[str, str]:
        if agent_type == "phycode":
            contract.write_bytes(swapped_contract_bytes)
            approvals.write_bytes(swapped_approval_bytes)
        return {}

    class StopAfterControls(RuntimeError):
        pass

    def setup_docker_environment(
        received_task_config: dict[str, object],
        received_task_dir: str,
        workspace_dir: str,
        **kwargs: object,
    ) -> object:
        contract_snapshot = kwargs["phycode_contract_snapshot"]
        approval_snapshot = kwargs["phycode_approvals_snapshot"]
        assert isinstance(contract_snapshot, bytes)
        assert isinstance(approval_snapshot, bytes)
        setup_snapshots.append((contract_snapshot, approval_snapshot))
        copy_controls(  # type: ignore[operator]
            received_task_config,
            received_task_dir,
            workspace_dir,
            contract_snapshot,
            approval_snapshot,
        )
        raise StopAfterControls

    launch_evaluation = _load_function(
        launcher,
        "launch_evaluation",
        {
            "DATA_DIR": str(tmp_path / "tasks"),
            "_archive_trace": lambda *_args: None,
            "_archive_workspace": lambda *_args: "archive-destination",
            "_copy_input_files": lambda *_args: None,
            "_copy_paper_images": lambda *_args: None,
            "_copy_paper_markdown": lambda _config, _task, workspace: (
                Path(workspace) / "paper.md"
            ).write_text("public paper", encoding="utf-8"),
            "_copy_phycode_controls": copy_controls,
            "_export_traces_for_type": lambda *_args: None,
            "_kill_process": lambda *_args: None,
            "_path_is_within": lambda *_args: True,
            "_remove_container": lambda *_args: None,
            "_remove_stale_output": lambda *_args: None,
            "find_free_port_pair": lambda: (9001, 9002),
            "json": json,
            "logger": types.SimpleNamespace(
                error=lambda *_: None,
                info=lambda *_: None,
            ),
            "logging": types.SimpleNamespace(
                INFO=20,
                basicConfig=lambda **_kwargs: None,
                FileHandler=lambda *_args: object(),
                StreamHandler=lambda *_args: object(),
            ),
            "open": tracking_open,
            "os": contract_os,
            "resolve_env": resolve_env,
            "setup_docker_environment": setup_docker_environment,
            "stat": stat,
            "yaml": types.SimpleNamespace(safe_load=lambda _handle: task_config),
        },
        dependencies=(
            "_path_is_within",
            "_read_verified_utf8",
            "_validate_phycode_contract",
            "_validate_phycode_approvals",
        ),
    )

    with pytest.raises(StopAfterControls):
        asyncio.run(
            launch_evaluation(  # type: ignore[arg-type]
                task_id="public-task",
                white_agent_type="phycode",
                green_agent_type="opencode",
                phycode_contract=str(contract),
                phycode_approvals=str(approvals),
                archive=False,
            )
        )

    workspace = task_dir / "workspace"
    assert contract.read_bytes() == swapped_contract_bytes
    assert approvals.read_bytes() == swapped_approval_bytes
    assert (workspace / "task_contract.json").read_bytes() == contract_bytes
    assert (workspace / "phycode-approvals.json").read_bytes() == approval_bytes
    assert setup_snapshots == [(contract_bytes, approval_bytes)]
    assert source_open_counts == {contract: 1, approvals: 1}


def test_official_main_and_white_runner_pass_approval_wait_only_to_phycode(
    patched_official_evaluator: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launch_calls: list[dict[str, object]] = []

    async def fake_launch_evaluation(**kwargs: object) -> bool:
        launch_calls.append(kwargs)
        return True

    src_module = types.ModuleType("src")
    src_module.__path__ = []  # type: ignore[attr-defined]
    launcher_module = types.ModuleType("src.launcher")
    launcher_module.launch_evaluation = fake_launch_evaluation  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src", src_module)
    monkeypatch.setitem(sys.modules, "src.launcher", launcher_module)

    launch = _load_function(
        patched_official_evaluator / "main.py",
        "launch",
        {"asyncio": asyncio, "typer": typer},
    )
    launch(
        task_id="public-task",
        green_port=0,
        white_port=0,
        code_only=False,
        agent_type="claude",
        white_agent_type="phycode",
        green_agent_type="opencode",
        phycode_contract="contract.json",
        phycode_approvals="approvals.json",
        approval_wait_seconds=900,
        phycode_max_tool_calls=50,
        phycode_max_context_chars=24_000,
        no_archive=True,
        results_subdir=None,
    )

    assert launch_calls[0]["approval_wait_seconds"] == 900
    assert launch_calls[0]["phycode_max_tool_calls"] == 50
    assert launch_calls[0]["phycode_max_context_chars"] == 24_000

    agent_env_module = types.ModuleType("src.my_util.agent_env")
    agent_env_module.build_docker_exec_env_flags = (  # type: ignore[attr-defined]
        lambda _agent_type: ["-e", "HOME=/home/agent"]
    )
    agent_env_module.resolve_env = lambda _agent_type: {}  # type: ignore[attr-defined]

    def run_with_cleanup(
        action: Callable[[], object],
        provider_env: dict[str, str],
        process_env: dict[str, str],
    ) -> object:
        try:
            return action()
        finally:
            provider_env.clear()
            process_env.clear()

    agent_env_module.run_with_phycode_cleanup = run_with_cleanup  # type: ignore[attr-defined]
    util_module = types.ModuleType("src.my_util")
    util_module.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.my_util", util_module)
    monkeypatch.setitem(sys.modules, "src.my_util.agent_env", agent_env_module)

    commands: list[list[str]] = []

    def fake_popen(cmd: list[str], **_kwargs: object) -> types.SimpleNamespace:
        commands.append(cmd)
        return types.SimpleNamespace(pid=1234)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    class FakeEventQueue:
        async def enqueue_event(self, _event: object) -> None:
            pass

    class FakeContext:
        context_id = None

        def get_user_input(self) -> str:
            return "perform the public task"

    class FakeRunningTask:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    safe_os = types.SimpleNamespace(path=os.path, environ={}, makedirs=os.makedirs)
    execute = _load_function(
        patched_official_evaluator / "src/white_agent/agent.py",
        "execute",
        {
            "RunningTask": FakeRunningTask,
            "json": json,
            "logger": types.SimpleNamespace(info=lambda *_: None, error=lambda *_: None),
            "new_agent_text_message": lambda *args, **kwargs: (args, kwargs),
            "os": safe_os,
            "uuid": types.SimpleNamespace(
                uuid4=lambda: types.SimpleNamespace(hex="12345678abcdef")
            ),
        },
    )

    for agent_type in ("phycode", "codex"):
        executor = types.SimpleNamespace(
            workspace_base=str(tmp_path),
            docker_container_id="container-id",
            agent_type=agent_type,
            approval_wait_seconds=900,
            max_tool_calls=50,
            max_context_chars=24_000,
            _provider_env={"PHYCODE_API_KEY": "synthetic-value"},
            _tasks={},
        )
        asyncio.run(
            execute(executor, FakeContext(), FakeEventQueue())  # type: ignore[arg-type]
        )

    assert "--approval-wait-seconds 900" in commands[0][-1]
    assert "--max-tool-calls 50" in commands[0][-1]
    assert "--max-context-chars 24000" in commands[0][-1]
    assert "synthetic-value" not in str(commands[0])
    assert "--approval-wait-seconds" not in str(commands[1])
    assert "--max-tool-calls" not in str(commands[1])
    assert "--max-context-chars" not in str(commands[1])


@pytest.mark.parametrize(
    (
        "agent_type",
        "provider_env",
        "constructor_fails",
        "expected_resolves",
        "clears_phycode",
    ),
    [
        ("phycode", {"PHYCODE_API_KEY": "provided"}, False, 0, True),
        ("phycode", None, False, 1, True),
        ("phycode", {"PHYCODE_API_KEY": "provided"}, True, 0, True),
        ("codex", {"PHYCODE_API_KEY": "provided"}, False, 0, False),
    ],
)
def test_official_start_white_clears_original_mapping_after_executor_copy(
    patched_official_evaluator: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent_type: str,
    provider_env: dict[str, str] | None,
    constructor_fails: bool,
    expected_resolves: int,
    clears_phycode: bool,
) -> None:
    provider_names = (
        "PHYCODE_API_KEY",
        "PHYCODE_BASE_URL",
        "PHYCODE_MODEL",
    )
    resolve_calls: list[str] = []
    executor_copies: list[dict[str, str]] = []
    fake_environment = {name: "synthetic" for name in provider_names}
    resolved_provider_env = {"PHYCODE_API_KEY": "resolved"}
    original_provider_env = provider_env or resolved_provider_env
    expected_provider_values = dict(original_provider_env)

    agent_env_module = types.ModuleType("src.my_util.agent_env")
    agent_env_module.PHYCODE_PROVIDER_NAMES = provider_names  # type: ignore[attr-defined]

    def resolve_env(requested_type: str) -> dict[str, str]:
        resolve_calls.append(requested_type)
        return resolved_provider_env

    agent_env_module.resolve_env = resolve_env  # type: ignore[attr-defined]
    src_module = types.ModuleType("src")
    src_module.__path__ = []  # type: ignore[attr-defined]
    util_module = types.ModuleType("src.my_util")
    util_module.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src", src_module)
    monkeypatch.setitem(sys.modules, "src.my_util", util_module)
    monkeypatch.setitem(sys.modules, "src.my_util.agent_env", agent_env_module)

    class FakeExecutor:
        def __init__(self, **kwargs: object) -> None:
            supplied = kwargs["provider_env"]
            assert isinstance(supplied, dict)
            executor_copies.append(dict(supplied))
            if constructor_fails:
                raise RuntimeError("executor construction failed")

    class FakeRequestHandler:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class FakeApplication:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def build(self) -> object:
            return object()

    start_white = _load_function(
        patched_official_evaluator / "src/white_agent/agent.py",
        "start_white_agent",
        {
            "A2AStarletteApplication": FakeApplication,
            "ClaudeCodeWhiteAgentExecutor": FakeExecutor,
            "DefaultRequestHandler": FakeRequestHandler,
            "InMemoryTaskStore": object,
            "logging": types.SimpleNamespace(
                INFO=20,
                basicConfig=lambda **_kwargs: None,
                FileHandler=lambda *_args: object(),
                StreamHandler=lambda *_args: object(),
            ),
            "logger": types.SimpleNamespace(info=lambda *_: None),
            "os": types.SimpleNamespace(environ=fake_environment),
            "prepare_white_agent_card": lambda _url: object(),
            "uvicorn": types.SimpleNamespace(run=lambda *_args, **_kwargs: None),
        },
    )

    if constructor_fails:
        with pytest.raises(RuntimeError, match="executor construction failed"):
            start_white(agent_type=agent_type, provider_env=provider_env)
    else:
        start_white(agent_type=agent_type, provider_env=provider_env)

    assert resolve_calls == ["phycode"] * expected_resolves
    assert executor_copies == [expected_provider_values]
    if clears_phycode:
        assert original_provider_env == {}
        assert all(name not in fake_environment for name in provider_names)
    else:
        assert original_provider_env == expected_provider_values
        assert set(fake_environment) == set(provider_names)


@pytest.mark.parametrize(
    "stale_kind",
    ["regular", "file_symlink", "dangling_symlink", "directory", "unlink_failure"],
)
def test_official_launcher_handles_stale_report_before_container_setup(
    patched_official_evaluator: Path,
    tmp_path: Path,
    stale_kind: str,
) -> None:
    task_root = tmp_path / "tasks/public-task"
    task_root.mkdir(parents=True)
    (task_root / "task.yaml").write_text("public task", encoding="utf-8")
    report_path = task_root / "workspace/eval_logs/eval_report.json"
    report_path.parent.mkdir(parents=True)
    if stale_kind == "directory":
        report_path.mkdir()
    elif stale_kind != "dangling_symlink":
        report_path.write_text('{"stale": true}', encoding="utf-8")

    report_active = True
    original_lexists = os.path.lexists
    original_lstat = os.lstat
    original_unlink = os.unlink

    class ReportPathProxy:
        def __getattr__(self, name: str) -> object:
            return getattr(os.path, name)

        def lexists(self, path: str | os.PathLike[str]) -> bool:
            if os.fspath(path) == str(report_path):
                return report_active
            return original_lexists(path)

    class ReportOsProxy:
        path = ReportPathProxy()

        def __getattr__(self, name: str) -> object:
            return getattr(os, name)

        def lstat(self, path: str | os.PathLike[str]) -> object:
            if os.fspath(path) == str(report_path) and stale_kind in {
                "file_symlink",
                "dangling_symlink",
            }:
                return types.SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
            return original_lstat(path)

        def unlink(self, path: str | os.PathLike[str]) -> None:
            nonlocal report_active
            if os.fspath(path) != str(report_path):
                original_unlink(path)
                return
            if stale_kind == "unlink_failure":
                raise PermissionError("stale report cannot be removed")
            report_active = False
            if original_lexists(path):
                original_unlink(path)

    class ReachedResourcePreparation(RuntimeError):
        pass

    class ReachedContainerSetup(RuntimeError):
        pass

    resource_calls: list[str] = []
    setup_calls: list[str] = []

    def observe_report_cleanup(*_args: object) -> None:
        resource_calls.append("copy")
        if stale_kind in {"regular", "file_symlink", "dangling_symlink"}:
            assert not report_active
            raise ReachedResourcePreparation

    def reject_container_setup(*_args: object, **_kwargs: object) -> object:
        setup_calls.append("setup")
        raise ReachedContainerSetup

    launcher_path = patched_official_evaluator / "src/launcher.py"
    report_os = ReportOsProxy()
    output_helpers = _load_launcher_output_helpers(launcher_path, report_os)
    launch_evaluation = _load_function(
        launcher_path,
        "launch_evaluation",
        {
            "DATA_DIR": str(tmp_path / "tasks"),
            **output_helpers,
            "_copy_input_files": lambda *_args: None,
            "_copy_paper_images": observe_report_cleanup,
            "_copy_paper_markdown": lambda *_args: None,
            "find_free_port_pair": lambda: (9001, 9002),
            "logger": types.SimpleNamespace(
                error=lambda *_: None,
                info=lambda *_: None,
            ),
            "logging": types.SimpleNamespace(
                INFO=20,
                basicConfig=lambda **_kwargs: None,
                FileHandler=lambda *_args: object(),
                StreamHandler=lambda *_args: object(),
            ),
            "os": report_os,
            "resolve_env": lambda _agent_type: {},
            "setup_docker_environment": reject_container_setup,
            "stat": stat,
            "yaml": types.SimpleNamespace(
                safe_load=lambda _handle: {
                    "paper": {"title": "Public task", "author": "Public author"}
                }
            ),
        },
    )
    launch_call = cast(
        Coroutine[object, object, object],
        launch_evaluation(task_id="public-task", green_port=9001, white_port=9002),
    )

    if stale_kind in {"regular", "file_symlink", "dangling_symlink"}:
        with pytest.raises(ReachedResourcePreparation):
            asyncio.run(launch_call)
        assert resource_calls == ["copy"]
    elif stale_kind == "directory":
        with pytest.raises(RuntimeError, match="report"):
            asyncio.run(launch_call)
        assert resource_calls == []
    else:
        with pytest.raises(PermissionError, match="cannot be removed"):
            asyncio.run(launch_call)
        assert resource_calls == []
    assert setup_calls == []


@pytest.mark.parametrize(
    ("outcome", "expected_success"),
    [
        ("green_not_ready", False),
        ("white_not_ready", False),
        ("send_error", False),
        ("no_report", False),
        ("stale_report", False),
        ("report_symlink", False),
        ("report_outside", False),
        ("stale_run_result", False),
        ("missing_run_result", False),
        ("malformed_run_result", False),
        ("run_result_symlink", False),
        ("run_result_outside", False),
        ("run_result_not_completed", False),
        ("grading_error", False),
        ("report", True),
    ],
)
def test_official_launcher_returns_report_success_and_always_runs_cleanup(
    patched_official_evaluator: Path,
    tmp_path: Path,
    outcome: str,
    expected_success: bool,
) -> None:
    task_root = tmp_path / "tasks/public-task"
    task_root.mkdir(parents=True)
    (task_root / "task.yaml").write_text("public task", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text(
        json.dumps(
            {
                "instruction_file": "instruction.md",
                "paper_file": "paper.md",
                "input_files": [],
                "expected_files": [],
            }
        ),
        encoding="utf-8",
    )
    report_path = task_root / "workspace/eval_logs/eval_report.json"
    run_result_path = task_root / "workspace/.phycode/prbench/run_result.json"
    if outcome == "stale_report":
        report_path.parent.mkdir(parents=True)
        report_path.write_text('{"stale": true}', encoding="utf-8")
    if outcome == "stale_run_result":
        run_result_path.parent.mkdir(parents=True)
        run_result_path.write_text('{"status": "completed"}', encoding="utf-8")
    cleanup_events: list[str] = []
    process_calls: list[dict[str, object]] = []
    ready_values = {
        "green_not_ready": [False],
        "white_not_ready": [True, False],
    }.get(outcome, [True, True])

    class FakeProcess:
        pid = 4321

    def fake_process(**kwargs: object) -> FakeProcess:
        process_calls.append(kwargs)
        return FakeProcess()

    class FakeDockerEnvironment:
        container_id = "container-id"

        def get_logs(self) -> str:
            return "public logs"

    class FakeA2A:
        def __init__(self) -> None:
            self.ready = list(ready_values)

        async def wait_agent_ready(self, _url: str, timeout: int) -> bool:
            del timeout
            return self.ready.pop(0)

        async def send_message(self, _url: str, _task: str) -> object:
            if outcome == "send_error":
                raise RuntimeError("send failed")
            if outcome not in {"no_report", "stale_report"}:
                report_path.parent.mkdir(parents=True, exist_ok=True)
                grading = (
                    {"error": "parse_failure"} if outcome == "grading_error" else {}
                )
                report_path.write_text(
                    json.dumps({"grading": grading}), encoding="utf-8"
                )
            if outcome not in {
                "no_report",
                "stale_report",
                "stale_run_result",
                "missing_run_result",
            }:
                run_result_path.parent.mkdir(parents=True, exist_ok=True)
                if outcome == "malformed_run_result":
                    run_result_path.write_text("not-json", encoding="utf-8")
                else:
                    status_value = (
                        "repeated_no_progress"
                        if outcome == "run_result_not_completed"
                        else "completed"
                    )
                    run_result_path.write_text(
                        json.dumps({"status": status_value}), encoding="utf-8"
                    )
            return object()

    original_open = open
    original_realpath = os.path.realpath
    original_lstat = os.lstat

    class ReportPathProxy:
        def __getattr__(self, name: str) -> object:
            return getattr(os.path, name)

        def realpath(self, path: str | os.PathLike[str]) -> str:
            if os.fspath(path) == str(report_path) and outcome == "report_outside":
                return str(tmp_path / "outside/eval_report.json")
            if (
                os.fspath(path) == str(run_result_path)
                and outcome == "run_result_outside"
            ):
                return str(tmp_path / "outside/run_result.json")
            return original_realpath(path)

    class ReportOsProxy:
        path = ReportPathProxy()

        def __getattr__(self, name: str) -> object:
            return getattr(os, name)

        def lstat(self, path: str | os.PathLike[str]) -> object:
            if os.fspath(path) == str(report_path) and outcome == "report_symlink":
                return types.SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
            if (
                os.fspath(path) == str(run_result_path)
                and outcome == "run_result_symlink"
            ):
                return types.SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
            return original_lstat(path)

    def guarded_open(
        path: str | os.PathLike[str],
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ) -> object:
        unsafe_path = (
            os.fspath(path) == str(report_path)
            and outcome in {"report_symlink", "report_outside"}
        ) or (
            os.fspath(path) == str(run_result_path)
            and outcome in {"run_result_symlink", "run_result_outside"}
        )
        if unsafe_path:
            raise AssertionError("untrusted report must not be opened")
        return original_open(path, mode, *args, **kwargs)  # type: ignore[call-overload]

    launcher_path = patched_official_evaluator / "src/launcher.py"
    report_os = ReportOsProxy()
    output_helpers = _load_launcher_output_helpers(
        launcher_path, report_os, open_function=guarded_open
    )
    launch_evaluation = _load_function(
        launcher_path,
        "launch_evaluation",
        {
            "DATA_DIR": str(tmp_path / "tasks"),
            **output_helpers,
            "_archive_trace": lambda *_args: cleanup_events.append("archive-trace"),
            "_archive_workspace": lambda *_args: cleanup_events.append("archive")
            or "archive-destination",
            "_copy_input_files": lambda *_args: None,
            "_copy_paper_images": lambda *_args: None,
            "_copy_paper_markdown": lambda *_args: None,
            "_copy_phycode_controls": lambda *_args: None,
            "_export_traces_for_type": lambda *_args: cleanup_events.append("export"),
            "_kill_process": lambda *_args: cleanup_events.append("kill"),
            "_remove_container": lambda *_args: cleanup_events.append("remove"),
            "_start_without_phycode_environment": lambda *_args: None,
            "find_free_port_pair": lambda: (9001, 9002),
            "json": json,
            "logger": types.SimpleNamespace(
                error=lambda *_: None,
                exception=lambda *_: None,
                info=lambda *_: None,
                warning=lambda *_: None,
            ),
            "logging": types.SimpleNamespace(
                INFO=20,
                basicConfig=lambda **_kwargs: None,
                FileHandler=lambda *_args: object(),
                StreamHandler=lambda *_args: object(),
            ),
            "multiprocessing": types.SimpleNamespace(Process=fake_process),
            "my_a2a": FakeA2A(),
            "open": guarded_open,
            "os": report_os,
            "resolve_env": lambda _agent_type: {},
            "run_with_phycode_cleanup": lambda action, *_args: action(),
            "setup_docker_environment": lambda *_args, **_kwargs: FakeDockerEnvironment(),
            "start_green_agent": object(),
            "start_white_agent": object(),
            "stat": stat,
            "time": types.SimpleNamespace(time=lambda: 1.0),
            "yaml": types.SimpleNamespace(
                safe_load=lambda _handle: {
                    "instruction_file": "instruction.md",
                    "paper": {
                        "title": "Public task",
                        "author": "Public author",
                        "paper_file": "paper.md",
                    }
                }
            ),
        },
    )

    result = asyncio.run(
        cast(
            Coroutine[object, object, object],
            launch_evaluation(
                task_id="public-task",
                green_port=9001,
                white_port=9002,
                white_agent_type="phycode",
                green_agent_type="opencode",
                phycode_contract=str(contract),
                phycode_max_tool_calls=50,
                phycode_max_context_chars=24_000,
            ),
        )
    )

    assert result is expected_success
    assert "remove" in cleanup_events
    assert "archive" in cleanup_events
    assert cleanup_events.count("kill") == 2
    if outcome != "green_not_ready":
        assert process_calls[1]["args"][-2:] == (50, 24_000)  # type: ignore[index]


@pytest.mark.parametrize(
    "report_payload",
    [{"grading": {"error": "parse_failure"}}, []],
)
def test_official_non_phycode_launcher_keeps_upstream_report_success_semantics(
    patched_official_evaluator: Path,
    tmp_path: Path,
    report_payload: object,
) -> None:
    task_root = tmp_path / "tasks/public-task"
    task_root.mkdir(parents=True)
    (task_root / "task.yaml").write_text("public task", encoding="utf-8")
    report_path = task_root / "workspace/eval_logs/eval_report.json"

    class FakeProcess:
        pid = 4321

    class FakeDockerEnvironment:
        container_id = "container-id"

        def get_logs(self) -> str:
            return "public logs"

    class FakeA2A:
        async def wait_agent_ready(self, _url: str, timeout: int) -> bool:
            del timeout
            return True

        async def send_message(self, _url: str, _task: str) -> object:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report_payload),
                encoding="utf-8",
            )
            return object()

    launcher_path = patched_official_evaluator / "src/launcher.py"
    output_helpers = _load_launcher_output_helpers(launcher_path, os)
    launch_evaluation = _load_function(
        launcher_path,
        "launch_evaluation",
        {
            "DATA_DIR": str(tmp_path / "tasks"),
            **output_helpers,
            "_archive_trace": lambda *_args: None,
            "_archive_workspace": lambda *_args: "archive-destination",
            "_copy_input_files": lambda *_args: None,
            "_copy_paper_images": lambda *_args: None,
            "_copy_paper_markdown": lambda *_args: None,
            "_export_traces_for_type": lambda *_args: None,
            "_kill_process": lambda *_args: None,
            "_remove_container": lambda *_args: None,
            "_start_without_phycode_environment": lambda *_args: None,
            "find_free_port_pair": lambda: (9001, 9002),
            "json": json,
            "logger": types.SimpleNamespace(
                error=lambda *_: None,
                exception=lambda *_: None,
                info=lambda *_: None,
                warning=lambda *_: None,
            ),
            "logging": types.SimpleNamespace(
                INFO=20,
                basicConfig=lambda **_kwargs: None,
                FileHandler=lambda *_args: object(),
                StreamHandler=lambda *_args: object(),
            ),
            "multiprocessing": types.SimpleNamespace(
                Process=lambda **_kwargs: FakeProcess()
            ),
            "my_a2a": FakeA2A(),
            "open": open,
            "os": os,
            "resolve_env": lambda _agent_type: {},
            "setup_docker_environment": lambda *_args, **_kwargs: FakeDockerEnvironment(),
            "start_green_agent": object(),
            "start_white_agent": object(),
            "stat": stat,
            "time": types.SimpleNamespace(time=lambda: 1.0),
            "yaml": types.SimpleNamespace(
                safe_load=lambda _handle: {
                    "paper": {
                        "title": "Public task",
                        "author": "Public author",
                        "paper_file": "paper.md",
                    }
                }
            ),
        },
    )

    assert asyncio.run(
        cast(
            Coroutine[object, object, object],
            launch_evaluation(
                task_id="public-task",
                green_port=9001,
                white_port=9002,
                white_agent_type="codex",
                green_agent_type="opencode",
            ),
        )
    ) is True


def test_official_launcher_missing_task_is_failure_and_main_exits_nonzero(
    patched_official_evaluator: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launch_evaluation = _load_function(
        patched_official_evaluator / "src/launcher.py",
        "launch_evaluation",
        {
            "DATA_DIR": str(tmp_path / "missing-tasks"),
            "find_free_port_pair": lambda: (9001, 9002),
            "logger": types.SimpleNamespace(error=lambda *_: None),
            "logging": types.SimpleNamespace(
                INFO=20,
                basicConfig=lambda **_kwargs: None,
                FileHandler=lambda *_args: object(),
                StreamHandler=lambda *_args: object(),
            ),
            "os": os,
            "resolve_env": lambda _agent_type: {},
        },
    )
    assert (
        asyncio.run(
            cast(
                Coroutine[object, object, object],
                launch_evaluation(task_id="missing"),
            )
        )
        is False
    )

    launcher_module = types.ModuleType("src.launcher")

    async def failed_launch(**_kwargs: object) -> bool:
        return False

    launcher_module.launch_evaluation = failed_launch  # type: ignore[attr-defined]
    src_module = types.ModuleType("src")
    src_module.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src", src_module)
    monkeypatch.setitem(sys.modules, "src.launcher", launcher_module)
    launch = _load_function(
        patched_official_evaluator / "main.py",
        "launch",
        {"asyncio": asyncio, "typer": typer},
    )

    with pytest.raises(typer.Exit) as raised:
        launch(
            task_id="missing",
            green_port=9001,
            white_port=9002,
            code_only=False,
            agent_type="claude",
            white_agent_type="phycode",
            green_agent_type="opencode",
            phycode_contract="contract.json",
            phycode_approvals="approvals.json",
            approval_wait_seconds=0,
            no_archive=True,
            results_subdir=None,
        )

    assert raised.value.exit_code == 1


def test_public_smoke_stops_after_first_evaluator_failure_with_fake_uv(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell is unavailable")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uv_log = tmp_path / "uv-calls.log"
    if os.name == "nt":
        (fake_bin / "uv.cmd").write_text(
            "@echo off\r\n"
            ">>\"%FAKE_UV_LOG%\" echo %*\r\n"
            "echo %* | findstr /C:\"apply_adapter.py\" >nul\r\n"
            "if not errorlevel 1 exit /b 0\r\n"
            "echo %* | findstr /C:\"aaatest_helloworld\" >nul\r\n"
            "if not errorlevel 1 exit /b 23\r\n"
            "exit /b 0\r\n",
            encoding="utf-8",
        )
    else:
        fake_uv = fake_bin / "uv"
        fake_uv.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_UV_LOG\"\n"
            "case \"$*\" in *apply_adapter.py*) exit 0 ;; esac\n"
            "case \"$*\" in *aaatest_helloworld*) exit 23 ;; esac\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IXUSR)
    evaluator = tmp_path / "evaluator"
    evaluator.mkdir()
    wheel = tmp_path / "phycode-0.1.5-py3-none-any.whl"
    wheel.write_bytes(b"fake wheel")
    wrapper = tmp_path / "invoke-smoke.ps1"
    wrapper.write_text(
        "param([string]$SmokeScript, [string]$EvaluatorRoot, [string]$WheelPath)\n"
        "$failed = $false\n"
        "try {\n"
        "  & $SmokeScript -EvaluatorRoot $EvaluatorRoot -WheelPath $WheelPath "
        "-TaskIds @('aaatest_helloworld','bbbtest_alphabet')\n"
        "}\n"
        "catch { $failed = $true }\n"
        "if (-not $failed) { exit 91 }\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
    environment.update(
        {
            "FAKE_UV_LOG": str(uv_log),
            "PHYCODE_API_KEY": "synthetic-key",
            "PHYCODE_BASE_URL": "https://synthetic.invalid/v1",
            "PHYCODE_MODEL": "synthetic-model",
        }
    )

    completed = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(wrapper),
            "-SmokeScript",
            str(Path(__file__).resolve().parents[1] / "integrations/prbench/run_public_smoke.ps1"),
            "-EvaluatorRoot",
            str(evaluator),
            "-WheelPath",
            str(wheel),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    calls = uv_log.read_text(encoding="utf-8").splitlines()
    assert sum("apply_adapter.py" in call for call in calls) == 1
    launch_calls = [call for call in calls if "main.py launch" in call]
    assert len(launch_calls) == 1
    assert "aaatest_helloworld" in launch_calls[0]
    assert all("bbbtest_alphabet" not in call for call in calls)


def test_official_agent_env_builds_exact_phycode_name_only_flags_without_regression(
    patched_official_evaluator: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "CC_API_KEY",
        "CC_BASE_URL",
        "CC_MODEL",
        "CC_SMALL_FAST_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_SMALL_FAST_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CC_API_KEY", "ordinary-claude-value")
    monkeypatch.setenv("PHYCODE_API_KEY", "synthetic-phycode-key")
    monkeypatch.setenv("PHYCODE_BASE_URL", "https://synthetic.invalid/v1")
    monkeypatch.setenv("PHYCODE_MODEL", "synthetic-model")
    agent_env = _load_agent_env(
        patched_official_evaluator / "src/my_util/agent_env.py"
    )

    phycode_flags = agent_env.build_docker_exec_env_flags("phycode")
    non_phycode_flags = agent_env.build_docker_exec_env_flags("claude")

    assert phycode_flags == [
        "-e",
        "HOME=/home/agent",
        "-e",
        "PHYCODE_API_KEY",
        "-e",
        "PHYCODE_BASE_URL",
        "-e",
        "PHYCODE_MODEL",
    ]
    assert all("=" not in value for value in phycode_flags[3::2])
    assert "synthetic-phycode-key" not in str(phycode_flags)
    assert non_phycode_flags == [
        "-e",
        "HOME=/home/agent",
        "-e",
        "ANTHROPIC_API_KEY=ordinary-claude-value",
    ]


def test_official_resolve_env_keeps_docstring_first_and_resolves_phycode(
    patched_official_evaluator: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHYCODE_API_KEY", "synthetic-phycode-key")
    monkeypatch.setenv("PHYCODE_BASE_URL", "https://synthetic.invalid/v1")
    monkeypatch.setenv("PHYCODE_MODEL", "synthetic-model")
    agent_env = _load_agent_env(
        patched_official_evaluator / "src/my_util/agent_env.py"
    )

    assert agent_env.resolve_env.__doc__ == (
        "Resolve environment variables for the given agent type."
    )
    assert agent_env.resolve_env("phycode") == {
        "PHYCODE_API_KEY": "synthetic-phycode-key",
        "PHYCODE_BASE_URL": "https://synthetic.invalid/v1",
        "PHYCODE_MODEL": "synthetic-model",
    }


@pytest.mark.parametrize("outcome", ["success", "nonzero", "oserror", "timeout"])
def test_official_green_grading_uses_child_only_environment_and_always_clears_it(
    patched_official_evaluator: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    outcome: str,
) -> None:
    agent_env = _load_agent_env(
        patched_official_evaluator / "src/my_util/agent_env.py"
    )
    resolved_mappings: list[dict[str, str]] = []
    original_resolve = agent_env.resolve_env

    def tracked_resolve(agent_type: str) -> dict[str, str]:
        resolved = original_resolve(agent_type)
        resolved_mappings.append(resolved)
        return resolved

    monkeypatch.setattr(agent_env, "resolve_env", tracked_resolve)
    monkeypatch.setenv("OPENCODE_API_KEY", "green-sensitive-value")
    monkeypatch.setenv("OPENCODE_BASE_URL", "https://green-sensitive.invalid/v1")
    monkeypatch.setenv("OPENCODE_MODEL", "openai/green-model")

    src_module = types.ModuleType("src")
    src_module.__path__ = []  # type: ignore[attr-defined]
    util_module = types.ModuleType("src.my_util")
    util_module.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src", src_module)
    monkeypatch.setitem(sys.modules, "src.my_util", util_module)
    monkeypatch.setitem(sys.modules, "src.my_util.agent_env", agent_env)

    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> types.SimpleNamespace:
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        captured["kwargs"] = kwargs
        child_env = kwargs["env"]
        assert isinstance(child_env, dict)
        assert child_env["OPENAI_API_KEY"] == "green-sensitive-value"
        assert child_env["OPENAI_BASE_URL"] == "https://green-sensitive.invalid/v1"
        assert child_env["OPENCODE_MODEL"] == "openai_compat/green-model"
        inline_config = child_env["OPENCODE_CONFIG_CONTENT"]
        assert isinstance(inline_config, str)
        assert "green-sensitive-value" not in inline_config
        assert json.loads(inline_config) == {
            "model": "openai_compat/green-model",
            "provider": {
                "openai_compat": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "OpenAI Compatible",
                    "options": {
                        "baseURL": "https://green-sensitive.invalid/v1",
                        "apiKey": "{env:OPENAI_API_KEY}",
                    },
                    "models": {"green-model": {}},
                }
            },
        }
        if outcome == "oserror":
            raise OSError("docker spawn failed")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(cmd, 600)
        return types.SimpleNamespace(
            returncode=17 if outcome == "nonzero" else 0,
            stdout='{"overall_score": 1.0}',
            stderr="nonzero" if outcome == "nonzero" else "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_grading = _load_function(
        patched_official_evaluator / "src/green_agent/agent.py",
        "_run_grading",
        {
            "logger": types.SimpleNamespace(
                info=lambda *_: None,
                warning=lambda *_: None,
                error=lambda *_: None,
            ),
            "os": os,
            "_parse_grading_json": json.loads,
        },
    )
    method = types.MethodType(run_grading, object())

    if outcome == "oserror":
        with pytest.raises(OSError, match="docker spawn failed"):
            method("grade this", "container-id", str(tmp_path), "opencode", True)
    else:
        method("grade this", "container-id", str(tmp_path), "opencode", True)

    command = captured["cmd"]
    kwargs = captured["kwargs"]
    assert isinstance(command, list)
    assert isinstance(kwargs, dict)
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert "green-sensitive-value" not in str(command)
    env_values = [
        command[index + 1]
        for index, value in enumerate(command)
        if value == "-e"
    ]
    assert env_values
    assert all("=" not in value for value in env_values)
    assert env_values == [
        "HOME",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENCODE_MODEL",
        "OPENCODE_CONFIG_CONTENT",
    ]

    child_env = captured["env"]
    assert isinstance(child_env, dict)
    assert "OPENAI_API_KEY" not in child_env
    assert "OPENAI_BASE_URL" not in child_env
    assert "OPENCODE_MODEL" not in child_env
    assert "OPENCODE_CONFIG_CONTENT" not in child_env
    assert resolved_mappings
    assert all(mapping == {} for mapping in resolved_mappings)


def test_official_deferred_green_grading_decodes_invalid_utf8_and_redacts_trace(
    patched_official_evaluator: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key = "synthetic-green-key-for-trace"
    base_url = "https://synthetic-green.invalid/v1"
    model = "openai/trace-model"
    monkeypatch.setenv("OPENCODE_API_KEY", key)
    monkeypatch.setenv("OPENCODE_BASE_URL", base_url)
    monkeypatch.setenv("OPENCODE_MODEL", model)

    agent_env = _load_agent_env(
        patched_official_evaluator / "src/my_util/agent_env.py"
    )
    src_module = types.ModuleType("src")
    src_module.__path__ = []  # type: ignore[attr-defined]
    util_module = types.ModuleType("src.my_util")
    util_module.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src", src_module)
    monkeypatch.setitem(sys.modules, "src.my_util", util_module)
    monkeypatch.setitem(sys.modules, "src.my_util.agent_env", agent_env)

    expected_provider_values: list[str] = []
    original_build = agent_env.build_green_child_environment

    def tracked_build(
        agent_type: str,
    ) -> tuple[list[str], dict[str, str], dict[str, str]]:
        env_flags, provider_env, process_env = original_build(agent_type)
        expected_provider_values.extend(
            value for value in provider_env.values() if value
        )
        return env_flags, provider_env, process_env

    monkeypatch.setattr(agent_env, "build_green_child_environment", tracked_build)
    real_run = subprocess.run

    def run_invalid_utf8_child(
        _cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        child_env = dict(cast(dict[str, str], kwargs["env"]))
        child_env["GRADING_STDOUT_PROBE"] = '{"overall_score": 1.0}'
        child_env["GRADING_STDERR_PROBE"] = "\n".join(expected_provider_values)
        child_kwargs = dict(kwargs)
        child_kwargs["env"] = child_env
        return cast(Any, real_run)(
            [
                sys.executable,
                "-c",
                (
                    "import os, sys; "
                    "sys.stdout.buffer.write(os.environ['GRADING_STDOUT_PROBE'].encode('utf-8')); "
                    "sys.stderr.buffer.write(os.environ['GRADING_STDERR_PROBE'].encode('utf-8') + bytes([0x92]))"
                ),
            ],
            **child_kwargs,
        )

    monkeypatch.setattr(subprocess, "run", run_invalid_utf8_child)
    run_grading = _load_function(
        patched_official_evaluator / "src/green_agent/agent.py",
        "_run_grading",
        {
            "logger": types.SimpleNamespace(
                info=lambda *_: None,
                warning=lambda *_: None,
                error=lambda *_: None,
            ),
            "os": os,
            "_parse_grading_json": json.loads,
        },
    )
    method = types.MethodType(run_grading, object())

    grading = method("grade this", "container-id", str(tmp_path), "opencode", True)

    assert grading == {"overall_score": 1.0}
    trace = (tmp_path / "grading_trace.log").read_text(encoding="utf-8")
    assert "\ufffd" in trace
    assert expected_provider_values
    assert all(value not in trace for value in expected_provider_values)
    assert "[REDACTED_PROVIDER_VALUE]" in trace


@pytest.mark.parametrize("parse_mode", ["valid_json", "parse_failure"])
def test_official_deferred_green_grading_redacts_recursive_parser_result(
    patched_official_evaluator: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    parse_mode: str,
) -> None:
    key = "synthetic-result-key"
    base_url = "https://synthetic-result.invalid/v1"
    model = "openai/result-model"
    monkeypatch.setenv("OPENCODE_API_KEY", key)
    monkeypatch.setenv("OPENCODE_BASE_URL", base_url)
    monkeypatch.setenv("OPENCODE_MODEL", model)

    agent_env = _load_agent_env(
        patched_official_evaluator / "src/my_util/agent_env.py"
    )
    resolved = agent_env.resolve_env("opencode")
    inline_config = agent_env._build_opencode_config_content(resolved)
    provider_values = [
        key,
        base_url,
        "openai_compat/result-model",
        inline_config,
    ]
    assert all(provider_values)

    src_module = types.ModuleType("src")
    src_module.__path__ = []  # type: ignore[attr-defined]
    util_module = types.ModuleType("src.my_util")
    util_module.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src", src_module)
    monkeypatch.setitem(sys.modules, "src.my_util", util_module)
    monkeypatch.setitem(sys.modules, "src.my_util.agent_env", agent_env)

    if parse_mode == "valid_json":
        stdout = json.dumps(
            {
                "overall_score": 1.0,
                f"provider-{key}": [
                    key,
                    {"endpoint": base_url, "config": inline_config},
                ],
            }
        )
    else:
        stdout = "unparseable grading output: " + " | ".join(provider_values)

    parser_inputs: list[str] = []

    def parse_grading(text: str) -> dict[str, object]:
        parser_inputs.append(text)
        if parse_mode == "parse_failure":
            return {
                "error": "parse_failure",
                "overall_score": 0.0,
                "scores": {},
                "summary": text[:500],
            }
        parsed = cast(dict[str, object], json.loads(text))
        parsed["tuple_probe"] = (provider_values[2], False, None, 7)
        return parsed

    def fake_run(cmd: list[str], **kwargs: object) -> types.SimpleNamespace:
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        return types.SimpleNamespace(returncode=0, stdout=stdout, stderr=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_grading = _load_function(
        patched_official_evaluator / "src/green_agent/agent.py",
        "_run_grading",
        {
            "logger": types.SimpleNamespace(
                info=lambda *_: None,
                warning=lambda *_: None,
                error=lambda *_: None,
            ),
            "os": os,
            "_parse_grading_json": parse_grading,
        },
    )
    method = types.MethodType(run_grading, object())

    grading = method("grade this", "container-id", str(tmp_path), "opencode", True)

    assert parser_inputs == [stdout]

    def recursive_strings(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [
                text
                for item in value.items()
                for element in item
                for text in recursive_strings(element)
            ]
        if isinstance(value, (list, tuple)):
            return [text for item in value for text in recursive_strings(item)]
        return []

    returned_strings = recursive_strings(grading)
    trace = (tmp_path / "grading_trace.log").read_text(encoding="utf-8")
    assert all(
        provider_value not in text
        for provider_value in provider_values
        for text in returned_strings
    )
    assert all(provider_value not in trace for provider_value in provider_values)
    assert any(
        "[REDACTED_PROVIDER_VALUE]" in text for text in returned_strings
    )
    if parse_mode == "valid_json":
        assert grading["overall_score"] == 1.0
        assert grading["tuple_probe"] == (
            "[REDACTED_PROVIDER_VALUE]",
            False,
            None,
            7,
        )


def _install_synthetic_green_agent_env(
    monkeypatch: pytest.MonkeyPatch,
    provider_values: dict[str, str],
) -> list[tuple[dict[str, str], dict[str, str]]]:
    agent_env = types.ModuleType("src.my_util.agent_env")
    transient_mappings: list[tuple[dict[str, str], dict[str, str]]] = []

    def build_green_child_environment(
        agent_type: str,
    ) -> tuple[list[str], dict[str, str], dict[str, str]]:
        assert agent_type == "codex"
        provider_env = dict(provider_values)
        process_env = {"SAFE": "kept", **provider_env}
        transient_mappings.append((provider_env, process_env))
        flags = [item for name in provider_env for item in ("-e", name)]
        return flags, provider_env, process_env

    def clear_green_child_environment(
        provider_env: dict[str, str], process_env: dict[str, str]
    ) -> None:
        provider_env.clear()
        process_env.clear()

    setattr(agent_env, "build_green_child_environment", build_green_child_environment)
    setattr(agent_env, "clear_green_child_environment", clear_green_child_environment)
    setattr(agent_env, "build_docker_exec_env_flags", lambda _agent_type: [])
    setattr(agent_env, "resolve_env", lambda _agent_type: {})
    src_module = types.ModuleType("src")
    src_module.__path__ = []  # type: ignore[attr-defined]
    util_module = types.ModuleType("src.my_util")
    util_module.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src", src_module)
    monkeypatch.setitem(sys.modules, "src.my_util", util_module)
    monkeypatch.setitem(sys.modules, "src.my_util.agent_env", agent_env)
    return transient_mappings


def _create_unrelated_grading_temp_sentinel(tmp_path: Path) -> tuple[Path, str, int]:
    sentinel = tmp_path / "._grading_output.unrelated.tmp"
    content = "unrelated grading temp sentinel"
    sentinel.write_text(content, encoding="utf-8")
    return sentinel, content, sentinel.stat().st_ino


def _assert_unrelated_grading_temp_sentinel(
    sentinel: Path, content: str, inode: int
) -> None:
    assert sentinel.read_text(encoding="utf-8") == content
    assert sentinel.stat().st_ino == inode


@pytest.mark.parametrize("parse_mode", ["valid_json", "parse_failure"])
def test_official_deferred_codex_sanitizes_last_message_file(
    patched_official_evaluator: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    parse_mode: str,
) -> None:
    provider_values = {
        "OPENAI_API_KEY": "synthetic-codex-file-key",
        "OPENAI_BASE_URL": "https://synthetic-codex-file.invalid/v1",
        "OPENAI_MODEL": "synthetic-codex-file-model",
        "CODEX_INLINE_CONFIG": '{"baseURL":"https://synthetic-codex-file.invalid/v1"}',
    }
    transient_mappings = _install_synthetic_green_agent_env(
        monkeypatch, provider_values
    )
    if parse_mode == "valid_json":
        raw_output = json.dumps(
            {
                "overall_score": 1.0,
                "provider": list(provider_values.values()),
            }
        )
    else:
        raw_output = "unparseable: " + " | ".join(provider_values.values())
    output_path = tmp_path / "_grading_output.txt"

    def fake_run(cmd: list[str], **kwargs: object) -> types.SimpleNamespace:
        assert not output_path.exists()
        output_path.write_text(raw_output, encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout=raw_output, stderr=raw_output)

    parser_inputs: list[str] = []

    def parse_grading(text: str) -> dict[str, object]:
        parser_inputs.append(text)
        if parse_mode == "parse_failure":
            return {
                "error": "parse_failure",
                "overall_score": 0.0,
                "scores": {},
                "summary": text[:500],
            }
        return cast(dict[str, object], json.loads(text))

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_grading = _load_function(
        patched_official_evaluator / "src/green_agent/agent.py",
        "_run_grading",
        {
            "logger": types.SimpleNamespace(
                info=lambda *_: None,
                warning=lambda *_: None,
                error=lambda *_: None,
            ),
            "os": os,
            "_parse_grading_json": parse_grading,
        },
    )
    method = types.MethodType(run_grading, object())

    grading = method("grade this", "container-id", str(tmp_path), "codex", True)

    assert parser_inputs == [raw_output]
    persisted = output_path.read_text(encoding="utf-8")
    trace = (tmp_path / "grading_trace.log").read_text(encoding="utf-8")
    serialized_result = json.dumps(grading, default=list)
    for provider_value in provider_values.values():
        assert provider_value not in persisted
        assert provider_value not in trace
        assert provider_value not in serialized_result
    assert "[REDACTED_PROVIDER_VALUE]" in persisted
    assert transient_mappings
    assert all(provider == {} and process == {} for provider, process in transient_mappings)


def test_official_non_deferred_codex_preserves_last_message_file(
    patched_official_evaluator: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw_output = '{"overall_score": 1.0, "echo": "ordinary-codex-value"}'
    output_path = tmp_path / "_grading_output.txt"

    def fake_run(cmd: list[str], **kwargs: object) -> types.SimpleNamespace:
        output_path.write_text(raw_output, encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    _install_synthetic_green_agent_env(monkeypatch, {})
    monkeypatch.setattr(subprocess, "run", fake_run)
    run_grading = _load_function(
        patched_official_evaluator / "src/green_agent/agent.py",
        "_run_grading",
        {
            "logger": types.SimpleNamespace(
                info=lambda *_: None,
                warning=lambda *_: None,
                error=lambda *_: None,
            ),
            "os": os,
            "_parse_grading_json": json.loads,
        },
    )
    method = types.MethodType(run_grading, object())

    grading = method("grade this", "container-id", str(tmp_path), "codex", False)

    assert grading["echo"] == "ordinary-codex-value"
    assert output_path.read_text(encoding="utf-8") == raw_output


def test_official_deferred_codex_removes_raw_file_when_atomic_publish_fails(
    patched_official_evaluator: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider_values = {"OPENAI_API_KEY": "synthetic-atomic-failure-key"}
    transient_mappings = _install_synthetic_green_agent_env(
        monkeypatch, provider_values
    )
    raw_output = json.dumps(
        {"overall_score": 1.0, "echo": provider_values["OPENAI_API_KEY"]}
    )
    output_path = tmp_path / "_grading_output.txt"

    def fake_run(cmd: list[str], **kwargs: object) -> types.SimpleNamespace:
        output_path.write_text(raw_output, encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout=raw_output, stderr="")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(os, "replace", fail_replace)
    run_grading = _load_function(
        patched_official_evaluator / "src/green_agent/agent.py",
        "_run_grading",
        {
            "logger": types.SimpleNamespace(
                info=lambda *_: None,
                warning=lambda *_: None,
                error=lambda *_: None,
            ),
            "os": os,
            "_parse_grading_json": json.loads,
        },
    )
    method = types.MethodType(run_grading, object())

    with pytest.raises(RuntimeError, match="failed to sanitize grading output"):
        method("grade this", "container-id", str(tmp_path), "codex", True)

    assert not output_path.exists()
    assert not tuple(tmp_path.glob("._grading_output.*.tmp"))
    assert transient_mappings
    assert all(provider == {} and process == {} for provider, process in transient_mappings)


def test_official_deferred_codex_removes_raw_file_when_child_times_out(
    patched_official_evaluator: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider_values = {"OPENAI_API_KEY": "synthetic-timeout-file-key"}
    transient_mappings = _install_synthetic_green_agent_env(
        monkeypatch, provider_values
    )
    raw_output = json.dumps(
        {"overall_score": 0.0, "echo": provider_values["OPENAI_API_KEY"]}
    )
    output_path = tmp_path / "_grading_output.txt"
    sentinel, sentinel_content, sentinel_inode = (
        _create_unrelated_grading_temp_sentinel(tmp_path)
    )

    def timeout_after_write(
        cmd: list[str], **kwargs: object
    ) -> types.SimpleNamespace:
        output_path.write_text(raw_output, encoding="utf-8")
        raise subprocess.TimeoutExpired(cmd, 600)

    monkeypatch.setattr(subprocess, "run", timeout_after_write)
    run_grading = _load_function(
        patched_official_evaluator / "src/green_agent/agent.py",
        "_run_grading",
        {
            "logger": types.SimpleNamespace(
                info=lambda *_: None,
                warning=lambda *_: None,
                error=lambda *_: None,
            ),
            "os": os,
            "_parse_grading_json": json.loads,
        },
    )
    method = types.MethodType(run_grading, object())

    grading = method("grade this", "container-id", str(tmp_path), "codex", True)

    assert grading["error"] == "timeout"
    assert not output_path.exists()
    _assert_unrelated_grading_temp_sentinel(
        sentinel, sentinel_content, sentinel_inode
    )
    assert transient_mappings
    assert all(provider == {} and process == {} for provider, process in transient_mappings)


def test_official_deferred_codex_removes_exact_raw_file_when_spawn_raises_oserror(
    patched_official_evaluator: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider_values = {"OPENAI_API_KEY": "synthetic-spawn-oserror-key"}
    transient_mappings = _install_synthetic_green_agent_env(
        monkeypatch, provider_values
    )
    raw_output = json.dumps(
        {"overall_score": 0.0, "echo": provider_values["OPENAI_API_KEY"]}
    )
    output_path = tmp_path / "_grading_output.txt"
    sentinel, sentinel_content, sentinel_inode = (
        _create_unrelated_grading_temp_sentinel(tmp_path)
    )

    def fail_spawn_after_write(cmd: list[str], **kwargs: object) -> object:
        output_path.write_text(raw_output, encoding="utf-8")
        raise OSError("synthetic spawn failure")

    monkeypatch.setattr(subprocess, "run", fail_spawn_after_write)
    run_grading = _load_function(
        patched_official_evaluator / "src/green_agent/agent.py",
        "_run_grading",
        {
            "logger": types.SimpleNamespace(
                info=lambda *_: None,
                warning=lambda *_: None,
                error=lambda *_: None,
            ),
            "os": os,
            "_parse_grading_json": json.loads,
        },
    )
    method = types.MethodType(run_grading, object())

    with pytest.raises(OSError, match="synthetic spawn failure"):
        method("grade this", "container-id", str(tmp_path), "codex", True)

    assert not output_path.exists()
    _assert_unrelated_grading_temp_sentinel(
        sentinel, sentinel_content, sentinel_inode
    )
    assert transient_mappings
    assert all(provider == {} and process == {} for provider, process in transient_mappings)


def test_official_deferred_codex_sanitizes_file_before_trace_write_failure(
    patched_official_evaluator: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider_values = {"OPENAI_API_KEY": "synthetic-trace-file-key"}
    transient_mappings = _install_synthetic_green_agent_env(
        monkeypatch, provider_values
    )
    raw_output = json.dumps(
        {"overall_score": 1.0, "echo": provider_values["OPENAI_API_KEY"]}
    )
    output_path = tmp_path / "_grading_output.txt"
    sentinel, sentinel_content, sentinel_inode = (
        _create_unrelated_grading_temp_sentinel(tmp_path)
    )
    real_open = open

    def fail_trace_open(path: str, *args: object, **kwargs: object) -> object:
        if Path(path).name == "grading_trace.log":
            raise OSError("synthetic trace write failure")
        return real_open(path, *args, **kwargs)  # type: ignore[call-overload]

    def fake_run(cmd: list[str], **kwargs: object) -> types.SimpleNamespace:
        output_path.write_text(raw_output, encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout=raw_output, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_grading = _load_function(
        patched_official_evaluator / "src/green_agent/agent.py",
        "_run_grading",
        {
            "logger": types.SimpleNamespace(
                info=lambda *_: None,
                warning=lambda *_: None,
                error=lambda *_: None,
            ),
            "open": fail_trace_open,
            "os": os,
            "_parse_grading_json": json.loads,
        },
    )
    method = types.MethodType(run_grading, object())

    with pytest.raises(RuntimeError, match="failed to write grading trace"):
        method("grade this", "container-id", str(tmp_path), "codex", True)

    assert output_path.exists()
    persisted = output_path.read_text(encoding="utf-8")
    assert provider_values["OPENAI_API_KEY"] not in persisted
    assert "[REDACTED_PROVIDER_VALUE]" in persisted
    _assert_unrelated_grading_temp_sentinel(
        sentinel, sentinel_content, sentinel_inode
    )
    assert transient_mappings
    assert all(provider == {} and process == {} for provider, process in transient_mappings)


@pytest.mark.parametrize("unsafe_path", ["symlink", "escape"])
def test_official_deferred_codex_rejects_unsafe_last_message_before_spawn(
    patched_official_evaluator: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    provider_values = {"OPENAI_API_KEY": "synthetic-symlink-key"}
    transient_mappings = _install_synthetic_green_agent_env(
        monkeypatch, provider_values
    )
    target = tmp_path / f"codex-target-{tmp_path.name}.txt"
    raw_output = json.dumps(
        {"overall_score": 1.0, "echo": provider_values["OPENAI_API_KEY"]}
    )
    target.write_text(raw_output, encoding="utf-8")
    output_path = tmp_path / "_grading_output.txt"
    output_path.write_text(raw_output, encoding="utf-8")
    if unsafe_path == "symlink":
        original_lstat = os.lstat

        def synthetic_lstat(path: str) -> object:
            if os.path.normcase(path) == os.path.normcase(str(output_path)):
                return types.SimpleNamespace(st_mode=stat.S_IFLNK)
            return original_lstat(path)

        monkeypatch.setattr(os, "lstat", synthetic_lstat)
    else:
        original_realpath = os.path.realpath
        outside_path = tmp_path.parent / "escaped-grading-output.txt"

        def synthetic_realpath(path: str) -> str:
            if os.path.normcase(path) == os.path.normcase(str(output_path)):
                return str(outside_path)
            return original_realpath(path)

        monkeypatch.setattr(os.path, "realpath", synthetic_realpath)
    spawned = False

    def fake_run(cmd: list[str], **kwargs: object) -> types.SimpleNamespace:
        nonlocal spawned
        spawned = True
        return types.SimpleNamespace(returncode=0, stdout=raw_output, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_grading = _load_function(
        patched_official_evaluator / "src/green_agent/agent.py",
        "_run_grading",
        {
            "logger": types.SimpleNamespace(
                info=lambda *_: None,
                warning=lambda *_: None,
                error=lambda *_: None,
            ),
            "os": os,
            "_parse_grading_json": json.loads,
        },
    )
    method = types.MethodType(run_grading, object())

    with pytest.raises(RuntimeError, match="unsafe grading output path"):
        method("grade this", "container-id", str(tmp_path), "codex", True)

    assert not spawned
    assert target.read_text(encoding="utf-8") == raw_output
    assert transient_mappings
    assert all(provider == {} and process == {} for provider, process in transient_mappings)


def test_official_non_phycode_green_grading_retains_upstream_transport(
    patched_official_evaluator: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    agent_env = _load_agent_env(
        patched_official_evaluator / "src/my_util/agent_env.py"
    )
    monkeypatch.setenv("OPENCODE_API_KEY", "ordinary-combination-value")
    monkeypatch.setenv("OPENCODE_MODEL", "openai/ordinary-model")

    src_module = types.ModuleType("src")
    src_module.__path__ = []  # type: ignore[attr-defined]
    util_module = types.ModuleType("src.my_util")
    util_module.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src", src_module)
    monkeypatch.setitem(sys.modules, "src.my_util", util_module)
    monkeypatch.setitem(sys.modules, "src.my_util.agent_env", agent_env)

    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> types.SimpleNamespace:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return types.SimpleNamespace(
            returncode=0,
            stdout=(
                '{"overall_score": 1.0, '
                '"echo": "ordinary-combination-value"}'
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_grading = _load_function(
        patched_official_evaluator / "src/green_agent/agent.py",
        "_run_grading",
        {
            "logger": types.SimpleNamespace(
                info=lambda *_: None,
                warning=lambda *_: None,
                error=lambda *_: None,
            ),
            "os": os,
            "_parse_grading_json": json.loads,
        },
    )
    method = types.MethodType(run_grading, object())

    grading = method(
        "grade this", "container-id", str(tmp_path), "opencode", False
    )

    command = captured["cmd"]
    kwargs = captured["kwargs"]
    assert isinstance(command, list)
    assert isinstance(kwargs, dict)
    assert "env" not in kwargs
    assert "OPENAI_API_KEY=ordinary-combination-value" in command
    assert grading["echo"] == "ordinary-combination-value"
    assert "ordinary-combination-value" in (
        tmp_path / "grading_trace.log"
    ).read_text(encoding="utf-8")


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
        "uv pip install --system /tmp/phycode-0.1.5-py3-none-any.whl" in added
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

    assert 'defer_green_env = white_agent_type == "phycode"' in added
    assert 'defer_green_env and t == green_agent_type' in added
    assert "_start_without_phycode_environment" in added
    assert "process_env.update(self._provider_env)" in added
    assert "run_with_phycode_cleanup" in added
    assert "clear_phycode_environment" in added
    assert 'os.environ.pop(name, None)' in added
    assert 'env_vars.update(resolve_env("phycode"))' not in added


def test_patch_green_environment_is_name_only_and_finally_cleared() -> None:
    green = _patch_section("src/green_agent/agent.py")
    helper = _patch_section("src/my_util/agent_env.py")

    assert "build_green_child_environment" in green
    assert 'run_env = {"env": green_process_env} if defer_provider_env else {}' in green
    assert "**run_env" in green
    assert "finally:" in green
    assert "clear_green_child_environment" in green
    assert 'effective_green_type, effective_white_type == "phycode"' in _patch_section(
        "src/launcher.py"
    )
    assert 'env_flags.extend(["-e", name])' in helper
    assert 'f"{key}={val}"' not in green


def test_patch_cleanup_wrapper_clears_credentials_when_spawn_raises(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    helper_hunk = next(
        (
            hunk
            for hunk in _added_hunks("src/my_util/agent_env.py")
            if "def run_with_phycode_cleanup" in hunk
        ),
        None,
    )
    assert helper_hunk is not None
    provider_names = ("PHYCODE_API_KEY", "PHYCODE_BASE_URL", "PHYCODE_MODEL")
    namespace: dict[str, object] = {
        "os": os,
        "PHYCODE_PROVIDER_NAMES": provider_names,
    }
    exec(compile(helper_hunk, "<phycode-agent-env-patch>", "exec"), namespace)
    run_with_cleanup = namespace["run_with_phycode_cleanup"]

    secret = "sensitive-provider-value"
    provider_env = {
        "PHYCODE_API_KEY": secret,
        "PHYCODE_BASE_URL": "https://sensitive.invalid/v1",
        "PHYCODE_MODEL": "sensitive-model",
    }
    process_env = {**provider_env, "SAFE": "kept"}
    for name, value in provider_env.items():
        monkeypatch.setenv(name, value)

    def fail_spawn() -> None:
        raise RuntimeError(secret)

    with pytest.raises(RuntimeError, match=secret):
        run_with_cleanup(fail_spawn, provider_env, process_env)  # type: ignore[operator]

    assert provider_env == {}
    assert process_env == {"SAFE": "kept"}
    assert all(name not in os.environ for name in provider_names)
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err


def test_patch_routes_launcher_and_white_spawns_through_cleanup_wrapper() -> None:
    launcher = _patch_section("src/launcher.py")
    white = _patch_section("src/white_agent/agent.py")

    assert "run_with_phycode_cleanup" in launcher
    assert "run_with_phycode_cleanup" in white
    assert "lambda: _start_without_phycode_environment(p_white)" in launcher
    assert "_launch_process" in white


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
    assert contract.execution_entrypoints == (expected_files[0],)
    assert len(contract.constraints) == 1
    assert contract.constraints[0].csv_header == header
    assert contract.constraints[0].csv_rows == rows
    assert "metadata" not in raw.casefold()
    assert "ground_truth" not in raw.casefold()


def test_full_public_contract_uses_only_instruction_declared_artifacts() -> None:
    path = Path("integrations/prbench/public_contracts/task_white_1993.json")
    raw = path.read_text(encoding="utf-8")
    contract = TaskContract.model_validate_json(raw)

    assert contract.instruction_file == "instruction.md"
    assert contract.paper_file == "white1993.md"
    assert contract.input_files == ()
    assert contract.expected_files == (
        "reproduction/ANALYSIS.md",
        "reproduction/operators.py",
        "reproduction/block.py",
        "reproduction/superblock.py",
        "reproduction/dmrg_infinite.py",
        "reproduction/dmrg_finite.py",
        "reproduction/fig2_compute.py",
        "reproduction/fig3_compute.py",
        "reproduction/fig4_compute.py",
        "reproduction/fig5_compute.py",
        "reproduction/fig6_compute.py",
        "reproduction/fig7_compute.py",
        "reproduction/fig8_compute.py",
        "data/fig2.csv",
        "data/fig3.csv",
        "data/fig4.csv",
        "data/fig5.csv",
        "data/fig6.csv",
        "data/fig7.csv",
        "data/fig8.csv",
    )
    assert contract.execution_entrypoints == tuple(
        f"reproduction/fig{figure}_compute.py" for figure in range(2, 9)
    )
    assert {
        item.path: (item.csv_header, item.csv_data_row_count)
        for item in contract.constraints
    } == {
        "data/fig2.csv": (
            (
                "alpha",
                "Open, S=1/2",
                "Open, S=1",
                "Periodic, S=1/2",
                "Periodic, S=1",
            ),
            50,
        ),
        "data/fig3.csv": (("m", "Open BCs", "Periodic BCs"), 24),
        "data/fig4.csv": (("m", "Open BCs", "Periodic BCs"), 24),
        "data/fig5.csv": (("L", "1/L", "Gap S=1/2", "Gap S=1"), 15),
        "data/fig6.csv": (
            ("i", "Panel (a)", "Panel (b)", "Panel (c)"),
            60,
        ),
        "data/fig7.csv": (("i", "Bond Strength", "Local Sz"), 60),
        "data/fig8.csv": (
            (
                "alpha",
                "1 Target",
                "2 Targets",
                "3 Targets",
                "4 Targets",
                "5 Targets",
            ),
            50,
        ),
    }
    assert all(item.csv_rows is None for item in contract.constraints)
    assert "metadata" not in raw.casefold()
    assert "ground_truth" not in raw.casefold()
    assert "reference" not in raw.casefold()
