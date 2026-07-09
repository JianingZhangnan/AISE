from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from phycode.cli import app
from phycode.credentials import CredentialStore, InMemoryCredentialBackend


runner = CliRunner()


def _json_from_stdout(stdout: str) -> dict:
    return json.loads(stdout)


def test_run_command_uses_echo_agent_and_writes_redacted_trace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path(".env").write_text("OPENAI_API_KEY=sk-live-SECRET1234567890\n", encoding="utf-8")

    result = runner.invoke(app, ["run", "hello from cli sk-live-SECRET1234567890"])

    assert result.exit_code == 0, result.stdout
    assert "Echo:" in result.stdout
    assert "hello from cli" in result.stdout
    assert "sk-live-SECRET1234567890" not in result.stdout
    traces = list(Path(".phycode/traces").glob("*.jsonl"))
    assert traces, "run should persist an auditable trace"
    trace_text = "\n".join(path.read_text(encoding="utf-8") for path in traces)
    assert "assistant_final" in trace_text
    assert "sk-live-SECRET1234567890" not in trace_text


def test_run_command_exits_nonzero_when_agent_does_not_finish(monkeypatch):
    import phycode.cli as cli
    from phycode.agent import AgentRunResult

    class NonFinalLoop:
        def run(self, user_input: str) -> AgentRunResult:
            return AgentRunResult(final_text=None, events=[], stopped_reason="max_steps")

    monkeypatch.setattr(cli, "build_agent", lambda mode: NonFinalLoop())

    result = runner.invoke(app, ["run", "unfinished"])

    assert result.exit_code == 1


def test_chat_echoes_multiple_turns_and_exits_cleanly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["chat"], input="first turn\nsecond turn\n/exit\n")

    assert result.exit_code == 0, result.stdout
    assert "PhyCode interactive session" in result.stdout
    assert result.stdout.count("Echo:") == 2
    assert "first turn" in result.stdout
    assert "second turn" in result.stdout


def test_tools_list_exposes_complete_default_registry():
    result = runner.invoke(app, ["tools", "list"])

    assert result.exit_code == 0
    expected_tools = {
        "config.read",
        "config.write",
        "file.edit",
        "file.list",
        "file.read",
        "file.write",
        "keys.status",
        "memory.read",
        "memory.write",
        "search.glob",
        "search.grep",
        "shell.run",
        "test.run",
        "workspace.status",
    }
    listed = {line.split()[0] for line in result.stdout.splitlines() if line.strip()}
    assert expected_tools <= listed
    assert any(line.split()[:2] == ["shell.run", "risky"] for line in result.stdout.splitlines())
    assert any(line.split()[:2] == ["file.read", "safe"] for line in result.stdout.splitlines())


def test_config_read_reports_project_file_values(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("phycode.toml").write_text(
        "\n".join(
            [
                "[agent]",
                "max_steps = 7",
                "",
                "[test]",
                'command = "pytest -q"',
                "",
                "[llm]",
                'provider = "openai-compatible"',
                'base_url = "https://llm.example/v1"',
                'model = "demo-model"',
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["config", "read"])

    assert result.exit_code == 0
    payload = _json_from_stdout(result.stdout)
    assert payload["agent"]["max_steps"] == 7
    assert payload["test"]["command"] == "pytest -q"
    assert payload["llm"]["base_url"] == "https://llm.example/v1"
    assert payload["llm"]["model"] == "demo-model"


def test_keys_set_status_and_clear_do_not_leak_secret(monkeypatch):
    store = CredentialStore(backend=InMemoryCredentialBackend())
    monkeypatch.setattr("phycode.cli.CredentialStore", lambda: store)

    set_result = runner.invoke(app, ["keys", "set", "openai-compatible"], input="sk-cli-secret-value\n")
    assert set_result.exit_code == 0, set_result.stdout
    assert store.get_key("openai-compatible") == "sk-cli-secret-value"
    assert "sk-cli-secret-value" not in set_result.stdout

    status_result = runner.invoke(app, ["keys", "status", "openai-compatible"])
    assert status_result.exit_code == 0
    assert '"configured": true' in status_result.stdout
    assert "sk-cli-secret-value" not in status_result.stdout
    assert "secret" not in status_result.stdout.lower()

    clear_result = runner.invoke(app, ["keys", "clear", "openai-compatible"])
    assert clear_result.exit_code == 0
    assert store.get_key("openai-compatible") is None

    final_status = runner.invoke(app, ["keys", "status", "openai-compatible"])
    assert final_status.exit_code == 0
    assert '"configured": false' in final_status.stdout


def test_keys_set_rejects_blank_secret(monkeypatch):
    store = CredentialStore(backend=InMemoryCredentialBackend())
    monkeypatch.setattr("phycode.cli.CredentialStore", lambda: store)

    result = runner.invoke(app, ["keys", "set", "openai-compatible"], input="\n")

    assert result.exit_code != 0
    assert store.get_key("openai-compatible") is None
