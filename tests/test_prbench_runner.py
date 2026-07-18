import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from phycode.llm import ScriptedLLM
from phycode.prbench_eval import (
    PRBenchRunResult,
    PRBenchRunStatus,
    _status_from_stop_reason,
    prbench_result_lines,
    run_prbench,
)
from prbench_test_support import (
    RaisingLLM as _RaisingLLM,
    RecordingFinalLLM as _RecordingFinalLLM,
    scripted_llm_that_writes_runs_reads_and_finishes as _scripted_llm_that_writes_runs_reads_and_finishes,
    write_public_task_files as _write_public_task_files,
)


def test_prbench_run_status_has_exact_public_values() -> None:
    assert {status.value for status in PRBenchRunStatus} == {
        "completed",
        "approval_required",
        "policy_blocked",
        "provider_error",
        "process_failed",
        "artifact_verification_failed",
        "repeated_no_progress",
        "tool_budget_exhausted",
    }


def test_runner_returns_non_success_when_final_artifacts_are_missing(tmp_path: Path) -> None:
    contract, approvals = _write_public_task_files(tmp_path, approvals=False)
    llm = ScriptedLLM([[{"type": "assistant_final", "payload": {"text": "done"}}]])

    result = run_prbench(tmp_path, contract, approvals, llm=llm, max_tool_calls=2)

    assert result.status == PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED
    assert result.exit_code != 0


@pytest.mark.parametrize("approval_wait_seconds", [-1, 901])
def test_runner_rejects_approval_wait_outside_public_bounds(
    tmp_path: Path,
    approval_wait_seconds: int,
) -> None:
    contract, approvals = _write_public_task_files(tmp_path, approvals=False)
    llm = _RecordingFinalLLM()

    result = run_prbench(
        tmp_path,
        contract,
        approvals,
        llm=llm,
        approval_wait_seconds=approval_wait_seconds,
    )

    assert result.status == PRBenchRunStatus.POLICY_BLOCKED
    assert llm.calls == 0


def test_runner_executes_script_and_writes_sanitized_result(tmp_path: Path) -> None:
    contract, approvals = _write_public_task_files(tmp_path)
    llm = _scripted_llm_that_writes_runs_reads_and_finishes()

    result = run_prbench(
        tmp_path,
        contract,
        approvals,
        llm=llm,
        max_tool_calls=8,
    )

    assert result.status == PRBenchRunStatus.COMPLETED
    assert result.tool_calls == 2
    assert llm.index == 2
    payload = json.loads(
        (tmp_path / ".phycode/prbench/run_result.json").read_text(encoding="utf-8")
    )
    assert payload["status"] == "completed"
    assert "api_key" not in json.dumps(payload).casefold()


def test_runner_does_not_auto_complete_invalid_csv_contract(tmp_path: Path) -> None:
    contract, approvals = _write_public_task_files(tmp_path)
    script = (
        "from pathlib import Path\n"
        "Path('result.csv').write_text('wrong\\nvalue\\n', encoding='utf-8')\n"
    )
    read_status = [
        {
            "type": "tool_call_requested",
            "payload": {"tool_name": "file.read", "args": {"path": "result.csv"}},
        }
    ]
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": "reproduce.py", "content": script},
                    },
                }
            ],
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "process.run",
                        "args": {"argv": [sys.executable, "reproduce.py"], "cwd": "."},
                    },
                }
            ],
            read_status,
            [{"type": "assistant_final", "payload": {"text": "done"}}],
        ]
    )

    result = run_prbench(tmp_path, contract, approvals, llm=llm, max_tool_calls=8)

    assert result.status == PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED
    assert result.tool_calls == 3
    assert llm.index == 4


def test_runner_does_not_auto_complete_direct_writes_without_process_provenance(
    tmp_path: Path,
) -> None:
    contract, approvals = _write_public_task_files(tmp_path)
    approvals.write_text(
        json.dumps(
            {
                "grants": [
                    {"tool_name": "file.write", "path": "reproduce.py"},
                    {"tool_name": "file.write", "path": "result.csv"},
                ]
            }
        ),
        encoding="utf-8",
    )
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": "reproduce.py", "content": "print('hello')\n"},
                    },
                }
            ],
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": "result.csv", "content": "message\nhello\n"},
                    },
                }
            ],
            [
                {
                    "type": "tool_call_requested",
                    "payload": {"tool_name": "file.read", "args": {"path": "result.csv"}},
                }
            ],
            [{"type": "assistant_final", "payload": {"text": "done"}}],
        ]
    )

    result = run_prbench(tmp_path, contract, approvals, llm=llm, max_tool_calls=8)

    assert result.status == PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED
    assert result.tool_calls == 3
    assert llm.index == 4


def test_runner_blocks_lexical_ground_truth_before_calling_llm(tmp_path: Path) -> None:
    contract, approvals = _write_public_task_files(tmp_path, approvals=False)
    hidden = tmp_path / "_ground_truth"
    hidden.mkdir()
    (hidden / "sentinel.txt").write_text("must stay hidden", encoding="utf-8")
    llm = _RecordingFinalLLM()

    result = run_prbench(tmp_path, contract, approvals, llm=llm)

    assert result.status == PRBenchRunStatus.POLICY_BLOCKED
    assert llm.calls == 0


def test_runner_blocks_resolved_ground_truth_alias_before_calling_llm(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contract, approvals = _write_public_task_files(workspace, approvals=False)
    hidden = tmp_path / "_ground_truth"
    hidden.mkdir()
    (hidden / "sentinel.txt").write_text("must stay hidden", encoding="utf-8")
    try:
        (workspace / "public_alias").symlink_to(hidden, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    llm = _RecordingFinalLLM()

    result = run_prbench(workspace, contract, approvals, llm=llm)

    assert result.status == PRBenchRunStatus.POLICY_BLOCKED
    assert llm.calls == 0


def test_workspace_scan_error_fails_closed_before_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import phycode.prbench_eval as prbench_eval

    contract, approvals = _write_public_task_files(tmp_path, approvals=False)
    observed_errors: list[str] = []

    def failing_walk(top, *, followlinks, onerror):
        del top, followlinks
        error = PermissionError("deterministic scan denial")
        observed_errors.append(str(error))
        onerror(error)
        return iter(())

    monkeypatch.setattr(prbench_eval.os, "walk", failing_walk)
    llm = _RecordingFinalLLM()

    result = run_prbench(tmp_path, contract, approvals, llm=llm)

    assert observed_errors == ["deterministic scan denial"]
    assert result.status == PRBenchRunStatus.POLICY_BLOCKED
    assert llm.calls == 0


@pytest.mark.parametrize("external_input", ["contract", "approvals"])
def test_runner_rejects_control_files_outside_workspace_before_llm(
    tmp_path: Path, external_input: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contract, approvals = _write_public_task_files(workspace, approvals=False)
    if external_input == "contract":
        outside = tmp_path / "outside-contract.json"
        outside.write_text(contract.read_text(encoding="utf-8"), encoding="utf-8")
        contract = outside
    else:
        outside = tmp_path / "outside-approvals.json"
        outside.write_text(approvals.read_text(encoding="utf-8"), encoding="utf-8")
        approvals = outside
    llm = _RecordingFinalLLM()

    result = run_prbench(workspace, contract, approvals, llm=llm)

    assert result.status == PRBenchRunStatus.POLICY_BLOCKED
    assert llm.calls == 0


def test_runner_rejects_result_directory_symlink_escape_before_llm(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contract, approvals = _write_public_task_files(workspace, approvals=False)
    outside = tmp_path / "outside-results"
    outside.mkdir()
    try:
        (workspace / ".phycode").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    llm = _RecordingFinalLLM()

    result = run_prbench(workspace, contract, approvals, llm=llm)

    assert result.status == PRBenchRunStatus.POLICY_BLOCKED
    assert llm.calls == 0
    assert list(outside.iterdir()) == []


def test_unexpected_write_requires_approval_and_does_not_execute(tmp_path: Path) -> None:
    contract, approvals = _write_public_task_files(tmp_path, approvals=False)
    unexpected_write = [
        {
            "type": "tool_call_requested",
            "payload": {
                "tool_name": "file.write",
                "args": {"path": "unexpected.py", "content": "print('unexpected')\n"},
            },
        }
    ]
    llm = ScriptedLLM([unexpected_write, unexpected_write, unexpected_write])

    result = run_prbench(tmp_path, contract, approvals, llm=llm, max_tool_calls=8)

    assert result.status == PRBenchRunStatus.APPROVAL_REQUIRED
    assert not (tmp_path / "unexpected.py").exists()


def test_policy_denial_maps_to_policy_blocked(tmp_path: Path) -> None:
    contract, approvals = _write_public_task_files(tmp_path, approvals=False)
    blocked_read = [
        {
            "type": "tool_call_requested",
            "payload": {
                "tool_name": "file.read",
                "args": {"path": "_ground_truth/sentinel.txt"},
            },
        }
    ]
    llm = ScriptedLLM([blocked_read, blocked_read, blocked_read])

    result = run_prbench(tmp_path, contract, approvals, llm=llm, max_tool_calls=8)

    assert result.status == PRBenchRunStatus.POLICY_BLOCKED


def test_failed_process_maps_to_process_failed(tmp_path: Path) -> None:
    contract, approvals = _write_public_task_files(tmp_path, approvals=False)
    (tmp_path / "reproduce.py").write_text("raise SystemExit(7)\n", encoding="utf-8")
    approvals.write_text(
        json.dumps(
            {
                "grants": [
                    {
                        "tool_name": "process.run",
                        "argv": [sys.executable, "reproduce.py"],
                        "cwd": ".",
                    }
                ]
                * 3
            }
        ),
        encoding="utf-8",
    )
    failed_process = [
        {
            "type": "tool_call_requested",
            "payload": {
                "tool_name": "process.run",
                "args": {"argv": [sys.executable, "reproduce.py"], "cwd": "."},
            },
        }
    ]
    llm = ScriptedLLM([failed_process, failed_process, failed_process])

    result = run_prbench(tmp_path, contract, approvals, llm=llm, max_tool_calls=8)

    assert result.status == PRBenchRunStatus.PROCESS_FAILED


def test_consecutive_no_progress_maps_to_repeated_no_progress(tmp_path: Path) -> None:
    contract, approvals = _write_public_task_files(tmp_path, approvals=False)
    repeated = [
        {
            "type": "tool_call_requested",
            "payload": {"tool_name": "file.read", "args": {"path": "instruction.md"}},
        }
    ]
    llm = ScriptedLLM([repeated, repeated, repeated])

    result = run_prbench(tmp_path, contract, approvals, llm=llm, max_tool_calls=8)

    assert result.status == PRBenchRunStatus.REPEATED_NO_PROGRESS


def test_exhausted_tool_budget_maps_to_tool_budget_exhausted(tmp_path: Path) -> None:
    contract, approvals = _write_public_task_files(tmp_path, approvals=False)
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {"tool_name": "file.read", "args": {"path": "instruction.md"}},
                }
            ]
        ]
    )

    result = run_prbench(tmp_path, contract, approvals, llm=llm, max_tool_calls=1)

    assert result.status == PRBenchRunStatus.TOOL_BUDGET_EXHAUSTED


def test_provider_exception_is_generic_and_not_persisted_verbatim(tmp_path: Path) -> None:
    contract, approvals = _write_public_task_files(tmp_path, approvals=False)

    result = run_prbench(tmp_path, contract, approvals, llm=_RaisingLLM())

    assert result.status == PRBenchRunStatus.PROVIDER_ERROR
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / ".phycode").rglob("*")
        if path.is_file()
    )
    assert "private.example" not in persisted
    assert "secret-approval-argument" not in persisted
    assert "provider request failed" in persisted.casefold()


def test_missing_provider_environment_returns_provider_error_without_echo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, approvals = _write_public_task_files(tmp_path, approvals=False)
    for name in ("PHYCODE_API_KEY", "PHYCODE_BASE_URL", "PHYCODE_MODEL"):
        monkeypatch.delenv(name, raising=False)

    result = run_prbench(tmp_path, contract, approvals)

    assert result.status == PRBenchRunStatus.PROVIDER_ERROR
    assert result.model == "unconfigured"
    assert "echo" not in (tmp_path / ".phycode/prbench/run_result.json").read_text(
        encoding="utf-8"
    ).casefold()


def test_run_result_contains_only_relative_artifact_and_trace_paths(tmp_path: Path) -> None:
    contract, approvals = _write_public_task_files(tmp_path)

    result = run_prbench(
        tmp_path,
        contract,
        approvals,
        llm=_scripted_llm_that_writes_runs_reads_and_finishes(),
        max_tool_calls=8,
    )

    payload = json.loads(
        (tmp_path / ".phycode/prbench/run_result.json").read_text(encoding="utf-8")
    )
    assert result.status == PRBenchRunStatus.COMPLETED
    assert all(not Path(item["path"]).is_absolute() for item in payload["artifacts"])
    assert not Path(payload["trace"]["path"]).is_absolute()
    assert str(tmp_path) not in json.dumps(payload)
    assert not (tmp_path / ".phycode/prbench/run_result.json.tmp").exists()


def test_runner_constructs_provider_only_from_phycode_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import phycode.prbench_eval as prbench_eval

    contract, approvals = _write_public_task_files(tmp_path, approvals=False)
    captured: dict[str, object] = {}

    class _EnvironmentAdapter:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)
            self.model = str(kwargs["model"])

        def generate(self, messages, tools):
            del messages, tools
            return ScriptedLLM(
                [[{"type": "assistant_final", "payload": {"text": "done"}}]]
            ).generate([], [])

    monkeypatch.setattr(
        prbench_eval,
        "OpenAICompatibleChatAdapter",
        _EnvironmentAdapter,
        raising=False,
    )
    monkeypatch.setenv("PHYCODE_API_KEY", "test-provider-secret")
    monkeypatch.setenv("PHYCODE_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("PHYCODE_MODEL", "environment-model")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")

    result = run_prbench(tmp_path, contract, approvals)

    assert result.status == PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED
    assert captured["api_key"] == "test-provider-secret"
    assert captured["base_url"] == "https://provider.example/v1"
    assert captured["model"] == "environment-model"
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / ".phycode").rglob("*")
        if path.is_file()
    )
    assert "test-provider-secret" not in persisted
    assert "provider.example" not in persisted


def test_prompt_contains_only_public_instruction_paper_and_input_names(tmp_path: Path) -> None:
    contract, approvals = _write_public_task_files(tmp_path, approvals=False)
    (tmp_path / "input.txt").write_text("public input", encoding="utf-8")
    payload = json.loads(contract.read_text(encoding="utf-8"))
    payload["input_files"] = ["input.txt"]
    contract.write_text(json.dumps(payload), encoding="utf-8")

    class _PromptRecorder:
        def __init__(self) -> None:
            self.messages = []

        def generate(self, messages, tools):
            del tools
            self.messages.append(messages)
            return ScriptedLLM(
                [[{"type": "assistant_final", "payload": {"text": "done"}}]]
            ).generate([], [])

    llm = _PromptRecorder()

    run_prbench(tmp_path, contract, approvals, llm=llm)

    rendered = json.dumps(llm.messages)
    assert "Create reproduce.py" in rendered
    assert "Public paper" in rendered
    assert "input.txt" in rendered


def test_unknown_stop_reason_fails_closed_as_provider_error() -> None:
    assert _status_from_stop_reason("future_unknown_reason") == (
        PRBenchRunStatus.PROVIDER_ERROR
    )


def test_shared_cli_summary_redacts_secret_shaped_model() -> None:
    secret_model = "sk-mistaken-model-secret-123456789"

    lines = prbench_result_lines(
        PRBenchRunResult(
            status=PRBenchRunStatus.PROVIDER_ERROR,
            model=secret_model,
            tool_calls=0,
        )
    )

    rendered = "\n".join(lines)
    assert secret_model not in rendered
    assert "model=[REDACTED_SECRET]" in rendered


def test_module_and_main_cli_use_same_provider_error_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from phycode.cli import app

    contract, approvals = _write_public_task_files(tmp_path, approvals=False)
    for name in ("PHYCODE_API_KEY", "PHYCODE_BASE_URL", "PHYCODE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    arguments = [
        "run",
        "--workspace",
        str(tmp_path),
        "--contract",
        str(contract),
        "--approvals",
        str(approvals),
    ]

    cli_result = CliRunner().invoke(app, ["prbench", *arguments])
    environment = dict(os.environ)
    for name in ("PHYCODE_API_KEY", "PHYCODE_BASE_URL", "PHYCODE_MODEL"):
        environment.pop(name, None)
    module_result = subprocess.run(
        [sys.executable, "-m", "phycode.prbench_eval", *arguments],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert cli_result.exit_code == 4
    assert module_result.returncode == cli_result.exit_code
    assert "provider_error" in cli_result.stdout
    assert "provider_error" in module_result.stdout
