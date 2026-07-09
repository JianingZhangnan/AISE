from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from phycode.cli import app
from phycode.credentials import CredentialStore, InMemoryCredentialBackend


runner = CliRunner()


def _json_from_stdout(stdout: str) -> dict:
    return json.loads(stdout)


def _force_no_credentials(monkeypatch) -> None:
    """Make build_agent resolve to EchoLLM regardless of the machine keyring."""
    monkeypatch.setattr(
        "phycode.cli.CredentialStore",
        lambda *a, **k: CredentialStore(backend=InMemoryCredentialBackend()),
    )


def test_build_llm_falls_back_to_echo_without_credentials(tmp_path):
    from phycode.cli import _build_llm
    from phycode.config import LLMConfig, ProjectConfig, WorkspaceConfig
    from phycode.llm import EchoLLM

    config = ProjectConfig(
        workspace=WorkspaceConfig(root=tmp_path),
        llm=LLMConfig(provider="openai-compatible", base_url="https://x/v1", model="m"),
    )
    store = CredentialStore(backend=InMemoryCredentialBackend())
    assert isinstance(_build_llm(config, store), EchoLLM)


def test_build_llm_uses_openai_adapter_when_key_configured(tmp_path):
    from phycode.cli import _build_llm
    from phycode.config import LLMConfig, ProjectConfig, WorkspaceConfig
    from phycode.llm import OpenAICompatibleChatAdapter

    config = ProjectConfig(
        workspace=WorkspaceConfig(root=tmp_path),
        llm=LLMConfig(provider="openai-compatible", base_url="https://llm.example/v1", model="demo-model"),
    )
    store = CredentialStore(backend=InMemoryCredentialBackend())
    store.set_key("openai-compatible", "sk-test-1234567890")
    llm = _build_llm(config, store)
    assert isinstance(llm, OpenAICompatibleChatAdapter)
    assert llm.model == "demo-model"
    assert llm.base_url == "https://llm.example/v1"


def test_chat_survives_non_final_turn(monkeypatch):
    import phycode.cli as cli
    from phycode.agent import AgentRunResult

    outcomes = iter(
        [
            AgentRunResult(final_text=None, events=[], stopped_reason="max_steps"),
            AgentRunResult(final_text="done", events=[], stopped_reason="final"),
        ]
    )

    class Loop:
        def run(self, user_input: str) -> AgentRunResult:
            return next(outcomes)

    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: Loop())
    result = runner.invoke(app, ["chat"], input="one\ntwo\n/exit\n")

    assert result.exit_code == 0, result.stdout
    assert "max_steps" in result.stdout  # non-final turn reported, session kept alive
    assert "done" in result.stdout  # a later turn still ran


def test_chat_slash_model_sets_config_and_reloads(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _force_no_credentials(monkeypatch)
    from phycode.config import load_project_config

    result = runner.invoke(app, ["chat"], input="/model deepseek-v4-flash\n/exit\n")

    assert result.exit_code == 0, result.stdout
    assert load_project_config(tmp_path).llm.model == "deepseek-v4-flash"


def test_chat_slash_url_sets_base_url(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _force_no_credentials(monkeypatch)
    from phycode.config import load_project_config

    result = runner.invoke(app, ["chat"], input="/url https://real.example/v1\n/exit\n")

    assert result.exit_code == 0, result.stdout
    assert load_project_config(tmp_path).llm.base_url == "https://real.example/v1"


def test_chat_slash_help_lists_commands(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _force_no_credentials(monkeypatch)

    result = runner.invoke(app, ["chat"], input="/help\n/exit\n")

    assert result.exit_code == 0
    assert "/model" in result.stdout
    assert "/url" in result.stdout
    assert "/exit" in result.stdout


def test_chat_slash_unknown_is_reported_and_not_sent_to_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _force_no_credentials(monkeypatch)

    result = runner.invoke(app, ["chat"], input="/bogus\n/exit\n")

    assert result.exit_code == 0
    assert "unknown command" in result.stdout.lower()
    assert "Echo:" not in result.stdout  # a slash line is never forwarded to the agent


def test_interactive_approver_uses_confirm(monkeypatch):
    import phycode.cli as cli
    from phycode.models import PolicyAction, PolicyDecision, ToolCall

    monkeypatch.setattr(cli.typer, "confirm", lambda *a, **k: True)
    approved = cli._interactive_approver(
        ToolCall(tool_name="file.write", args={"path": "x"}),
        PolicyDecision(tool_call_id="1", decision=PolicyAction.ASK, rule_id="r", reason="why"),
    )
    assert approved is True


def test_run_command_uses_echo_agent_and_writes_redacted_trace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _force_no_credentials(monkeypatch)
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
    _force_no_credentials(monkeypatch)
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


def test_config_set_writes_value_read_back(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    set_result = runner.invoke(app, ["config", "set", "llm", "base_url", "https://real.example/v1"])
    assert set_result.exit_code == 0, set_result.stdout
    read_result = runner.invoke(app, ["config", "read"])
    payload = _json_from_stdout(read_result.stdout)
    assert payload["llm"]["base_url"] == "https://real.example/v1"


def test_config_set_rejects_unknown_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["config", "set", "secrets", "api_key", "leak"])
    assert result.exit_code != 0
    assert not (tmp_path / "phycode.toml").exists()


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
