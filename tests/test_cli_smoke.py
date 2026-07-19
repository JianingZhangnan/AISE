from click import unstyle
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
    assert "phycode 0.1.1" in result.stdout.lower()


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
    result = runner.invoke(app, ["prbench", "run", "--help"], terminal_width=160)
    help_text = unstyle(result.stdout)

    assert result.exit_code == 0, help_text
    assert "--workspace" in help_text
    assert "--contract" in help_text
    assert "--approvals" in help_text
    # Rich shortens long option labels to fit its fixed help-table width.
    assert "--approval-wait-sec" in help_text


def test_prbench_approval_wait_option_is_bounded_before_runner_call(tmp_path, monkeypatch):
    import phycode.prbench_eval as prbench_eval

    called = False

    def record_call(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("runner must not be called for an invalid wait")

    monkeypatch.setattr(prbench_eval, "run_prbench", record_call)

    result = runner.invoke(
        prbench_eval.prbench_app,
        [
            "run",
            "--workspace",
            str(tmp_path),
            "--contract",
            str(tmp_path / "contract.json"),
            "--approvals",
            str(tmp_path / "approvals.json"),
            "--approval-wait-seconds",
            "901",
        ],
    )
    error_text = unstyle(result.stderr)

    assert result.exit_code == 2
    assert not called
    assert "approval-wait-seconds" in error_text
    assert "0<=x<=900" in error_text


def test_prbench_approval_wait_option_is_forwarded_to_runner(tmp_path, monkeypatch):
    import phycode.prbench_eval as prbench_eval

    captured = None
    expected = PRBenchRunResult(
        status=PRBenchRunStatus.APPROVAL_REQUIRED,
        model="safe-model",
        tool_calls=1,
    )

    def record_call(*args, **kwargs):
        nonlocal captured
        captured = kwargs["approval_wait_seconds"]
        return expected

    monkeypatch.setattr(prbench_eval, "run_prbench", record_call)

    result = runner.invoke(
        prbench_eval.prbench_app,
        [
            "run",
            "--workspace",
            str(tmp_path),
            "--contract",
            str(tmp_path / "contract.json"),
            "--approvals",
            str(tmp_path / "approvals.json"),
            "--approval-wait-seconds",
            "7",
        ],
    )

    assert result.exit_code == expected.exit_code
    assert captured == 7


def test_main_cli_mounts_the_single_prbench_eval_app():
    import phycode.cli as cli
    import phycode.prbench_eval as prbench_eval

    assert cli.prbench_app is prbench_eval.prbench_app

    module_help = runner.invoke(prbench_eval.prbench_app, ["run", "--help"])
    main_help = runner.invoke(cli.app, ["prbench", "run", "--help"])
    assert module_help.exit_code == main_help.exit_code == 0
    module_help_text = unstyle(module_help.stdout)
    main_help_text = unstyle(main_help.stdout)
    for option in ("--workspace", "--contract", "--approvals", "--max-tool-calls"):
        assert option in module_help_text
        assert option in main_help_text


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


def test_registry_with_explicit_dependencies_does_not_parse_cwd_config(
    tmp_path, monkeypatch
):
    from phycode.cli import build_default_registry
    from phycode.context import MemoryStore
    from phycode.visibility import PathVisibilityPolicy

    (tmp_path / "phycode.toml").write_text("invalid = [toml", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    registry = build_default_registry(
        workspace_root=tmp_path,
        test_command="trusted-test-command",
        memory_store=MemoryStore.ephemeral(),
        visibility=PathVisibilityPolicy(tmp_path),
    )

    assert registry.spec_for("file.read") is not None
