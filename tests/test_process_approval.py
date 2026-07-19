from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from phycode.approval import ApprovalManifest
from phycode.execution import ExecutionJournal
from phycode.llm import EchoLLM
from phycode.models import AgentProfile, PolicyAction, PolicyDecision, SessionMode, ToolCall
from phycode.policy import PolicyContext, PolicyEngine
from phycode.profiles import profile_spec
from phycode.tools import ToolRegistry, ToolRuntime
from phycode.tools.process_tools import register_process_tools
from phycode.visibility import PathVisibilityPolicy


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _prbench_context(workspace_root: Path) -> PolicyContext:
    return PolicyContext(workspace_root, [], False, profile_spec(AgentProfile.PRBENCH))


def _register_python(registry: ToolRegistry, workspace_root: Path) -> None:
    register_process_tools(
        registry,
        workspace_root,
        frozenset({Path(sys.executable).resolve()}),
    )


def test_process_run_passes_metacharacters_as_literal_argv(tmp_path: Path) -> None:
    script = tmp_path / "argv.py"
    script.write_text(
        "import pathlib, sys\npathlib.Path('seen.txt').write_text(sys.argv[1], encoding='utf-8')\n",
        encoding="utf-8",
    )
    registry = ToolRegistry()
    _register_python(registry, tmp_path)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "argv.py", "literal & not-a-shell"], "cwd": "."},
    )

    result = ToolRuntime(registry).run(call, _prbench_context(tmp_path), approved=True)

    assert result.tool_result.status == "ok"
    assert (tmp_path / "seen.txt").read_text(encoding="utf-8") == "literal & not-a-shell"


def test_process_run_schema_is_structured_and_bounded(tmp_path: Path) -> None:
    registry = ToolRegistry()
    _register_python(registry, tmp_path)

    spec = registry.spec_for("process.run")

    assert spec is not None
    assert spec.input_schema == {
        "type": "object",
        "properties": {
            "argv": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": (
                    "argv[0] must be an absolute allowlisted executable, or the exact "
                    "bare alias 'python' when exactly one Python executable is allowlisted"
                ),
            },
            "cwd": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 300},
        },
        "required": ["argv"],
        "additionalProperties": False,
    }


def test_process_run_canonicalizes_exact_python_alias_across_runtime_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "reproduce.py"
    script.write_text("print('hello')\n", encoding="utf-8")
    expected_executable = str(Path(sys.executable).resolve())
    observed: dict[str, object] = {}

    class RecordingJournal:
        workspace_root = tmp_path.resolve()

        def validate_cwd(self, cwd: Path) -> None:
            observed["journal_cwd"] = cwd

        def snapshot_artifacts(self):
            return ()

        def snapshot_script(self, argv: tuple[str, ...], cwd: Path):
            observed["journal_snapshot_argv"] = argv
            return "reproduce.py", hashlib.sha256(script.read_bytes()).hexdigest()

        def record_process(self, **values):
            observed["journal_record_argv"] = values["argv"]

    def execution_guard(call: ToolCall) -> bool:
        observed["guard_call"] = call
        return True

    def fake_run(argv, **kwargs):
        observed["subprocess_argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout="hello\n", stderr="")

    monkeypatch.setattr("phycode.tools.process_tools.subprocess.run", fake_run)
    registry = ToolRegistry()
    register_process_tools(
        registry,
        tmp_path,
        frozenset({Path(sys.executable).resolve()}),
        journal=cast(ExecutionJournal, RecordingJournal()),
        execution_guard=execution_guard,
    )
    original = ToolCall(
        id="stable_call",
        provider_call_id="stable_provider_call",
        tool_name="process.run",
        args={"argv": ["python", "reproduce.py"], "cwd": ".", "timeout": 17},
    )

    def approve(call: ToolCall, decision: PolicyDecision) -> bool:
        del decision
        observed["approval_call"] = call
        return True

    result = ToolRuntime(registry).run(
        original,
        _prbench_context(tmp_path),
        approval_handler=approve,
    )

    assert result.tool_result.status == "ok"
    approval_call = observed["approval_call"]
    guard_call = observed["guard_call"]
    assert isinstance(approval_call, ToolCall)
    assert isinstance(guard_call, ToolCall)
    assert approval_call == guard_call
    assert guard_call.id == original.id
    assert guard_call.provider_call_id == original.provider_call_id
    assert guard_call.args == {
        "argv": [expected_executable, "reproduce.py"],
        "cwd": ".",
        "timeout": 17,
    }
    assert observed["journal_snapshot_argv"] == (expected_executable, "reproduce.py")
    assert observed["journal_record_argv"] == (expected_executable, "reproduce.py")
    assert observed["subprocess_argv"] == [expected_executable, "reproduce.py"]
    assert original.args["argv"][0] == "python"


@pytest.mark.parametrize("alias", ["Python", "python3", "./python", "python.exe"])
def test_process_run_does_not_normalize_non_exact_python_alias(
    tmp_path: Path,
    alias: str,
) -> None:
    registry = ToolRegistry()
    _register_python(registry, tmp_path)
    normalizer = registry.normalizer_for("process.run")
    assert normalizer is not None
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [alias, "reproduce.py"], "cwd": "."},
    )

    assert normalizer(call) == call


@pytest.mark.parametrize("allowlist_kind", ["multiple", "non_python"])
def test_process_run_does_not_normalize_python_alias_without_unique_python_allowlist(
    tmp_path: Path,
    allowlist_kind: str,
) -> None:
    allowed = (
        frozenset({Path(sys.executable), tmp_path / "other-python.exe"})
        if allowlist_kind == "multiple"
        else frozenset({tmp_path / "node.exe"})
    )
    registry = ToolRegistry()
    register_process_tools(registry, tmp_path, allowed)
    normalizer = registry.normalizer_for("process.run")
    assert normalizer is not None
    call = ToolCall(
        tool_name="process.run",
        args={"argv": ["python", "reproduce.py"], "cwd": "."},
    )

    assert normalizer(call) == call


@pytest.mark.parametrize(
    "argv",
    [
        [],
        [""],
        [sys.executable, ""],
        [sys.executable, "bad\x00argument"],
        [sys.executable, 1],
    ],
)
def test_process_run_rejects_invalid_argv(tmp_path: Path, argv: list[object]) -> None:
    registry = ToolRegistry()
    _register_python(registry, tmp_path)
    call = ToolCall(tool_name="process.run", args={"argv": argv, "cwd": "."})

    result = ToolRuntime(registry).run(call, _prbench_context(tmp_path), approved=True)

    assert result.tool_result.status == "invalid_tool_args"


@pytest.mark.parametrize("timeout", [0, 301, "30", True])
def test_process_run_rejects_timeout_outside_integer_bounds(tmp_path: Path, timeout: object) -> None:
    registry = ToolRegistry()
    _register_python(registry, tmp_path)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "-c", "print('unreachable')"], "cwd": ".", "timeout": timeout},
    )

    result = ToolRuntime(registry).run(call, _prbench_context(tmp_path), approved=True)

    assert result.tool_result.status == "invalid_tool_args"
    assert "unreachable" not in result.tool_result.stdout


def test_process_run_rejects_executable_outside_allowlist(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_process_tools(registry, tmp_path, frozenset({tmp_path / "definitely-not-python"}))
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "-c", "print('unreachable')"], "cwd": "."},
    )

    result = ToolRuntime(registry).run(call, _prbench_context(tmp_path), approved=True)

    assert result.tool_result.status == "invalid_tool_args"
    assert result.tool_result.stderr == (
        f"executable is not allowed: {Path(sys.executable).resolve().name}"
    )
    assert "unreachable" not in result.tool_result.stdout


def test_process_executor_cwd_resolution_error_is_generic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = ToolRegistry()
    _register_python(registry, tmp_path)
    executor = registry.executor_for("process.run")
    assert executor is not None
    original_resolve = PathVisibilityPolicy.resolve

    def fail_cwd(policy: PathVisibilityPolicy, path: str | Path) -> Path:
        if str(path) == "resolution-error":
            raise OSError("C:\\private\\cwd-secret")
        return original_resolve(policy, path)

    monkeypatch.setattr(PathVisibilityPolicy, "resolve", fail_cwd)
    result = executor(
        ToolCall(
            tool_name="process.run",
            args={"argv": [sys.executable, "script.py"], "cwd": "resolution-error"},
        )
    )

    assert result.status == "invalid_tool_args"
    assert result.stderr == "cwd is not visible"


def test_process_executor_executable_resolution_error_is_generic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = ToolRegistry()
    _register_python(registry, tmp_path)
    executor = registry.executor_for("process.run")
    assert executor is not None
    requested = tmp_path / "broken-command.exe"
    original_resolve = Path.resolve

    def fail_executable(path: Path, *args, **kwargs) -> Path:
        if path == requested:
            raise OSError("C:\\private\\executable-secret")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_executable)
    result = executor(
        ToolCall(
            tool_name="process.run",
            args={"argv": [str(requested), "script.py"], "cwd": "."},
        )
    )

    assert result.status == "invalid_tool_args"
    assert result.stderr == "executable path cannot be resolved"


def test_process_run_rejects_different_absolute_executable_with_same_basename(tmp_path: Path) -> None:
    other_executable = tmp_path / "other" / Path(sys.executable).name
    other_executable.parent.mkdir()
    shutil.copy2(sys.executable, other_executable)
    registry = ToolRegistry()
    _register_python(registry, tmp_path)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [str(other_executable), "-c", "print('unreachable')"], "cwd": "."},
    )

    result = ToolRuntime(registry).run(call, _prbench_context(tmp_path), approved=True)

    assert result.tool_result.status == "invalid_tool_args"
    assert "not allowed" in result.tool_result.stderr
    assert "unreachable" not in result.tool_result.stdout


def test_process_run_rejects_relative_executable(tmp_path: Path) -> None:
    registry = ToolRegistry()
    _register_python(registry, tmp_path)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": ["relative-python", "-c", "print('unreachable')"], "cwd": "."},
    )

    result = ToolRuntime(registry).run(call, _prbench_context(tmp_path), approved=True)

    assert result.tool_result.status == "invalid_tool_args"
    assert "absolute" in result.tool_result.stderr
    assert "unreachable" not in result.tool_result.stdout


def test_process_run_rejects_unknown_arguments(tmp_path: Path) -> None:
    registry = ToolRegistry()
    _register_python(registry, tmp_path)
    call = ToolCall(
        tool_name="process.run",
        args={
            "argv": [sys.executable, "-c", "print('unreachable')"],
            "cwd": ".",
            "environment": {"UNSAFE": "override"},
        },
    )

    result = ToolRuntime(registry).run(call, _prbench_context(tmp_path), approved=True)

    assert result.tool_result.status == "invalid_tool_args"
    assert "unreachable" not in result.tool_result.stdout


@pytest.mark.parametrize(
    ("cwd", "rule_id"),
    [("../outside", "workspace.path_escape"), ("_ground_truth", "prbench.hidden_path_blocked")],
)
def test_process_run_policy_rejects_invisible_cwd(tmp_path: Path, cwd: str, rule_id: str) -> None:
    registry = ToolRegistry()
    _register_python(registry, tmp_path)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "-c", "print('unreachable')"], "cwd": cwd},
    )

    result = ToolRuntime(registry).run(call, _prbench_context(tmp_path), approved=True)

    assert result.policy.decision == PolicyAction.DENY
    assert result.policy.rule_id == rule_id
    assert result.tool_result.status == "policy_blocked"


def test_process_run_policy_fails_closed_when_cwd_cannot_be_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_resolve = PathVisibilityPolicy.resolve

    def reject_malformed_cwd(policy: PathVisibilityPolicy, path: str | Path) -> Path:
        if str(path) == "bad\x00cwd":
            raise OSError("malformed cwd")
        return original_resolve(policy, path)

    monkeypatch.setattr(PathVisibilityPolicy, "resolve", reject_malformed_cwd)
    registry = ToolRegistry()
    _register_python(registry, tmp_path)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "-c", "print('unreachable')"], "cwd": "bad\x00cwd"},
    )

    result = ToolRuntime(registry).run(call, _prbench_context(tmp_path), approved=True)

    assert result.policy.decision == PolicyAction.DENY
    assert result.policy.rule_id == "workspace.path_escape"
    assert result.tool_result.status == "policy_blocked"


def test_process_run_reports_nonzero_exit_without_shell(tmp_path: Path) -> None:
    registry = ToolRegistry()
    _register_python(registry, tmp_path)
    call = ToolCall(
        tool_name="process.run",
        args={
            "argv": [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)"],
            "cwd": ".",
        },
    )

    result = ToolRuntime(registry).run(call, _prbench_context(tmp_path), approved=True)

    assert result.tool_result.status == "command_failed"
    assert result.tool_result.stdout.strip() == "out"
    assert result.tool_result.stderr.strip() == "err"


def test_process_run_uses_only_minimal_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    forbidden_names = {
        "PHYCODE_API_KEY",
        "OPENAI_API_KEY",
        "CUSTOM_PROVIDER",
        "CUSTOM_TOKEN",
        "CUSTOM_CREDENTIAL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "PATH",
        "HOME",
    }
    for name in forbidden_names:
        monkeypatch.setenv(name, f"test-only-{name.casefold()}")
    allowed_names = {
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
    }
    registry = ToolRegistry()
    _register_python(registry, tmp_path)
    call = ToolCall(
        tool_name="process.run",
        args={
            "argv": [sys.executable, "-c", "import json, os; print(json.dumps(sorted(os.environ)))"],
            "cwd": ".",
        },
    )

    result = ToolRuntime(registry).run(call, _prbench_context(tmp_path), approved=True)
    child_environment_names = {name.upper() for name in json.loads(result.tool_result.stdout)}

    assert result.tool_result.status == "ok"
    assert child_environment_names <= allowed_names
    assert child_environment_names.isdisjoint(forbidden_names)


def test_process_run_redacts_stdout_and_stderr(tmp_path: Path) -> None:
    stdout_secret = "sk-stdout-reviewer-1234567890"
    stderr_secret = "sk-stderr-reviewer-1234567890"
    registry = ToolRegistry()
    _register_python(registry, tmp_path)
    script = (
        f"import sys; print({stdout_secret!r}); "
        f"print('OPENAI_API_KEY=' + {stderr_secret!r}, file=sys.stderr); sys.exit(7)"
    )
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "-c", script], "cwd": "."},
    )

    result = ToolRuntime(registry).run(call, _prbench_context(tmp_path), approved=True)

    assert result.tool_result.status == "command_failed"
    assert stdout_secret not in result.tool_result.stdout
    assert stderr_secret not in result.tool_result.stderr
    assert "[REDACTED_SECRET]" in result.tool_result.stdout
    assert "[REDACTED_SECRET]" in result.tool_result.stderr


def test_process_run_reports_timeout(tmp_path: Path) -> None:
    timeout_secret = "sk-timeout-reviewer-1234567890"
    registry = ToolRegistry()
    _register_python(registry, tmp_path)
    call = ToolCall(
        tool_name="process.run",
        args={
            "argv": [
                sys.executable,
                "-c",
                f"import time; print({timeout_secret!r}, flush=True); time.sleep(5)",
            ],
            "cwd": ".",
            "timeout": 1,
        },
    )

    result = ToolRuntime(registry).run(call, _prbench_context(tmp_path), approved=True)

    assert result.tool_result.status == "timeout"
    assert "timed out after 1 seconds" in result.tool_result.stderr.casefold()
    assert timeout_secret not in result.tool_result.stdout
    assert "[REDACTED_SECRET]" in result.tool_result.stdout


def test_exact_approval_is_consumed_once(tmp_path: Path) -> None:
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps({"grants": [{"tool_name": "file.write", "path": "reproduction/a.py"}]}),
        encoding="utf-8",
    )
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    call = ToolCall(tool_name="file.write", args={"path": "reproduction/a.py", "content": "print(1)"})
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert decision.decision == PolicyAction.ASK
    assert manifest(call, decision)
    assert not manifest(call, decision)


def test_dynamic_process_approval_request_is_a_directly_usable_hash_bound_grant(
    tmp_path: Path,
) -> None:
    reproduction = tmp_path / "reproduction"
    reproduction.mkdir()
    script = reproduction / "a.py"
    script.write_text("print('approved')\n", encoding="utf-8")
    expected_hash = hashlib.sha256(script.read_bytes()).hexdigest()
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    request_path = tmp_path / ".phycode" / "prbench" / "approval-request.json"
    clock = _FakeClock()
    observed_request: dict[str, object] = {}

    def approve_after_request(seconds: float) -> None:
        nonlocal observed_request
        observed_request = json.loads(request_path.read_text(encoding="utf-8"))
        approvals.write_text(
            json.dumps({"grants": [observed_request]}),
            encoding="utf-8",
        )
        clock.advance(seconds)

    manifest = ApprovalManifest.from_json(
        approvals,
        tmp_path,
        approval_wait_seconds=1,
        clock=clock,
        sleeper=approve_after_request,
        poll_interval_seconds=0.01,
    )
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "./reproduction/a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert manifest(call, decision)
    assert observed_request == {
        "tool_name": "process.run",
        "argv": [os.path.normcase(str(Path(sys.executable).resolve())), "reproduction/a.py"],
        "cwd": ".",
        "script_sha256": expected_hash,
    }
    assert str(tmp_path) not in json.dumps(observed_request)
    assert not request_path.exists()


def test_dynamic_request_argv_round_trips_when_process_cwd_is_not_workspace_root(
    tmp_path: Path,
) -> None:
    reproduction = tmp_path / "reproduction"
    reproduction.mkdir()
    (reproduction / "a.py").write_text("print('approved')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    request_path = tmp_path / ".phycode/prbench/approval-request.json"
    clock = _FakeClock()
    observed_argv: list[str] = []

    def approve_after_request(seconds: float) -> None:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        observed_argv.extend(request["argv"])
        approvals.write_text(
            json.dumps({"grants": [request]}),
            encoding="utf-8",
        )
        clock.advance(seconds)

    manifest = ApprovalManifest.from_json(
        approvals,
        tmp_path,
        approval_wait_seconds=0.02,
        clock=clock,
        sleeper=approve_after_request,
        poll_interval_seconds=0.01,
    )
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "reproduction"},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert manifest(call, decision)
    assert observed_argv[1] == "a.py"


def test_dynamic_request_never_writes_an_executable_path_inside_workspace(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "private-runtime" / "python.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"test executable placeholder")
    (tmp_path / "a.py").write_text("print('blocked')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    clock = _FakeClock()
    sleeps = 0

    def unexpected_sleep(seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        clock.advance(seconds)

    manifest = ApprovalManifest.from_json(
        approvals,
        tmp_path,
        approval_wait_seconds=1,
        clock=clock,
        sleeper=unexpected_sleep,
    )
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [str(executable), "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert not manifest(call, decision)
    assert sleeps == 0
    assert not (tmp_path / ".phycode/prbench/approval-request.json").exists()


def test_dynamic_request_rejects_runtime_parent_symlink_seam_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "a.py").write_text("print('blocked')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    runtime_parent = tmp_path / ".phycode"
    clock = _FakeClock()
    sleeps = 0
    original_is_symlink = Path.is_symlink

    def mark_runtime_parent(path: Path) -> bool:
        if path == runtime_parent:
            return True
        return original_is_symlink(path)

    def unexpected_sleep(seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        clock.advance(seconds)

    manifest = ApprovalManifest.from_json(
        approvals,
        tmp_path,
        approval_wait_seconds=1,
        clock=clock,
        sleeper=unexpected_sleep,
    )
    monkeypatch.setattr(Path, "is_symlink", mark_runtime_parent)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert not manifest(call, decision)
    assert sleeps == 0
    assert not runtime_parent.exists()


def test_zero_wait_keeps_missing_process_grant_fail_closed_without_pending_request(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.py").write_text("print('blocked')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert not manifest(call, decision)
    assert not (tmp_path / ".phycode/prbench/approval-request.json").exists()


def test_zero_wait_uses_constructor_snapshot_and_ignores_later_grants(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('blocked')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    approvals.write_text(
        json.dumps(
            {
                "grants": [
                    {
                        "tool_name": "process.run",
                        "argv": [sys.executable, "a.py"],
                        "cwd": ".",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert not manifest(call, decision)


def test_waiting_manifest_rejects_preloaded_process_grant_without_hash(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('blocked')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps(
            {
                "grants": [
                    {
                        "tool_name": "process.run",
                        "argv": [sys.executable, "a.py"],
                        "cwd": ".",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    clock = _FakeClock()
    manifest = ApprovalManifest.from_json(
        approvals,
        tmp_path,
        approval_wait_seconds=0.01,
        clock=clock,
        sleeper=clock.advance,
        poll_interval_seconds=0.01,
    )
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert not manifest(call, decision)
    assert not (tmp_path / ".phycode/prbench/approval-request.json").exists()


def test_zero_wait_removes_a_stale_pending_request_before_failing_closed(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('blocked')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    request_path = tmp_path / ".phycode/prbench/approval-request.json"
    request_path.parent.mkdir(parents=True)
    request_path.write_text('{"stale": true}', encoding="utf-8")
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert not manifest(call, decision)
    assert not request_path.exists()


def test_pending_request_cleanup_does_not_follow_runtime_directory_symlink(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.py").write_text("print('blocked')\n", encoding="utf-8")
    approvals = workspace / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "approval-request.json"
    sentinel.write_text("must remain", encoding="utf-8")
    try:
        (workspace / ".phycode").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    manifest = ApprovalManifest.from_json(approvals, workspace)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(workspace, [], False))

    assert not manifest(call, decision)
    assert sentinel.read_text(encoding="utf-8") == "must remain"


def test_pending_request_cleanup_fails_closed_if_runtime_target_resolves_outside(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.py").write_text("print('blocked')\n", encoding="utf-8")
    approvals = workspace / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    sentinel = tmp_path / "approval-request.json"
    sentinel.write_text("must remain", encoding="utf-8")
    manifest = ApprovalManifest.from_json(approvals, workspace)
    setattr(manifest, "_request_path", sentinel)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(workspace, [], False))

    assert not manifest(call, decision)
    assert sentinel.read_text(encoding="utf-8") == "must remain"


def test_pending_request_cleanup_rejects_destination_symlink_seam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "a.py").write_text("print('blocked')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    request_path = tmp_path / ".phycode/prbench/approval-request.json"
    request_path.parent.mkdir(parents=True)
    request_path.write_text("must remain", encoding="utf-8")
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    original_is_symlink = Path.is_symlink

    def mark_destination(path: Path) -> bool:
        if path == request_path:
            return True
        return original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", mark_destination)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert not manifest(call, decision)
    assert request_path.read_text(encoding="utf-8") == "must remain"


def test_hash_bound_process_grant_rejects_script_changed_while_waiting(tmp_path: Path) -> None:
    script = tmp_path / "a.py"
    script.write_text("print('before')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    request_path = tmp_path / ".phycode/prbench/approval-request.json"
    clock = _FakeClock()
    wrote_grant = False

    def replace_script_after_request(seconds: float) -> None:
        nonlocal wrote_grant
        if not wrote_grant:
            request = json.loads(request_path.read_text(encoding="utf-8"))
            script.write_text("print('after')\n", encoding="utf-8")
            approvals.write_text(
                json.dumps({"grants": [request]}),
                encoding="utf-8",
            )
            wrote_grant = True
        clock.advance(seconds)

    manifest = ApprovalManifest.from_json(
        approvals,
        tmp_path,
        approval_wait_seconds=0.03,
        clock=clock,
        sleeper=replace_script_after_request,
        poll_interval_seconds=0.01,
    )
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert not manifest(call, decision)
    assert not request_path.exists()


def test_dynamic_refresh_rejects_a_new_process_grant_without_script_hash(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.py").write_text("print('blocked')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    request_path = tmp_path / ".phycode/prbench/approval-request.json"
    clock = _FakeClock()
    wrote_grant = False

    def append_unbound_grant(seconds: float) -> None:
        nonlocal wrote_grant
        if not wrote_grant:
            request = json.loads(request_path.read_text(encoding="utf-8"))
            approvals.write_text(
                json.dumps(
                    {
                        "grants": [
                            {
                                "tool_name": "process.run",
                                "argv": request["argv"],
                                "cwd": request["cwd"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            wrote_grant = True
        clock.advance(seconds)

    manifest = ApprovalManifest.from_json(
        approvals,
        tmp_path,
        approval_wait_seconds=0.02,
        clock=clock,
        sleeper=append_unbound_grant,
        poll_interval_seconds=0.01,
    )
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert not manifest(call, decision)
    assert not request_path.exists()


def test_dynamic_request_rejects_extra_opaque_argv_before_persisting(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('blocked')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    request_path = tmp_path / ".phycode/prbench/approval-request.json"
    clock = _FakeClock()
    sleeps = 0

    def unexpected_sleep(seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        clock.advance(seconds)

    manifest = ApprovalManifest.from_json(
        approvals,
        tmp_path,
        approval_wait_seconds=1,
        clock=clock,
        sleeper=unexpected_sleep,
    )
    call = ToolCall(
        tool_name="process.run",
        args={
            "argv": [sys.executable, "a.py", "opaque-review-token"],
            "cwd": ".",
        },
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert not manifest(call, decision)
    assert sleeps == 0
    assert not request_path.exists()


def test_waiting_manifest_rejects_preloaded_hash_grant_with_extra_argv(tmp_path: Path) -> None:
    script = tmp_path / "a.py"
    script.write_text("print('blocked')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps(
            {
                "grants": [
                    {
                        "tool_name": "process.run",
                        "argv": [sys.executable, "a.py", "opaque-review-token"],
                        "cwd": ".",
                        "script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    clock = _FakeClock()
    manifest = ApprovalManifest.from_json(
        approvals,
        tmp_path,
        approval_wait_seconds=0.01,
        clock=clock,
        sleeper=clock.advance,
        poll_interval_seconds=0.01,
    )
    call = ToolCall(
        tool_name="process.run",
        args={
            "argv": [sys.executable, "a.py", "opaque-review-token"],
            "cwd": ".",
        },
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert not manifest(call, decision)


def test_reloading_manifest_does_not_revive_consumed_hash_bound_grant(tmp_path: Path) -> None:
    script = tmp_path / "a.py"
    script.write_text("print('once')\n", encoding="utf-8")
    script_hash = hashlib.sha256(script.read_bytes()).hexdigest()
    grant = {
        "tool_name": "process.run",
        "argv": [sys.executable, "a.py"],
        "cwd": ".",
        "script_sha256": script_hash,
    }
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": [grant]}), encoding="utf-8")
    clock = _FakeClock()
    manifest = ApprovalManifest.from_json(
        approvals,
        tmp_path,
        approval_wait_seconds=0.01,
        clock=clock,
        sleeper=clock.advance,
        poll_interval_seconds=0.01,
    )
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert manifest(call, decision)
    approvals.write_text(json.dumps({"grants": [grant]}), encoding="utf-8")
    assert not manifest(call, decision)
    approvals.write_text(json.dumps({"grants": [grant, grant]}), encoding="utf-8")
    assert manifest(call, decision)
    assert not manifest(call, decision)


def test_refresh_rejects_manifest_target_changed_outside_workspace(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('blocked')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    script_hash = hashlib.sha256((tmp_path / "a.py").read_bytes()).hexdigest()
    outside = tmp_path.parent / f"{tmp_path.name}-outside-approvals.json"
    outside.write_text(
        json.dumps(
            {
                "grants": [
                    {
                        "tool_name": "process.run",
                        "argv": [sys.executable, "a.py"],
                        "cwd": ".",
                        "script_sha256": script_hash,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    clock = _FakeClock()
    manifest = ApprovalManifest.from_json(
        approvals,
        tmp_path,
        approval_wait_seconds=0.01,
        clock=clock,
        sleeper=clock.advance,
        poll_interval_seconds=0.01,
    )
    setattr(manifest, "_manifest_path", outside)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    try:
        assert not manifest(call, decision)
    finally:
        outside.unlink(missing_ok=True)


def test_refresh_rejects_manifest_file_symlink_seam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "a.py").write_text("print('blocked')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    clock = _FakeClock()
    manifest = ApprovalManifest.from_json(
        approvals,
        tmp_path,
        approval_wait_seconds=0.01,
        clock=clock,
        sleeper=clock.advance,
        poll_interval_seconds=0.01,
    )
    script_hash = hashlib.sha256((tmp_path / "a.py").read_bytes()).hexdigest()
    approvals.write_text(
        json.dumps(
            {
                "grants": [
                    {
                        "tool_name": "process.run",
                        "argv": [sys.executable, "a.py"],
                        "cwd": ".",
                        "script_sha256": script_hash,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    original_is_symlink = Path.is_symlink

    def mark_manifest(path: Path) -> bool:
        if path == approvals:
            return True
        return original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", mark_manifest)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert not manifest(call, decision)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink replacement semantics")
def test_refresh_rejects_real_manifest_symlink_replacement(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('blocked')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    clock = _FakeClock()
    manifest = ApprovalManifest.from_json(
        approvals,
        tmp_path,
        approval_wait_seconds=0.01,
        clock=clock,
        sleeper=clock.advance,
        poll_interval_seconds=0.01,
    )
    outside = tmp_path.parent / f"{tmp_path.name}-symlink-approvals.json"
    outside.write_text(json.dumps({"grants": []}), encoding="utf-8")
    approvals.unlink()
    approvals.symlink_to(outside)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    try:
        assert not manifest(call, decision)
    finally:
        outside.unlink(missing_ok=True)


def test_malformed_refresh_fails_closed_and_cleans_pending_request(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('blocked')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    request_path = tmp_path / ".phycode/prbench/approval-request.json"
    clock = _FakeClock()

    def corrupt_manifest(seconds: float) -> None:
        assert request_path.is_file()
        approvals.write_text("{malformed", encoding="utf-8")
        clock.advance(seconds)

    manifest = ApprovalManifest.from_json(
        approvals,
        tmp_path,
        approval_wait_seconds=1,
        clock=clock,
        sleeper=corrupt_manifest,
        poll_interval_seconds=0.01,
    )
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert not manifest(call, decision)
    assert not request_path.exists()


def test_dynamic_process_approval_retries_after_transient_malformed_refresh(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.py").write_text("print('approved')\n", encoding="utf-8")
    script_hash = hashlib.sha256((tmp_path / "a.py").read_bytes()).hexdigest()
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    request_path = tmp_path / ".phycode/prbench/approval-request.json"
    clock = _FakeClock()
    sleeps = 0

    def publish_approval_after_transient_invalid_state(seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        assert request_path.is_file()
        if sleeps == 1:
            approvals.write_text("{malformed", encoding="utf-8")
        else:
            approvals.write_text(
                json.dumps(
                    {
                        "grants": [
                            {
                                "tool_name": "process.run",
                                "argv": [sys.executable, "a.py"],
                                "cwd": ".",
                                "script_sha256": script_hash,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
        clock.advance(seconds)

    manifest = ApprovalManifest.from_json(
        approvals,
        tmp_path,
        approval_wait_seconds=0.03,
        clock=clock,
        sleeper=publish_approval_after_transient_invalid_state,
        poll_interval_seconds=0.01,
    )
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert manifest(call, decision)
    assert sleeps == 2
    assert not request_path.exists()


def test_dynamic_process_approval_timeout_cleans_pending_request(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('blocked')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    request_path = tmp_path / ".phycode/prbench/approval-request.json"
    clock = _FakeClock()
    sleeps: list[float] = []

    def advance(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    manifest = ApprovalManifest.from_json(
        approvals,
        tmp_path,
        approval_wait_seconds=0.02,
        clock=clock,
        sleeper=advance,
        poll_interval_seconds=0.01,
    )
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert not manifest(call, decision)
    assert sleeps == [0.01, 0.01]
    assert not request_path.exists()


def test_dynamic_process_approval_request_write_failure_is_closed(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('blocked')\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": []}), encoding="utf-8")
    (tmp_path / ".phycode").write_text("not a directory", encoding="utf-8")
    manifest = ApprovalManifest.from_json(
        approvals,
        tmp_path,
        approval_wait_seconds=1,
    )
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert not manifest(call, decision)


def test_process_approval_grant_rejects_invalid_script_hash(tmp_path: Path) -> None:
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps(
            {
                "grants": [
                    {
                        "tool_name": "process.run",
                        "argv": [sys.executable, "a.py"],
                        "cwd": ".",
                        "script_sha256": "not-a-sha256",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        ApprovalManifest.from_json(approvals, tmp_path)


def test_composed_process_guard_rehashes_after_approval_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from phycode.composition import build_agent

    script = tmp_path / "a.py"
    script.write_text(
        "from pathlib import Path\nPath('original.txt').write_text('ORIGINAL')\n",
        encoding="utf-8",
    )
    script_hash = hashlib.sha256(script.read_bytes()).hexdigest()
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps(
            {
                "grants": [
                    {
                        "tool_name": "process.run",
                        "argv": [sys.executable, "a.py"],
                        "cwd": ".",
                        "script_sha256": script_hash,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    monkeypatch.chdir(tmp_path)
    loop = build_agent(
        SessionMode.NON_INTERACTIVE,
        llm=EchoLLM(),
        approval_handler=manifest,
        profile=AgentProfile.PRBENCH,
    )
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, loop.policy_context)
    assert manifest(call, decision)
    script.write_text(
        "from pathlib import Path\n"
        "Path('changed.txt').write_text('CHANGED_AFTER_APPROVAL')\n"
        "print('CHANGED_AFTER_APPROVAL')\n",
        encoding="utf-8",
    )

    result = loop.tool_runtime.run(call, loop.policy_context, approved=True)

    assert result.tool_result.status != "ok"
    assert "CHANGED_AFTER_APPROVAL" not in result.tool_result.stdout
    assert not (tmp_path / "original.txt").exists()
    assert not (tmp_path / "changed.txt").exists()


def test_composed_process_guard_keeps_zero_wait_hashless_grant_compatible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from phycode.composition import build_agent

    (tmp_path / "a.py").write_text(
        "from pathlib import Path\nPath('ran.txt').write_text('ok')\n",
        encoding="utf-8",
    )
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps(
            {
                "grants": [
                    {
                        "tool_name": "process.run",
                        "argv": [sys.executable, "a.py"],
                        "cwd": ".",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    monkeypatch.chdir(tmp_path)
    loop = build_agent(
        SessionMode.NON_INTERACTIVE,
        llm=EchoLLM(),
        approval_handler=manifest,
        profile=AgentProfile.PRBENCH,
    )
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, loop.policy_context)
    assert manifest(call, decision)

    result = loop.tool_runtime.run(call, loop.policy_context, approved=True)

    assert result.tool_result.status == "ok"
    assert (tmp_path / "ran.txt").read_text(encoding="utf-8") == "ok"
    (tmp_path / "ran.txt").unlink()

    repeated = loop.tool_runtime.run(call, loop.policy_context, approved=True)

    assert repeated.tool_result.status != "ok"
    assert not (tmp_path / "ran.txt").exists()


def test_file_approval_matches_resolved_path(tmp_path: Path) -> None:
    (tmp_path / "reproduction").mkdir()
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps({"grants": [{"tool_name": "file.edit", "path": "reproduction/../reproduction/a.py"}]}),
        encoding="utf-8",
    )
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    call = ToolCall(tool_name="file.edit", args={"path": "reproduction/a.py", "old": "a", "new": "b"})
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert manifest(call, decision)


@pytest.mark.skipif(os.name != "nt", reason="Windows normcase behavior")
def test_file_approval_matches_case_variant_for_nonexistent_windows_target(tmp_path: Path) -> None:
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps({"grants": [{"tool_name": "file.write", "path": "Reproduction/NewFile.PY"}]}),
        encoding="utf-8",
    )
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    call = ToolCall(
        tool_name="file.write",
        args={"path": "reproduction/newfile.py", "content": "print('case variant')"},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert manifest(call, decision)


@pytest.mark.skipif(os.name == "nt", reason="POSIX paths remain case-sensitive")
def test_file_approval_does_not_match_case_variant_on_posix(tmp_path: Path) -> None:
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps({"grants": [{"tool_name": "file.write", "path": "NewFile.py"}]}),
        encoding="utf-8",
    )
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    call = ToolCall(tool_name="file.write", args={"path": "newfile.py", "content": "different target"})
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert not manifest(call, decision)


def test_approval_does_not_match_different_argv(tmp_path: Path) -> None:
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps(
            {
                "grants": [
                    {
                        "tool_name": "process.run",
                        "argv": [sys.executable, "reproduction/a.py"],
                        "cwd": ".",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "reproduction/b.py"], "cwd": "."},
    )
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))

    assert not manifest(call, decision)


def test_process_approval_binds_resolved_cwd(tmp_path: Path) -> None:
    (tmp_path / "reproduction").mkdir()
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps(
            {
                "grants": [
                    {"tool_name": "process.run", "argv": [sys.executable, "a.py"], "cwd": "reproduction"}
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    same = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "reproduction/../reproduction"},
    )
    different = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "a.py"], "cwd": "."},
    )

    assert not manifest(different, PolicyEngine().decide(different, PolicyContext(tmp_path, [], False)))
    assert manifest(same, PolicyEngine().decide(same, PolicyContext(tmp_path, [], False)))


@pytest.mark.parametrize(
    "invalid_args",
    [
        {
            "argv": [sys.executable, "reproduction/a.py"],
            "cwd": ".",
            "unexpected": "not allowed",
        },
        {"argv": [sys.executable, "reproduction/a.py"], "cwd": ".", "timeout": 0},
        {"argv": [sys.executable, "reproduction/a.py"], "cwd": ".", "timeout": "30"},
        {"cwd": "."},
        {"argv": [], "cwd": "."},
        {"argv": [sys.executable, ""], "cwd": "."},
        {"argv": [sys.executable, "bad\x00argument"], "cwd": "."},
        {"argv": [sys.executable, "reproduction/a.py"], "cwd": ""},
        {"argv": [sys.executable, "reproduction/a.py"], "cwd": "bad\x00cwd"},
    ],
)
def test_invalid_process_call_does_not_consume_grant(
    tmp_path: Path, invalid_args: dict[str, object]
) -> None:
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps(
            {
                "grants": [
                    {
                        "tool_name": "process.run",
                        "argv": [sys.executable, "reproduction/a.py"],
                        "cwd": ".",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    invalid_call = ToolCall(tool_name="process.run", args=invalid_args)
    invalid_decision = PolicyEngine().decide(invalid_call, PolicyContext(tmp_path, [], False))
    valid_call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "reproduction/a.py"], "cwd": ".", "timeout": 30},
    )
    valid_decision = PolicyEngine().decide(valid_call, PolicyContext(tmp_path, [], False))

    assert not manifest(invalid_call, invalid_decision)
    assert manifest(valid_call, valid_decision)
    assert not manifest(valid_call, valid_decision)


@pytest.mark.parametrize(
    ("tool_name", "invalid_args", "valid_args"),
    [
        ("file.write", {"path": "a.py"}, {"path": "a.py", "content": "ok"}),
        (
            "file.write",
            {"path": "a.py", "content": "ok", "unexpected": True},
            {"path": "a.py", "content": "ok"},
        ),
        ("file.write", {"path": "a.py", "content": 1}, {"path": "a.py", "content": "ok"}),
        ("file.edit", {"path": "a.py", "old": "a"}, {"path": "a.py", "old": "a", "new": "b"}),
        (
            "file.edit",
            {"path": "a.py", "old": "a", "new": "b", "unexpected": True},
            {"path": "a.py", "old": "a", "new": "b"},
        ),
        (
            "file.edit",
            {"path": "a.py", "old": 1, "new": "b"},
            {"path": "a.py", "old": "a", "new": "b"},
        ),
    ],
)
def test_invalid_file_call_does_not_consume_grant(
    tmp_path: Path,
    tool_name: str,
    invalid_args: dict[str, object],
    valid_args: dict[str, object],
) -> None:
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps({"grants": [{"tool_name": tool_name, "path": "a.py"}]}),
        encoding="utf-8",
    )
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    invalid_call = ToolCall(tool_name=tool_name, args=invalid_args)
    invalid_decision = PolicyEngine().decide(invalid_call, PolicyContext(tmp_path, [], False))
    valid_call = ToolCall(tool_name=tool_name, args=valid_args)
    valid_decision = PolicyEngine().decide(valid_call, PolicyContext(tmp_path, [], False))

    assert not manifest(invalid_call, invalid_decision)
    assert manifest(valid_call, valid_decision)
    assert not manifest(valid_call, valid_decision)


@pytest.mark.parametrize(
    "grant",
    [
        {"tool_name": "file.write", "path": ""},
        {"tool_name": "file.edit", "path": "bad\x00path"},
        {"tool_name": "process.run", "argv": [], "cwd": "."},
        {"tool_name": "process.run", "argv": ["python", "a.py"], "cwd": "."},
        {"tool_name": "process.run", "argv": [sys.executable, ""], "cwd": "."},
        {"tool_name": "process.run", "argv": [sys.executable, "bad\x00argument"], "cwd": "."},
        {"tool_name": "process.run", "argv": [sys.executable, "a.py"], "cwd": ""},
        {"tool_name": "process.run", "argv": [sys.executable, "a.py"], "cwd": "bad\x00cwd"},
    ],
)
def test_approval_manifest_rejects_invalid_grant_target(tmp_path: Path, grant: dict[str, object]) -> None:
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": [grant]}), encoding="utf-8")

    with pytest.raises(ValidationError):
        ApprovalManifest.from_json(approvals, tmp_path)


@pytest.mark.parametrize(
    "payload",
    [
        {"grants": [], "unexpected": True},
        {"grants": [{"tool_name": "file.write", "path": "a.py", "unexpected": True}]},
    ],
)
def test_approval_manifest_forbids_unknown_fields(tmp_path: Path, payload: dict[str, object]) -> None:
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        ApprovalManifest.from_json(approvals, tmp_path)


def test_prbench_registry_has_process_without_shell_and_coding_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from phycode.cli import build_agent

    monkeypatch.chdir(tmp_path)
    prbench = build_agent(SessionMode.NON_INTERACTIVE, llm=EchoLLM(), profile=AgentProfile.PRBENCH)
    coding = build_agent(SessionMode.NON_INTERACTIVE, llm=EchoLLM(), profile=AgentProfile.CODING)
    prbench_names = {spec.name for spec in prbench.tool_runtime.registry.list_specs()}
    coding_names = {spec.name for spec in coding.tool_runtime.registry.list_specs()}

    assert "process.run" in prbench_names
    assert "shell.run" not in prbench_names
    assert "shell.run" in coding_names
    assert "process.run" not in coding_names
