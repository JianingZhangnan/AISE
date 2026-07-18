from typer.testing import CliRunner

from phycode.cli import app
from phycode.prbench_eval import (
    PRBenchArtifactSummary,
    PRBenchRunResult,
    PRBenchRunStatus,
    PRBenchTraceSummary,
)


runner = CliRunner()


def test_version_command_prints_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "phycode" in result.stdout.lower()


def test_tools_list_command_exists():
    result = runner.invoke(app, ["tools", "list"])
    assert result.exit_code == 0
    assert "file.read" in result.stdout
    assert "search.grep" in result.stdout


def test_demo_guardrail_command_blocks_dangerous_command():
    result = runner.invoke(app, ["demo", "guardrail"])
    assert result.exit_code == 0
    assert "policy_blocked" in result.stdout


def test_demo_unknown_name_exits_nonzero():
    result = runner.invoke(app, ["demo", "nope"])
    assert result.exit_code != 0


def test_prbench_run_help_exposes_required_paths():
    result = runner.invoke(app, ["prbench", "run", "--help"])

    assert result.exit_code == 0, result.stdout
    assert "--workspace" in result.stdout
    assert "--contract" in result.stdout
    assert "--approvals" in result.stdout


def test_prbench_cli_prints_only_safe_summary_and_uses_result_exit_code(
    tmp_path, monkeypatch
):
    import phycode.prbench_eval as prbench_eval

    expected = PRBenchRunResult(
        status=PRBenchRunStatus.PROCESS_FAILED,
        model="safe-model",
        tool_calls=3,
        artifacts=(PRBenchArtifactSummary(path="result.csv", exists=False),),
        trace=PRBenchTraceSummary(path=".phycode/prbench/traces/trace.jsonl", events=7),
    )
    monkeypatch.setattr(prbench_eval, "run_prbench", lambda *args, **kwargs: expected)

    result = runner.invoke(
        app,
        [
            "prbench",
            "run",
            "--workspace",
            str(tmp_path),
            "--contract",
            str(tmp_path / "contract.json"),
            "--approvals",
            str(tmp_path / "approvals.json"),
        ],
    )

    assert result.exit_code == expected.exit_code
    assert "status=process_failed" in result.stdout
    assert "model=safe-model" in result.stdout
    assert "tool_calls=3" in result.stdout
    assert "artifact=result.csv" in result.stdout
    assert "trace=" not in result.stdout
    assert "base_url" not in result.stdout.casefold()
    assert "api_key" not in result.stdout.casefold()
