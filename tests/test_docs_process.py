from __future__ import annotations

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
