from pathlib import Path

from phycode.credentials import CredentialStore, InMemoryCredentialBackend
from phycode.context import MemoryStore
from phycode.models import PolicyAction, ToolCall
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.search_tools import register_search_tools
from phycode.tools.state_tools import register_state_tools


def _runtime_with_state(tmp_path: Path) -> ToolRuntime:
    registry = ToolRegistry()
    register_search_tools(registry, workspace_root=tmp_path)
    register_state_tools(
        registry,
        workspace_root=tmp_path,
        memory_store=MemoryStore(tmp_path / ".phycode" / "memory.jsonl"),
        credential_store=CredentialStore(backend=InMemoryCredentialBackend()),
    )
    return ToolRuntime(registry)


def test_search_grep_finds_matches(tmp_path: Path):
    (tmp_path / "a.py").write_text("alpha\nneedle here\n", encoding="utf-8")
    result = _runtime_with_state(tmp_path).run(
        ToolCall(tool_name="search.grep", args={"pattern": "needle"}),
        PolicyContext(tmp_path, [], True),
    )
    assert result.policy.decision == PolicyAction.ALLOW
    assert "needle" in result.tool_result.stdout
    assert "a.py" in result.tool_result.stdout


def test_search_glob_lists_files(tmp_path: Path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("y", encoding="utf-8")
    result = _runtime_with_state(tmp_path).run(
        ToolCall(tool_name="search.glob", args={"pattern": "*.py"}),
        PolicyContext(tmp_path, [], True),
    )
    assert "a.py" in result.tool_result.stdout
    assert "b.txt" not in result.tool_result.stdout


def test_search_grep_does_not_leak_credential_files(tmp_path: Path):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-live-SECRET1234567890\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("KEY = 1\n", encoding="utf-8")
    result = _runtime_with_state(tmp_path).run(
        ToolCall(tool_name="search.grep", args={"pattern": "KEY"}),
        PolicyContext(tmp_path, [], True),
    )
    assert "SECRET1234567890" not in result.tool_result.stdout
    assert ".env" not in result.tool_result.stdout
    assert "app.py" in result.tool_result.stdout  # non-credential files still searched


def test_search_glob_skips_credential_files(tmp_path: Path):
    (tmp_path / ".env").write_text("x", encoding="utf-8")
    (tmp_path / "a.py").write_text("y", encoding="utf-8")
    result = _runtime_with_state(tmp_path).run(
        ToolCall(tool_name="search.glob", args={"pattern": "*"}),
        PolicyContext(tmp_path, [], True),
    )
    assert ".env" not in result.tool_result.stdout


def test_search_glob_cannot_escape_workspace(tmp_path: Path):
    outside = tmp_path / "outside_marker.txt"
    outside.write_text("secret", encoding="utf-8")
    root = tmp_path / "ws"
    root.mkdir()
    registry = ToolRegistry()
    register_search_tools(registry, workspace_root=root)
    result = ToolRuntime(registry).run(
        ToolCall(tool_name="search.glob", args={"pattern": "../*.txt"}),
        PolicyContext(root, [], True),
    )
    assert "outside_marker" not in result.tool_result.stdout


def test_config_write_rejects_newline_value_without_corrupting(tmp_path: Path):
    runtime = _runtime_with_state(tmp_path)
    (tmp_path / "phycode.toml").write_text('[test]\ncommand = "pytest"\n', encoding="utf-8")
    bad = runtime.run(
        ToolCall(tool_name="config.write", args={"section": "test", "key": "command", "value": "a\nb"}),
        PolicyContext(tmp_path, [], True),
        approved=True,
    )
    assert bad.tool_result.status != "ok"
    # The existing config is still readable (was not corrupted).
    read = runtime.run(
        ToolCall(tool_name="config.read", args={}),
        PolicyContext(tmp_path, [], True),
    )
    assert read.tool_result.status == "ok"
    assert "pytest" in read.tool_result.stdout


def test_config_write_preserves_existing_allowlist(tmp_path: Path):
    (tmp_path / "phycode.toml").write_text(
        '[workspace]\nallowlist = ["a", "b"]\n\n[test]\ncommand = "old"\n', encoding="utf-8"
    )
    runtime = _runtime_with_state(tmp_path)
    runtime.run(
        ToolCall(tool_name="config.write", args={"section": "test", "key": "command", "value": "new"}),
        PolicyContext(tmp_path, [], True),
        approved=True,
    )
    import tomllib

    data = tomllib.loads((tmp_path / "phycode.toml").read_text(encoding="utf-8"))
    assert data["workspace"]["allowlist"] == ["a", "b"]
    assert data["test"]["command"] == "new"


def test_memory_write_then_read_round_trip(tmp_path: Path):
    runtime = _runtime_with_state(tmp_path)
    write = runtime.run(
        ToolCall(tool_name="memory.write", args={"category": "test_command", "content": "uv run pytest"}),
        PolicyContext(tmp_path, [], True),
        approved=True,
    )
    assert write.policy.decision == PolicyAction.ASK
    assert write.tool_result.status == "ok"
    read = runtime.run(
        ToolCall(tool_name="memory.read", args={}),
        PolicyContext(tmp_path, [], True),
    )
    assert "uv run pytest" in read.tool_result.stdout


def test_config_read_and_write(tmp_path: Path):
    runtime = _runtime_with_state(tmp_path)
    write = runtime.run(
        ToolCall(tool_name="config.write", args={"section": "test", "key": "command", "value": "pytest -q"}),
        PolicyContext(tmp_path, [], True),
        approved=True,
    )
    assert write.tool_result.status == "ok"
    read = runtime.run(
        ToolCall(tool_name="config.read", args={}),
        PolicyContext(tmp_path, [], True),
    )
    assert "pytest -q" in read.tool_result.stdout


def test_keys_status_tool_hides_secret(tmp_path: Path):
    store = CredentialStore(backend=InMemoryCredentialBackend())
    store.set_key("openai-compatible", "sk-super-secret-value")
    registry = ToolRegistry()
    register_state_tools(
        registry,
        workspace_root=tmp_path,
        memory_store=MemoryStore(tmp_path / ".phycode" / "memory.jsonl"),
        credential_store=store,
    )
    result = ToolRuntime(registry).run(
        ToolCall(tool_name="keys.status", args={}),
        PolicyContext(tmp_path, [], True),
    )
    assert result.tool_result.status == "ok"
    assert '"configured":true' in result.tool_result.stdout.replace(" ", "")
    assert "super-secret" not in result.tool_result.stdout
