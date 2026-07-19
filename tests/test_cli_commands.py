from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Callable
from typing import cast

from typer.testing import CliRunner

from phycode.agent import AgentLoop
from phycode.cli import app
from phycode.credentials import CredentialStore, InMemoryCredentialBackend


runner = CliRunner()


def _json_from_stdout(stdout: str) -> dict:
    return json.loads(stdout)


def _force_no_credentials(monkeypatch) -> None:
    """Make build_agent resolve to EchoLLM regardless of the machine keyring."""
    monkeypatch.setattr(
        "phycode.composition.CredentialStore",
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


def test_chat_slash_model_strips_surrounding_quotes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _force_no_credentials(monkeypatch)
    from phycode.config import load_project_config

    result = runner.invoke(app, ["chat"], input='/model "kimi-2.7-code"\n/exit\n')

    assert result.exit_code == 0, result.stdout
    assert load_project_config(tmp_path).llm.model == "kimi-2.7-code"  # no literal quotes stored


def test_models_command_lists_provider_models(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class FakeAdapter:
        def list_models(self):
            return ["deepseek-chat", "kimi-k2"]

    monkeypatch.setattr("phycode.cli._build_llm", lambda *a, **k: FakeAdapter())
    result = runner.invoke(app, ["models"])

    assert result.exit_code == 0, result.stdout
    assert "deepseek-chat" in result.stdout
    assert "kimi-k2" in result.stdout


def test_models_command_without_key_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from phycode.llm import EchoLLM

    monkeypatch.setattr("phycode.cli._build_llm", lambda *a, **k: EchoLLM())
    result = runner.invoke(app, ["models"])

    assert result.exit_code != 0


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


class _RecordingStatus:
    def __init__(self, events: list[str], restart_error: str | None) -> None:
        self._events = events
        self._restart_error = restart_error

    def __enter__(self):
        self._events.append("status-enter")
        return self

    def __exit__(self, *args: object) -> None:
        self._events.append("status-exit")

    def stop(self) -> None:
        self._events.append("status-stop")

    def start(self) -> None:
        self._events.append("status-start")
        if self._restart_error is not None:
            raise RuntimeError(self._restart_error)


class _RecordingConsole:
    is_terminal = True

    def __init__(self, events: list[str], restart_error: str | None) -> None:
        self._events = events
        self._restart_error = restart_error

    def status(self, *args: object, **kwargs: object) -> _RecordingStatus:
        return _RecordingStatus(self._events, self._restart_error)


class _ApprovalLoop:
    def __init__(
        self,
        events: list[str],
        approval_handler: Callable[[object, object], bool],
        approvals_per_turn: int,
    ) -> None:
        self._events = events
        self.approval_handler = approval_handler
        self._approvals_per_turn = approvals_per_turn

    def run(self, text: str) -> None:
        self._events.append("run")
        for _ in range(self._approvals_per_turn):
            self.approval_handler(None, None)
        self._events.append("run-after-approval")


def _approval_turn_fixture(
    *,
    approvals_per_turn: int = 1,
    approval_error: str | None = None,
    restart_error: str | None = None,
) -> tuple[
    list[str],
    _ApprovalLoop,
    Callable[[object, object], bool],
    _RecordingConsole,
]:
    events: list[str] = []

    def approve(call: object, decision: object) -> bool:
        events.append("approval")
        if approval_error is not None:
            raise RuntimeError(approval_error)
        return True

    return (
        events,
        _ApprovalLoop(events, approve, approvals_per_turn),
        approve,
        _RecordingConsole(events, restart_error),
    )


def _turn_events(approval_count: int, *, completed: bool) -> list[str]:
    events = ["status-enter", "run"]
    events.extend(["status-stop", "approval", "status-start"] * approval_count)
    if completed:
        events.append("run-after-approval")
    events.append("status-exit")
    return events


def test_run_turn_stops_status_during_approval(monkeypatch):
    import phycode.cli as cli

    events, loop, approve, fake_console = _approval_turn_fixture()
    monkeypatch.setattr(cli, "console", fake_console)

    cli._run_turn(cast(AgentLoop, loop), "test")

    assert events == _turn_events(1, completed=True)
    assert loop.approval_handler is approve


def test_run_turn_keeps_approval_lifecycle_independent_across_calls(monkeypatch):
    import phycode.cli as cli

    events, loop, approve, fake_console = _approval_turn_fixture(approvals_per_turn=2)
    monkeypatch.setattr(cli, "console", fake_console)

    cli._run_turn(cast(AgentLoop, loop), "first")
    assert loop.approval_handler is approve
    cli._run_turn(cast(AgentLoop, loop), "second")

    assert events == _turn_events(2, completed=True) * 2
    assert loop.approval_handler is approve


def test_run_turn_restarts_status_and_restores_approval_handler_after_error(monkeypatch):
    import pytest

    import phycode.cli as cli

    events, loop, approve, fake_console = _approval_turn_fixture(approval_error="approval failed")
    monkeypatch.setattr(cli, "console", fake_console)

    with pytest.raises(RuntimeError, match="approval failed"):
        cli._run_turn(cast(AgentLoop, loop), "test")

    assert events == _turn_events(1, completed=False)
    assert loop.approval_handler is approve


def test_run_turn_preserves_approval_error_when_status_restart_also_fails(monkeypatch):
    import pytest

    import phycode.cli as cli

    events, loop, approve, fake_console = _approval_turn_fixture(
        approval_error="approval failed", restart_error="status restart failed"
    )
    monkeypatch.setattr(cli, "console", fake_console)

    with pytest.raises(RuntimeError, match="approval failed"):
        cli._run_turn(cast(AgentLoop, loop), "test")

    assert events == _turn_events(1, completed=False)
    assert loop.approval_handler is approve


def test_run_turn_propagates_status_restart_error_after_successful_approval(monkeypatch):
    import pytest

    import phycode.cli as cli

    events, loop, approve, fake_console = _approval_turn_fixture(restart_error="status restart failed")
    monkeypatch.setattr(cli, "console", fake_console)

    with pytest.raises(RuntimeError, match="status restart failed"):
        cli._run_turn(cast(AgentLoop, loop), "test")

    assert events == _turn_events(1, completed=False)
    assert loop.approval_handler is approve


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


def test_render_agent_event_shows_activity_and_redacts(capsys):
    from phycode.cli import _render_agent_event
    from phycode.models import AgentEvent, AgentEventType

    _render_agent_event(
        AgentEvent(session_id="s", type=AgentEventType.TOOL_CALL_REQUESTED, payload={"tool_name": "file.read", "args": {"path": "a.txt"}})
    )
    _render_agent_event(
        AgentEvent(session_id="s", type=AgentEventType.ERROR, payload={"message": "boom key=sk-secret1234567890"})
    )
    out = capsys.readouterr().out
    assert "file.read" in out
    assert "error" in out.lower()
    assert "sk-secret1234567890" not in out  # redacted in the live display too


def test_run_streams_tool_activity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    from phycode.llm import ScriptedLLM

    scripted = ScriptedLLM(
        [
            [{"type": "tool_call_requested", "payload": {"tool_name": "file.read", "args": {"path": "a.txt"}}}],
            [{"type": "assistant_final", "payload": {"text": "all done"}}],
        ]
    )
    monkeypatch.setattr("phycode.cli._build_llm", lambda *a, **k: scripted)

    result = runner.invoke(app, ["run", "read it"])

    assert result.exit_code == 0, result.stdout
    assert "file.read" in result.stdout  # tool activity is shown live
    assert "all done" in result.stdout  # final answer is shown


def test_run_command_exits_nonzero_when_agent_does_not_finish(monkeypatch):
    import phycode.cli as cli
    from phycode.agent import AgentRunResult

    class NonFinalLoop:
        def run(self, user_input: str) -> AgentRunResult:
            return AgentRunResult(final_text=None, events=[], stopped_reason="max_steps")

    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: NonFinalLoop())

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
        "calculator.calculate",
        "config.read",
        "config.write",
        "file.edit",
        "file.inspect",
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
        "web.fetch",
        "web.search",
    }
    listed = {line.split()[0] for line in result.stdout.splitlines() if line.strip()}
    assert expected_tools <= listed
    assert any(line.split()[:2] == ["shell.run", "risky"] for line in result.stdout.splitlines())
    assert any(line.split()[:2] == ["file.read", "safe"] for line in result.stdout.splitlines())


def test_gaia_profile_only_exposes_research_tools(tmp_path, monkeypatch):
    from phycode.cli import build_agent
    from phycode.llm import EchoLLM
    from phycode.models import AgentProfile, SessionMode

    monkeypatch.chdir(tmp_path)
    loop = build_agent(SessionMode.NON_INTERACTIVE, llm=EchoLLM(), profile=AgentProfile.GAIA)
    names = {spec.name for spec in loop.tool_runtime.registry.list_specs()}

    assert names == {
        "calculator.calculate",
        "file.inspect",
        "file.list",
        "file.read",
        "web.fetch",
        "web.search",
    }
    assert "shell.run" not in names
    assert "file.write" not in names


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


def test_clean_api_key_rejects_non_ascii_and_blank_and_strips():
    from phycode.cli import _clean_api_key

    import pytest

    assert _clean_api_key("  sk-abc123  ") == "sk-abc123"
    with pytest.raises(ValueError):
        _clean_api_key("   ")
    with pytest.raises(ValueError):
        _clean_api_key("sk-​hidden")  # zero-width space from a bad copy/paste
    with pytest.raises(ValueError):
        _clean_api_key("sk-密钥")


def test_keys_set_rejects_non_ascii_key(monkeypatch):
    store = CredentialStore(backend=InMemoryCredentialBackend())
    monkeypatch.setattr("phycode.cli.CredentialStore", lambda: store)

    result = runner.invoke(app, ["keys", "set", "openai-compatible"], input="sk-密钥示例值\n")

    assert result.exit_code != 0
    assert store.get_key("openai-compatible") is None


def test_keys_set_strips_surrounding_whitespace(monkeypatch):
    store = CredentialStore(backend=InMemoryCredentialBackend())
    monkeypatch.setattr("phycode.cli.CredentialStore", lambda: store)

    result = runner.invoke(app, ["keys", "set", "openai-compatible"], input="  sk-abc123  \n")

    assert result.exit_code == 0
    assert store.get_key("openai-compatible") == "sk-abc123"
