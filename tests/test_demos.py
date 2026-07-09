from pathlib import Path

from phycode.demos import run_feedback_demo, run_guardrail_demo, run_policy_demo


def test_guardrail_demo_blocks_dangerous_command(tmp_path: Path):
    output = run_guardrail_demo(tmp_path)
    assert "policy_blocked" in output
    assert "shell.dangerous_command" in output
    assert "executed=False" in output


def test_policy_demo_shows_approval_required(tmp_path: Path):
    output = run_policy_demo(tmp_path)
    assert "ask" in output
    assert "policy_requires_approval" in output


def test_feedback_demo_changes_next_action(tmp_path: Path):
    output = run_feedback_demo(tmp_path)
    # The failing test drives the loop into a corrective edit and then success.
    assert "test_failed" in output
    assert "file.edit" in output
    assert "success" in output
    # The action genuinely changed: the failure comes before the corrective edit.
    assert output.index("test_failed") < output.index("file.edit")
    # And the bug was actually fixed in the workspace, not just narrated.
    assert (tmp_path / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
