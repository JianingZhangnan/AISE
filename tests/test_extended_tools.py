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
