from __future__ import annotations

import asyncio
import ast
import importlib.util
import json
import os
import subprocess
import sys
import types
from collections.abc import Callable
from pathlib import Path

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
    wheel = tmp_path / "phycode-0.1.0-py3-none-any.whl"
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
    wheel = tmp_path / "phycode-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"dynamic-adapter-probe")
    apply_adapter(repository, wheel)
    return repository


def _load_function(
    source: Path, function_name: str, globals_: dict[str, object]
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
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            ),
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


def _load_agent_env(source: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("patched_agent_env", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_official_phycode_setup_defers_green_credentials_until_grading(
    patched_official_evaluator: Path,
) -> None:
    calls: list[str] = []
    instances: list[object] = []

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

        def install_opencode(self) -> bool:
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
            "DockerEnvironment": FakeDockerEnvironment,
            "PROJECT_ROOT": str(patched_official_evaluator),
            "logger": types.SimpleNamespace(info=lambda *_: None),
            "os": os,
            "resolve_env": resolve_env,
        },
    )
    config = {"docker": {}}

    setup(config, "task", "workspace", "phycode", "opencode")  # type: ignore[operator]

    assert calls == []
    assert instances[-1].env_vars == {}  # type: ignore[attr-defined]

    setup(config, "task", "workspace", "claude", "opencode")  # type: ignore[operator]

    assert set(calls) == {"claude", "opencode"}
    assert instances[-1].env_vars == {  # type: ignore[attr-defined]
        "CLAUDE_TOKEN": "claude-secret",
        "OPENCODE_TOKEN": "opencode-secret",
    }


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


def test_official_main_and_white_runner_pass_approval_wait_only_to_phycode(
    patched_official_evaluator: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launch_calls: list[dict[str, object]] = []

    async def fake_launch_evaluation(**kwargs: object) -> None:
        launch_calls.append(kwargs)

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
        no_archive=True,
        results_subdir=None,
    )

    assert launch_calls[0]["approval_wait_seconds"] == 900

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
            _provider_env={"PHYCODE_API_KEY": "synthetic-value"},
            _tasks={},
        )
        asyncio.run(
            execute(executor, FakeContext(), FakeEventQueue())  # type: ignore[arg-type]
        )

    assert "--approval-wait-seconds 900" in commands[0][-1]
    assert "synthetic-value" not in str(commands[0])
    assert "--approval-wait-seconds" not in str(commands[1])


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
        child_env = kwargs["env"]
        assert isinstance(child_env, dict)
        assert child_env["OPENAI_API_KEY"] == "green-sensitive-value"
        assert child_env["OPENAI_BASE_URL"] == "https://green-sensitive.invalid/v1"
        assert child_env["OPENCODE_MODEL"] == "openai_compat/green-model"
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
    assert isinstance(command, list)
    assert "green-sensitive-value" not in str(command)
    env_values = [
        command[index + 1]
        for index, value in enumerate(command)
        if value == "-e"
    ]
    assert env_values
    assert all("=" not in value for value in env_values)
    assert {"HOME", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENCODE_MODEL"}.issubset(
        env_values
    )

    child_env = captured["env"]
    assert isinstance(child_env, dict)
    assert "OPENAI_API_KEY" not in child_env
    assert "OPENAI_BASE_URL" not in child_env
    assert "OPENCODE_MODEL" not in child_env
    assert resolved_mappings
    assert all(mapping == {} for mapping in resolved_mappings)


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
            stdout='{"overall_score": 1.0}',
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

    method("grade this", "container-id", str(tmp_path), "opencode", False)

    command = captured["cmd"]
    kwargs = captured["kwargs"]
    assert isinstance(command, list)
    assert isinstance(kwargs, dict)
    assert "env" not in kwargs
    assert "OPENAI_API_KEY=ordinary-combination-value" in command


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
    assert len(contract.constraints) == 1
    assert contract.constraints[0].csv_header == header
    assert contract.constraints[0].csv_rows == rows
    assert "metadata" not in raw.casefold()
    assert "ground_truth" not in raw.casefold()
