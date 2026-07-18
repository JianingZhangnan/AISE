from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from phycode.approval import ApprovalManifest
from phycode.llm import EchoLLM
from phycode.models import AgentProfile, PolicyAction, SessionMode, ToolCall
from phycode.policy import PolicyContext, PolicyEngine
from phycode.profiles import profile_spec
from phycode.tools import ToolRegistry, ToolRuntime
from phycode.tools.process_tools import register_process_tools
from phycode.visibility import PathVisibilityPolicy


def _prbench_context(workspace_root: Path) -> PolicyContext:
    return PolicyContext(workspace_root, [], False, profile_spec(AgentProfile.PRBENCH))


def _register_python(registry: ToolRegistry, workspace_root: Path) -> None:
    register_process_tools(
        registry,
        workspace_root,
        frozenset({Path(sys.executable).name.casefold()}),
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
            "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "cwd": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 300},
        },
        "required": ["argv"],
        "additionalProperties": False,
    }


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
    register_process_tools(registry, tmp_path, frozenset({"definitely-not-python"}))
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "-c", "print('unreachable')"], "cwd": "."},
    )

    result = ToolRuntime(registry).run(call, _prbench_context(tmp_path), approved=True)

    assert result.tool_result.status == "invalid_tool_args"
    assert "not allowed" in result.tool_result.stderr
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


def test_process_run_reports_timeout(tmp_path: Path) -> None:
    registry = ToolRegistry()
    _register_python(registry, tmp_path)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "-c", "import time; time.sleep(5)"], "cwd": ".", "timeout": 1},
    )

    result = ToolRuntime(registry).run(call, _prbench_context(tmp_path), approved=True)

    assert result.tool_result.status == "timeout"
    assert "timed out after 1 seconds" in result.tool_result.stderr.casefold()


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


def test_approval_does_not_match_different_argv(tmp_path: Path) -> None:
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps(
            {
                "grants": [
                    {"tool_name": "process.run", "argv": ["python", "reproduction/a.py"], "cwd": "."}
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    call = ToolCall(
        tool_name="process.run",
        args={"argv": ["python", "reproduction/b.py"], "cwd": "."},
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
                    {"tool_name": "process.run", "argv": ["python", "a.py"], "cwd": "reproduction"}
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    same = ToolCall(
        tool_name="process.run",
        args={"argv": ["python", "a.py"], "cwd": "reproduction/../reproduction"},
    )
    different = ToolCall(
        tool_name="process.run",
        args={"argv": ["python", "a.py"], "cwd": "."},
    )

    assert not manifest(different, PolicyEngine().decide(different, PolicyContext(tmp_path, [], False)))
    assert manifest(same, PolicyEngine().decide(same, PolicyContext(tmp_path, [], False)))


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
