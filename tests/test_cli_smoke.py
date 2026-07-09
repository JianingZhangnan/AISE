from typer.testing import CliRunner

from phycode.cli import app


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
