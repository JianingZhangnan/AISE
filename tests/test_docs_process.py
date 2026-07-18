from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_readme_documents_all_user_facing_commands():
    readme = _read("README.md")
    required = [
        "uv sync --dev",
        "uv run phycode version",
        "uv run phycode tools list",
        'uv run phycode run "hello"',
        "uv run phycode chat",
        "uv run phycode demo guardrail",
        "uv run phycode demo feedback",
        "uv run phycode demo policy",
        "uv run phycode keys set openai-compatible",
        "uv run phycode keys status openai-compatible",
        "uv run phycode keys clear openai-compatible",
        "uv run pytest",
        "uvx pyright",
    ]
    for command in required:
        assert command in readme
    assert "操作系统钥匙串" in readme
    assert ".env" in readme
    assert "不得提交" in readme


def test_plan_marks_tasks_10_to_12_complete_and_records_scope():
    plan = _read("PLAN.md")
    assert "### Task 10: CLI Run、Chat、Config、Keys 和工具列表 - ✅" in plan
    assert "### Task 11: 确定性演示 - ✅" in plan
    assert "### Task 12: README、过程记录收尾和最终验证 - ✅" in plan
    assert "uv run pytest" in plan
    assert "uvx pyright" in plan


def test_process_documents_final_validation_and_review_ready_branch():
    process = _read("SPEC_PROCESS.md")
    assert "Task 10–12 收尾" in process
    assert "严格 CLI 测试" in process
    assert "uv run pytest" in process
    assert "uvx pyright" in process
    assert "codex/task-10-12" in process


def test_agent_log_records_task_10_to_12_execution_and_subagent_use():
    log = _read("AGENT_LOG.md")
    assert "Task 10–12" in log
    assert "gpt-5.5" in log
    assert "subagent" in log.lower()
    assert "tests/test_cli_commands.py" in log
    assert "tests/test_docs_process.py" in log


def test_prbench_readme_separates_deterministic_real_runner_and_official_smoke():
    readme = _read("README.md")
    required = [
        "phycode prbench run",
        "process.run",
        "3e5bee4545cad2138832f06302e9c98bd81f5216",
        "run_public_smoke.ps1",
        "_ground_truth",
        "Docker daemon",
        "默认 `uv run pytest`",
    ]
    for item in required:
        assert item in readme
    assert "确定性测试" in readme
    assert "真实模型 runner smoke" in readme
    assert "官方 Docker evaluator" in readme


def test_prbench_process_documents_refactor_and_real_api_acceptance_boundary():
    plan = _read("PLAN.md")
    log = _read("AGENT_LOG.md")
    process = _read("SPEC_PROCESS.md")

    for task_number in range(14, 21):
        assert f"- [x] Task {task_number}" in plan
    for document in (plan, log, process):
        assert "PRBench 运行时真正重构" in document
        assert "旧 parser" in document
        assert "真实 API" in document
        assert "aaatest_helloworld" in document
        assert "bbbtest_alphabet" in document
    assert "主 agent" in log
    assert "subagent" in log.lower()


def test_public_smoke_script_has_closed_credential_and_approval_contract():
    script = _read("integrations/prbench/run_public_smoke.ps1")

    assert "[Parameter(Mandatory=$true)][string]$EvaluatorRoot" in script
    assert "[Parameter(Mandatory=$true)][string]$WheelPath" in script
    assert (
        "[ValidateSet('aaatest_helloworld','bbbtest_alphabet')][string[]]$TaskIds"
        in script
    )
    assert set(re.findall(r"\$env:(PHYCODE_[A-Z_]+)", script)) == {
        "PHYCODE_API_KEY",
        "PHYCODE_BASE_URL",
        "PHYCODE_MODEL",
    }
    assert "NewTextDocument" not in script
    assert ".env" not in script
    assert "Write-Output" not in script
    for argument in (
        "apply_adapter.py",
        'a2a-sdk[http-server]==0.3.8',
        "--task-id",
        "--white-agent-type",
        "--green-agent-type",
        "opencode",
        "--phycode-contract",
        "--phycode-approvals",
    ):
        assert argument in script
    for exact_target in (
        "reproduction/hello.py",
        "reproduction/alphabet.py",
        "/usr/local/bin/python",
    ):
        assert exact_target in script
    assert "expected_files" not in script
    for variable in ("OPENCODE_API_KEY", "OPENCODE_BASE_URL", "OPENCODE_MODEL"):
        assert variable in script
    assert "$env:OPENCODE_API_KEY = $env:PHYCODE_API_KEY" in script
    assert "$env:OPENCODE_BASE_URL = $env:PHYCODE_BASE_URL" in script
    assert "$env:OPENCODE_MODEL = 'openai/' + $env:PHYCODE_MODEL" in script
    assert "[Environment]::SetEnvironmentVariable($name, $null, 'Process')" in script
    assert not re.search(r"Write-(?:Host|Output).*\$env:", script, flags=re.IGNORECASE)
    assert 'a2a-sdk[http-server]==0.3.8' in _read("README.md")
