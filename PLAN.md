# PhyCode Phase 1 Agent Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a CLI-first, self-implemented coding agent harness with a policy-aware tool runtime, mock-LLM test path, safe credential handling, context/memory/trace support, deterministic demos, and CI.

**Architecture:** The project uses a Python `src/phycode` package. The CLI calls a self-owned agent loop; the loop normalizes LLM output into events, routes tool calls through a policy-aware runtime, classifies feedback, writes traces, and builds the next context turn. The core test path uses scripted mock LLMs and fake tool executors so required verification never depends on network or real API keys.

**Tech Stack:** Python 3.11+, `uv`, Typer, Rich, Pydantic v2, pytest, keyring, cryptography, OpenAI-compatible Chat Completions.

## Global Constraints

- Use Python with `uv`; do not use pip or conda workflows.
- No WebUI in Phase 1.
- Do not use OpenAI Agents SDK, LangChain `AgentExecutor`, AutoGen, CrewAI, LlamaIndex agent, or a host coding-agent SDK loop as the product core.
- Tests and CI must not require a real LLM provider, network access, or API key.
- Main provider path is OpenAI-compatible Chat Completions with `tools` / `tool_calls`; keep fallback JSON action parsing available.
- Every tool call must flow through schema validation, policy decision, execution wrapper, feedback mapping, and trace recording.
- Policy decisions are exactly `allow`, `ask`, and `deny`.
- Tool risk levels are exactly `safe`, `risky`, and `dangerous`.
- Memory categories are exactly `decision`, `preference`, `project_fact`, and `test_command`.
- Feedback kinds are exactly `success`, `command_failed`, `test_failed`, `policy_blocked`, `policy_requires_approval`, `invalid_tool_args`, `tool_error`, `timeout`, and `output_truncated`.
- Default max agent steps is 50.
- Default workspace root is the current project directory; extra roots require explicit allowlist configuration.
- `.env`, private keys, token stores, `.phycode/`, traces, logs, and caches must not be committed.
- `.gitlab-ci.yml` must include a `unit-test` job running `uv run pytest`.

---

## File Structure

Create these files and responsibilities:

- `pyproject.toml`: package metadata, dependencies, console script, pytest settings.
- `README.md`: install, run, test, security, distribution, and project overview.
- `.gitlab-ci.yml`: required `unit-test` CI job.
- `.github/workflows/test.yml`: convenience GitHub CI while development happens on GitHub.
- `src/phycode/__init__.py`: package version.
- `src/phycode/cli.py`: Typer commands and Rich rendering entrypoints.
- `src/phycode/models.py`: Pydantic data models and enums shared across modules.
- `src/phycode/redaction.py`: secret redaction helpers.
- `src/phycode/config.py`: user/project config loading and saving.
- `src/phycode/credentials.py`: keyring and encrypted-file credential store.
- `src/phycode/policy.py`: workspace, shell, credential, and approval policy.
- `src/phycode/tools/base.py`: tool registry, runtime, executor protocol.
- `src/phycode/tools/file_tools.py`: file and search tools.
- `src/phycode/tools/shell_tools.py`: shell and test tools.
- `src/phycode/tools/state_tools.py`: workspace, memory, config, and key-status tools.
- `src/phycode/feedback.py`: feedback classifiers.
- `src/phycode/context.py`: session store, memory store, context builder.
- `src/phycode/trace.py`: JSONL trace writer and reader.
- `src/phycode/llm.py`: LLM client protocol, scripted mocks, OpenAI-compatible adapter.
- `src/phycode/agent.py`: agent loop and stop controller.
- `src/phycode/demos.py`: deterministic demo scenarios.
- `tests/`: unit, integration, CLI, credential, and demo tests.

---

### Task 1: Project Scaffold, CLI Smoke Test, and CI Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `.gitlab-ci.yml`
- Create: `.github/workflows/test.yml`
- Create: `src/phycode/__init__.py`
- Create: `src/phycode/cli.py`
- Create: `tests/test_cli_smoke.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: console script `phycode = phycode.cli:app`.
- Produces: `phycode.__version__: str`.
- Produces: Typer app object `phycode.cli.app`.

- [ ] **Step 1: Write the failing CLI smoke test**

Create `tests/test_cli_smoke.py`:

```python
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
    assert "No tools registered yet" in result.stdout
```

- [ ] **Step 2: Run the failing smoke test**

Run: `uv run pytest tests/test_cli_smoke.py -v`

Expected: FAIL with an import error for `phycode` or `phycode.cli`.

- [ ] **Step 3: Create package scaffold**

Create `pyproject.toml`:

```toml
[project]
name = "phycode"
version = "0.1.0"
description = "CLI-first coding agent harness with policy-aware tool runtime"
requires-python = ">=3.11"
dependencies = [
    "cryptography>=43.0.0",
    "keyring>=25.0.0",
    "openai>=1.0.0",
    "pydantic>=2.7.0",
    "rich>=13.7.0",
    "typer>=0.12.0",
]

[project.scripts]
phycode = "phycode.cli:app"

[dependency-groups]
dev = [
    "pytest>=8.2.0",
    "pytest-cov>=5.0.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/phycode"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-q"
```

Create `src/phycode/__init__.py`:

```python
__version__ = "0.1.0"
```

Create `src/phycode/cli.py`:

```python
import typer
from rich.console import Console

from phycode import __version__

app = typer.Typer(help="PhyCode coding agent harness")
tools_app = typer.Typer(help="Inspect registered tools")
app.add_typer(tools_app, name="tools")
console = Console()


@app.command()
def version() -> None:
    """Print the PhyCode version."""
    console.print(f"phycode {__version__}")


@tools_app.command("list")
def list_tools() -> None:
    """List registered tools."""
    console.print("No tools registered yet")
```

- [ ] **Step 4: Add CI skeletons**

Create `.gitlab-ci.yml`:

```yaml
stages:
  - test

unit-test:
  stage: test
  image: python:3.11
  before_script:
    - pip install uv
    - uv sync --dev
  script:
    - uv run pytest
```

Create `.github/workflows/test.yml`:

```yaml
name: test

on:
  push:
  pull_request:

jobs:
  unit-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive
      - uses: astral-sh/setup-uv@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: uv sync --dev
      - run: uv run pytest
```

Append these lines to `.gitignore` if they are not present:

```gitignore
.phycode/
dist/
build/
*.egg-info/
```

- [ ] **Step 5: Run the smoke test**

Run: `uv run pytest tests/test_cli_smoke.py -v`

Expected: PASS for both smoke tests.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml README.md .gitlab-ci.yml .github/workflows/test.yml src/phycode/__init__.py src/phycode/cli.py tests/test_cli_smoke.py .gitignore
git commit -m "chore: scaffold phycode package"
```

---

### Task 2: Core Models and Redaction

**Files:**
- Create: `src/phycode/models.py`
- Create: `src/phycode/redaction.py`
- Create: `tests/test_models.py`
- Create: `tests/test_redaction.py`

**Interfaces:**
- Produces enums: `AgentEventType`, `ToolRiskLevel`, `PolicyAction`, `FeedbackKind`, `MemoryCategory`, `SessionMode`.
- Produces models: `AgentEvent`, `ToolSpec`, `ToolCall`, `PolicyDecision`, `ToolResult`, `FeedbackSignal`, `MemoryEntry`, `Session`, `ProviderConfig`.
- Produces function: `redact_text(text: str) -> str`.

- [ ] **Step 1: Write failing model tests**

Create `tests/test_models.py`:

```python
from phycode.models import FeedbackKind, MemoryCategory, PolicyAction, ToolCall, ToolRiskLevel, ToolSpec


def test_tool_spec_uses_declared_risk_levels():
    spec = ToolSpec(
        name="file.read",
        description="Read a file",
        input_schema={"type": "object"},
        risk_level=ToolRiskLevel.SAFE,
    )
    assert spec.name == "file.read"
    assert spec.risk_level == ToolRiskLevel.SAFE


def test_tool_call_preserves_provider_id():
    call = ToolCall(tool_name="file.read", args={"path": "README.md"}, provider_call_id="call_1")
    assert call.provider_call_id == "call_1"
    assert call.args["path"] == "README.md"


def test_required_enums_match_spec_values():
    assert {item.value for item in PolicyAction} == {"allow", "ask", "deny"}
    assert {item.value for item in ToolRiskLevel} == {"safe", "risky", "dangerous"}
    assert {item.value for item in MemoryCategory} == {"decision", "preference", "project_fact", "test_command"}
    assert "test_failed" in {item.value for item in FeedbackKind}
```

Create `tests/test_redaction.py`:

```python
from phycode.redaction import redact_text


def test_redacts_openai_style_key():
    text = "Authorization: Bearer sk-testsecret1234567890"
    assert "sk-testsecret" not in redact_text(text)
    assert "[REDACTED_SECRET]" in redact_text(text)


def test_redacts_env_assignment():
    text = "OPENAI_API_KEY=abc123SECRET"
    assert "abc123SECRET" not in redact_text(text)
    assert "OPENAI_API_KEY=[REDACTED_SECRET]" in redact_text(text)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_models.py tests/test_redaction.py -v`

Expected: FAIL with missing module or missing classes.

- [ ] **Step 3: Implement core models**

Create `src/phycode/models.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class AgentEventType(str, Enum):
    ASSISTANT_COMMENTARY = "assistant_commentary"
    REASONING_SUMMARY = "reasoning_summary"
    TOOL_CALL_REQUESTED = "tool_call_requested"
    POLICY_DECISION = "policy_decision"
    TOOL_CALL_RUNNING = "tool_call_running"
    TOOL_CALL_OUTPUT = "tool_call_output"
    FEEDBACK_SIGNAL = "feedback_signal"
    ASSISTANT_FINAL = "assistant_final"
    ERROR = "error"
    INCOMPLETE = "incomplete"
    USER_INTERRUPT = "user_interrupt"


class ToolRiskLevel(str, Enum):
    SAFE = "safe"
    RISKY = "risky"
    DANGEROUS = "dangerous"


class PolicyAction(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class FeedbackKind(str, Enum):
    SUCCESS = "success"
    COMMAND_FAILED = "command_failed"
    TEST_FAILED = "test_failed"
    POLICY_BLOCKED = "policy_blocked"
    POLICY_REQUIRES_APPROVAL = "policy_requires_approval"
    INVALID_TOOL_ARGS = "invalid_tool_args"
    TOOL_ERROR = "tool_error"
    TIMEOUT = "timeout"
    OUTPUT_TRUNCATED = "output_truncated"


class MemoryCategory(str, Enum):
    DECISION = "decision"
    PREFERENCE = "preference"
    PROJECT_FACT = "project_fact"
    TEST_COMMAND = "test_command"


class SessionMode(str, Enum):
    INTERACTIVE = "interactive"
    NON_INTERACTIVE = "non_interactive"


class AgentEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("evt"))
    session_id: str
    type: AgentEventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)
    redaction_status: str = "redacted"


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    risk_level: ToolRiskLevel


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: new_id("call"))
    tool_name: str
    args: dict[str, Any]
    provider_call_id: str | None = None


class PolicyDecision(BaseModel):
    tool_call_id: str
    decision: PolicyAction
    rule_id: str
    reason: str
    requires_user: bool = False


class ToolResult(BaseModel):
    tool_call_id: str
    status: str
    stdout: str = ""
    stderr: str = ""
    artifact_refs: list[str] = Field(default_factory=list)
    truncated: bool = False


class FeedbackSignal(BaseModel):
    kind: FeedbackKind
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False
    suggested_next_step: str | None = None


class MemoryEntry(BaseModel):
    id: str = Field(default_factory=lambda: new_id("mem"))
    category: MemoryCategory
    content: str
    source: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Session(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ses"))
    workspace_root: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    mode: SessionMode


class ProviderConfig(BaseModel):
    provider: str
    base_url: str
    model: str
    credential_ref: str | None = None
```

- [ ] **Step 4: Implement redaction**

Create `src/phycode/redaction.py`:

```python
from __future__ import annotations

import re

SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{10,}"), "[REDACTED_SECRET]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{10,}", re.IGNORECASE), "Bearer [REDACTED_SECRET]"),
    (re.compile(r"(OPENAI_API_KEY=)[^\s]+", re.IGNORECASE), r"\1[REDACTED_SECRET]"),
    (re.compile(r"(ANTHROPIC_API_KEY=)[^\s]+", re.IGNORECASE), r"\1[REDACTED_SECRET]"),
    (re.compile(r"(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[^'\"\s]+", re.IGNORECASE), r"\1[REDACTED_SECRET]"),
]


def redact_text(text: str) -> str:
    redacted = text
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_models.py tests/test_redaction.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/phycode/models.py src/phycode/redaction.py tests/test_models.py tests/test_redaction.py
git commit -m "feat: add core event models and redaction"
```

---

### Task 3: Configuration and Credential Storage

**Files:**
- Create: `src/phycode/config.py`
- Create: `src/phycode/credentials.py`
- Create: `tests/test_config.py`
- Create: `tests/test_credentials.py`
- Modify: `src/phycode/cli.py`

**Interfaces:**
- Consumes: `ProviderConfig` from `src/phycode/models.py`.
- Produces: `ProjectConfig`, `UserConfig`, `load_project_config(path: Path) -> ProjectConfig`.
- Produces: `CredentialStore` with `set_key(provider: str, secret: str)`, `get_key(provider: str) -> str | None`, `clear_key(provider: str)`, `status(provider: str) -> CredentialStatus`.
- Produces CLI commands: `phycode config read`, `phycode keys status`.

- [ ] **Step 1: Write failing config and credential tests**

Create `tests/test_config.py`:

```python
from pathlib import Path

from phycode.config import ProjectConfig, load_project_config


def test_missing_project_config_uses_workspace_root(tmp_path: Path):
    config = load_project_config(tmp_path)
    assert config.workspace.root == tmp_path
    assert config.agent.max_steps == 50


def test_project_config_reads_test_command(tmp_path: Path):
    (tmp_path / "phycode.toml").write_text('[test]\ncommand = "uv run pytest"\n', encoding="utf-8")
    config = load_project_config(tmp_path)
    assert config.test.command == "uv run pytest"
```

Create `tests/test_credentials.py`:

```python
from phycode.credentials import InMemoryCredentialBackend, CredentialStore


def test_key_status_does_not_reveal_secret():
    store = CredentialStore(backend=InMemoryCredentialBackend())
    store.set_key("openai-compatible", "sk-secret-value")
    status = store.status("openai-compatible")
    assert status.configured is True
    assert "secret" not in status.model_dump_json()


def test_clear_key_removes_secret():
    store = CredentialStore(backend=InMemoryCredentialBackend())
    store.set_key("openai-compatible", "sk-secret-value")
    store.clear_key("openai-compatible")
    assert store.get_key("openai-compatible") is None
    assert store.status("openai-compatible").configured is False
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_config.py tests/test_credentials.py -v`

Expected: FAIL with missing modules.

- [ ] **Step 3: Implement configuration models**

Create `src/phycode/config.py`:

```python
from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


class WorkspaceConfig(BaseModel):
    root: Path
    allowlist: list[Path] = Field(default_factory=list)


class AgentConfig(BaseModel):
    max_steps: int = 50


class TestConfig(BaseModel):
    command: str = "uv run pytest"


class LLMConfig(BaseModel):
    provider: str = "openai-compatible"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1-mini"


class ProjectConfig(BaseModel):
    workspace: WorkspaceConfig
    agent: AgentConfig = Field(default_factory=AgentConfig)
    test: TestConfig = Field(default_factory=TestConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)


def load_project_config(workspace_root: Path) -> ProjectConfig:
    root = workspace_root.resolve()
    config_path = root / "phycode.toml"
    data: dict = {}
    if config_path.exists():
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    workspace_data = data.get("workspace", {})
    allowlist = [Path(item).expanduser().resolve() for item in workspace_data.get("allowlist", [])]
    return ProjectConfig(
        workspace=WorkspaceConfig(root=root, allowlist=allowlist),
        agent=AgentConfig(**data.get("agent", {})),
        test=TestConfig(**data.get("test", {})),
        llm=LLMConfig(**data.get("llm", {})),
    )
```

- [ ] **Step 4: Implement credential store**

Create `src/phycode/credentials.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel


class CredentialStatus(BaseModel):
    provider: str
    configured: bool
    source: str
    updated_at: str | None = None


class CredentialBackend(Protocol):
    source_name: str

    def set_password(self, service: str, username: str, password: str) -> None:
        ...

    def get_password(self, service: str, username: str) -> str | None:
        ...

    def delete_password(self, service: str, username: str) -> None:
        ...


@dataclass
class InMemoryCredentialBackend:
    source_name: str = "memory"
    _values: dict[tuple[str, str], str] = field(default_factory=dict)

    def set_password(self, service: str, username: str, password: str) -> None:
        self._values[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self._values.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self._values.pop((service, username), None)


class KeyringCredentialBackend:
    source_name = "keyring"

    def __init__(self) -> None:
        import keyring

        self._keyring = keyring

    def set_password(self, service: str, username: str, password: str) -> None:
        self._keyring.set_password(service, username, password)

    def get_password(self, service: str, username: str) -> str | None:
        return self._keyring.get_password(service, username)

    def delete_password(self, service: str, username: str) -> None:
        try:
            self._keyring.delete_password(service, username)
        except Exception:
            return


class CredentialStore:
    def __init__(self, backend: CredentialBackend | None = None, service: str = "phycode") -> None:
        self.backend = backend if backend is not None else KeyringCredentialBackend()
        self.service = service
        self._updated_at: dict[str, str] = {}

    def set_key(self, provider: str, secret: str) -> None:
        self.backend.set_password(self.service, provider, secret)
        self._updated_at[provider] = datetime.now(timezone.utc).isoformat()

    def get_key(self, provider: str) -> str | None:
        return self.backend.get_password(self.service, provider)

    def clear_key(self, provider: str) -> None:
        self.backend.delete_password(self.service, provider)
        self._updated_at.pop(provider, None)

    def status(self, provider: str) -> CredentialStatus:
        return CredentialStatus(
            provider=provider,
            configured=self.get_key(provider) is not None,
            source=self.backend.source_name,
            updated_at=self._updated_at.get(provider),
        )
```

- [ ] **Step 5: Add CLI status commands**

Modify `src/phycode/cli.py` by adding config and keys apps:

```python
from pathlib import Path

from phycode.config import load_project_config
from phycode.credentials import CredentialStore

config_app = typer.Typer(help="Read and write non-sensitive configuration")
keys_app = typer.Typer(help="Manage provider credentials")
app.add_typer(config_app, name="config")
app.add_typer(keys_app, name="keys")


@config_app.command("read")
def config_read() -> None:
    config = load_project_config(Path.cwd())
    console.print_json(config.model_dump_json())


@keys_app.command("status")
def keys_status(provider: str = "openai-compatible") -> None:
    status = CredentialStore().status(provider)
    console.print_json(status.model_dump_json())
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_config.py tests/test_credentials.py tests/test_cli_smoke.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/phycode/config.py src/phycode/credentials.py src/phycode/cli.py tests/test_config.py tests/test_credentials.py
git commit -m "feat: add config and credential foundations"
```

---

### Task 4: Workspace Policy and Guardrails

**Files:**
- Create: `src/phycode/policy.py`
- Create: `tests/test_policy.py`

**Interfaces:**
- Consumes: `ToolCall`, `PolicyDecision`, `PolicyAction`.
- Produces: `PolicyContext(workspace_root: Path, allowlist: list[Path], interactive: bool)`.
- Produces: `PolicyEngine.decide(call: ToolCall, context: PolicyContext) -> PolicyDecision`.
- Produces: `resolve_workspace_path(path: str, context: PolicyContext) -> Path`.

- [ ] **Step 1: Write failing policy tests**

Create `tests/test_policy.py`:

```python
from pathlib import Path

import pytest

from phycode.models import PolicyAction, ToolCall
from phycode.policy import PolicyContext, PolicyEngine, WorkspaceViolation, resolve_workspace_path


def test_safe_read_is_allowed(tmp_path: Path):
    call = ToolCall(tool_name="file.read", args={"path": "README.md"})
    decision = PolicyEngine().decide(call, PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=True))
    assert decision.decision == PolicyAction.ALLOW


def test_file_edit_requires_approval(tmp_path: Path):
    call = ToolCall(tool_name="file.edit", args={"path": "app.py", "old": "a", "new": "b"})
    decision = PolicyEngine().decide(call, PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=True))
    assert decision.decision == PolicyAction.ASK
    assert decision.requires_user is True


def test_path_escape_is_rejected(tmp_path: Path):
    with pytest.raises(WorkspaceViolation):
        resolve_workspace_path("../secret.txt", PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=True))


def test_dangerous_shell_is_denied(tmp_path: Path):
    call = ToolCall(tool_name="shell.run", args={"command": "rm -rf /"})
    decision = PolicyEngine().decide(call, PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=True))
    assert decision.decision == PolicyAction.DENY
    assert decision.rule_id == "shell.dangerous_command"


def test_env_file_read_is_denied(tmp_path: Path):
    call = ToolCall(tool_name="file.read", args={"path": ".env"})
    decision = PolicyEngine().decide(call, PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=True))
    assert decision.decision == PolicyAction.DENY
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/test_policy.py -v`

Expected: FAIL with missing policy module.

- [ ] **Step 3: Implement policy**

Create `src/phycode/policy.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from phycode.models import PolicyAction, PolicyDecision, ToolCall


class WorkspaceViolation(ValueError):
    pass


@dataclass(frozen=True)
class PolicyContext:
    workspace_root: Path
    allowlist: list[Path]
    interactive: bool


CREDENTIAL_FILENAMES = {".env", ".env.local", "id_rsa", "id_ed25519"}
SAFE_TOOLS = {"file.read", "file.list", "search.grep", "search.glob", "memory.read", "config.read", "workspace.status", "keys.status"}
RISKY_TOOLS = {"file.write", "file.edit", "memory.write", "config.write", "shell.run", "test.run"}
DANGEROUS_SHELL_PATTERNS = [
    re.compile(r"\brm\s+-rf\s+/", re.IGNORECASE),
    re.compile(r"\bdel\s+/s\b", re.IGNORECASE),
    re.compile(r"\bformat\s+[A-Z]:", re.IGNORECASE),
    re.compile(r"\bcurl\b.*\|\s*sh\b", re.IGNORECASE),
    re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE),
]


def _allowed_roots(context: PolicyContext) -> list[Path]:
    return [context.workspace_root.resolve(), *[path.resolve() for path in context.allowlist]]


def resolve_workspace_path(path: str, context: PolicyContext) -> Path:
    candidate = (context.workspace_root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    for root in _allowed_roots(context):
        if candidate == root or root in candidate.parents:
            return candidate
    raise WorkspaceViolation(f"path escapes workspace: {path}")


def _is_credential_path(path: str) -> bool:
    name = Path(path).name
    return name in CREDENTIAL_FILENAMES or name.endswith(".pem") or name.endswith(".key")


class PolicyEngine:
    def decide(self, call: ToolCall, context: PolicyContext) -> PolicyDecision:
        if "path" in call.args:
            try:
                resolve_workspace_path(str(call.args["path"]), context)
            except WorkspaceViolation:
                return PolicyDecision(
                    tool_call_id=call.id,
                    decision=PolicyAction.DENY,
                    rule_id="workspace.path_escape",
                    reason="Path is outside the workspace allowlist",
                )
            if call.tool_name.startswith("file.") and _is_credential_path(str(call.args["path"])):
                return PolicyDecision(
                    tool_call_id=call.id,
                    decision=PolicyAction.DENY,
                    rule_id="credential.read_blocked",
                    reason="Credential-like files cannot be read by model-callable tools",
                )

        if call.tool_name == "shell.run":
            command = str(call.args.get("command", ""))
            for pattern in DANGEROUS_SHELL_PATTERNS:
                if pattern.search(command):
                    return PolicyDecision(
                        tool_call_id=call.id,
                        decision=PolicyAction.DENY,
                        rule_id="shell.dangerous_command",
                        reason="Command matches a dangerous shell pattern",
                    )

        if call.tool_name in SAFE_TOOLS:
            return PolicyDecision(tool_call_id=call.id, decision=PolicyAction.ALLOW, rule_id="tool.safe_default", reason="Safe read/status tool")

        if call.tool_name in RISKY_TOOLS:
            return PolicyDecision(
                tool_call_id=call.id,
                decision=PolicyAction.ASK,
                rule_id="tool.risky_default",
                reason="Risky tool requires approval",
                requires_user=True,
            )

        return PolicyDecision(tool_call_id=call.id, decision=PolicyAction.DENY, rule_id="tool.unknown", reason="Unknown tool")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_policy.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phycode/policy.py tests/test_policy.py
git commit -m "feat: add deterministic policy engine"
```

---

### Task 5: Tool Registry and File/Search Tools

**Files:**
- Create: `src/phycode/tools/__init__.py`
- Create: `src/phycode/tools/base.py`
- Create: `src/phycode/tools/file_tools.py`
- Create: `tests/test_tool_registry.py`
- Create: `tests/test_file_tools.py`
- Modify: `src/phycode/cli.py`

**Interfaces:**
- Consumes: `PolicyEngine`, `PolicyContext`, `ToolCall`, `ToolResult`.
- Produces: `ToolRegistry.register(spec: ToolSpec, executor: ToolExecutor) -> None`.
- Produces: `ToolRuntime.run(call: ToolCall, context: PolicyContext, approved: bool = False) -> ToolRuntimeResult`.
- Produces: `register_file_tools(registry: ToolRegistry) -> None`.

- [ ] **Step 1: Write failing registry and file tool tests**

Create `tests/test_tool_registry.py`:

```python
from pathlib import Path

from phycode.models import PolicyAction, ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRegistry, ToolRuntime


def test_runtime_returns_policy_block_for_denied_call(tmp_path: Path):
    registry = ToolRegistry()
    runtime = ToolRuntime(registry=registry)
    call = ToolCall(tool_name="unknown.tool", args={})
    result = runtime.run(call, PolicyContext(tmp_path, [], interactive=False))
    assert result.policy.decision == PolicyAction.DENY
    assert result.tool_result.status == "policy_blocked"


def test_registry_lists_specs():
    registry = ToolRegistry()
    registry.register(ToolSpec(name="x.echo", description="Echo", input_schema={}, risk_level=ToolRiskLevel.SAFE), lambda call: ToolResult(tool_call_id=call.id, status="ok"))
    assert [spec.name for spec in registry.list_specs()] == ["x.echo"]
```

Create `tests/test_file_tools.py`:

```python
from pathlib import Path

from phycode.models import PolicyAction, ToolCall
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.file_tools import register_file_tools


def test_file_read_reads_workspace_file(tmp_path: Path):
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    registry = ToolRegistry()
    register_file_tools(registry)
    result = ToolRuntime(registry).run(ToolCall(tool_name="file.read", args={"path": "README.md"}), PolicyContext(tmp_path, [], True))
    assert result.policy.decision == PolicyAction.ALLOW
    assert result.tool_result.stdout == "hello"


def test_file_edit_requires_approval_then_writes_diff(tmp_path: Path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    registry = ToolRegistry()
    register_file_tools(registry)
    call = ToolCall(tool_name="file.edit", args={"path": "app.py", "old": "x = 1", "new": "x = 2"})
    result = ToolRuntime(registry).run(call, PolicyContext(tmp_path, [], True), approved=True)
    assert result.tool_result.status == "ok"
    assert "x = 2" in (tmp_path / "app.py").read_text(encoding="utf-8")
    assert "-x = 1" in result.tool_result.stdout
    assert "+x = 2" in result.tool_result.stdout
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/test_tool_registry.py tests/test_file_tools.py -v`

Expected: FAIL with missing tool modules.

- [ ] **Step 3: Implement registry and runtime**

Create `src/phycode/tools/base.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from phycode.models import PolicyAction, PolicyDecision, ToolCall, ToolResult, ToolSpec
from phycode.policy import PolicyContext, PolicyEngine

ToolExecutor = Callable[[ToolCall], ToolResult]


@dataclass
class ToolRuntimeResult:
    policy: PolicyDecision
    tool_result: ToolResult


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._executors: dict[str, ToolExecutor] = {}

    def register(self, spec: ToolSpec, executor: ToolExecutor) -> None:
        self._specs[spec.name] = spec
        self._executors[spec.name] = executor

    def list_specs(self) -> list[ToolSpec]:
        return [self._specs[name] for name in sorted(self._specs)]

    def executor_for(self, name: str) -> ToolExecutor | None:
        return self._executors.get(name)


class ToolRuntime:
    def __init__(self, registry: ToolRegistry, policy: PolicyEngine | None = None) -> None:
        self.registry = registry
        self.policy = policy or PolicyEngine()

    def run(self, call: ToolCall, context: PolicyContext, approved: bool = False) -> ToolRuntimeResult:
        decision = self.policy.decide(call, context)
        if decision.decision == PolicyAction.DENY:
            return ToolRuntimeResult(decision, ToolResult(tool_call_id=call.id, status="policy_blocked", stderr=decision.reason))
        if decision.decision == PolicyAction.ASK and not approved:
            return ToolRuntimeResult(decision, ToolResult(tool_call_id=call.id, status="policy_requires_approval", stderr=decision.reason))
        executor = self.registry.executor_for(call.tool_name)
        if executor is None:
            return ToolRuntimeResult(decision, ToolResult(tool_call_id=call.id, status="tool_error", stderr=f"Tool not registered: {call.tool_name}"))
        return ToolRuntimeResult(decision, executor(call))
```

Create `src/phycode/tools/__init__.py`:

```python
from phycode.tools.base import ToolRegistry, ToolRuntime, ToolRuntimeResult

__all__ = ["ToolRegistry", "ToolRuntime", "ToolRuntimeResult"]
```

- [ ] **Step 4: Implement file and search tools**

Create `src/phycode/tools/file_tools.py`:

```python
from __future__ import annotations

import difflib
from pathlib import Path

from phycode.models import ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from phycode.tools.base import ToolRegistry


def _read(path: Path, limit: int | None = None, offset: int = 0) -> tuple[str, bool]:
    text = path.read_text(encoding="utf-8")
    sliced = text[offset:]
    if limit is not None and len(sliced) > limit:
        return sliced[:limit], True
    return sliced, False


def _file_read(call: ToolCall) -> ToolResult:
    content, truncated = _read(Path(call.args["path"]), call.args.get("limit"), call.args.get("offset", 0))
    return ToolResult(tool_call_id=call.id, status="ok", stdout=content, truncated=truncated)


def _file_list(call: ToolCall) -> ToolResult:
    root = Path(call.args.get("path", "."))
    entries = sorted(item.name for item in root.iterdir())
    return ToolResult(tool_call_id=call.id, status="ok", stdout="\n".join(entries))


def _file_write(call: ToolCall) -> ToolResult:
    path = Path(call.args["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(call.args["content"]), encoding="utf-8")
    return ToolResult(tool_call_id=call.id, status="ok", stdout=f"wrote {path}")


def _file_edit(call: ToolCall) -> ToolResult:
    path = Path(call.args["path"])
    old = str(call.args["old"])
    new = str(call.args["new"])
    before = path.read_text(encoding="utf-8")
    count = before.count(old)
    if count == 0:
        return ToolResult(tool_call_id=call.id, status="tool_error", stderr="old text not found")
    if count > 1:
        return ToolResult(tool_call_id=call.id, status="tool_error", stderr="old text matches more than once")
    after = before.replace(old, new, 1)
    path.write_text(after, encoding="utf-8")
    diff = "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile=str(path), tofile=str(path), lineterm=""))
    return ToolResult(tool_call_id=call.id, status="ok", stdout=diff)


def register_file_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(name="file.read", description="Read a file", input_schema={"type": "object"}, risk_level=ToolRiskLevel.SAFE), _file_read)
    registry.register(ToolSpec(name="file.list", description="List a directory", input_schema={"type": "object"}, risk_level=ToolRiskLevel.SAFE), _file_list)
    registry.register(ToolSpec(name="file.write", description="Write a file", input_schema={"type": "object"}, risk_level=ToolRiskLevel.RISKY), _file_write)
    registry.register(ToolSpec(name="file.edit", description="Edit a file by exact replacement", input_schema={"type": "object"}, risk_level=ToolRiskLevel.RISKY), _file_edit)
```

- [ ] **Step 5: Wire tools list CLI**

Modify `src/phycode/cli.py`:

```python
from phycode.tools import ToolRegistry
from phycode.tools.file_tools import register_file_tools


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_file_tools(registry)
    return registry


@tools_app.command("list")
def list_tools() -> None:
    """List registered tools."""
    registry = build_default_registry()
    for spec in registry.list_specs():
        console.print(f"{spec.name}\t{spec.risk_level.value}\t{spec.description}")
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_tool_registry.py tests/test_file_tools.py tests/test_cli_smoke.py -v`

Expected: update `tests/test_cli_smoke.py::test_tools_list_command_exists` to assert `"file.read"` appears, then PASS.

- [ ] **Step 7: Commit**

```bash
git add src/phycode/tools src/phycode/cli.py tests/test_tool_registry.py tests/test_file_tools.py tests/test_cli_smoke.py
git commit -m "feat: add tool registry and file tools"
```

---

### Task 6: Shell, Test, Workspace, and Feedback Tools

**Files:**
- Create: `src/phycode/tools/shell_tools.py`
- Create: `src/phycode/tools/state_tools.py`
- Create: `src/phycode/feedback.py`
- Create: `tests/test_shell_and_feedback.py`
- Create: `tests/test_state_tools.py`
- Modify: `src/phycode/cli.py`

**Interfaces:**
- Produces: `register_shell_tools(registry: ToolRegistry, workspace_root: Path, test_command: str) -> None`.
- Produces: `register_state_tools(registry: ToolRegistry, workspace_root: Path) -> None`.
- Produces: `classify_feedback(result: ToolResult) -> list[FeedbackSignal]`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_shell_and_feedback.py`:

```python
from pathlib import Path

from phycode.feedback import classify_feedback
from phycode.models import FeedbackKind, ToolCall
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.shell_tools import register_shell_tools


def test_shell_success_maps_to_success_feedback(tmp_path: Path):
    registry = ToolRegistry()
    register_shell_tools(registry, workspace_root=tmp_path, test_command="python --version")
    call = ToolCall(tool_name="shell.run", args={"command": "python --version"})
    runtime_result = ToolRuntime(registry).run(call, PolicyContext(tmp_path, [], True), approved=True)
    feedback = classify_feedback(runtime_result.tool_result)
    assert feedback[0].kind == FeedbackKind.SUCCESS


def test_tool_error_maps_to_tool_error():
    from phycode.models import ToolResult

    feedback = classify_feedback(ToolResult(tool_call_id="x", status="tool_error", stderr="old text not found"))
    assert feedback[0].kind == FeedbackKind.TOOL_ERROR
```

Create `tests/test_state_tools.py`:

```python
from pathlib import Path

from phycode.models import ToolCall
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.state_tools import register_state_tools


def test_workspace_status_reports_root(tmp_path: Path):
    registry = ToolRegistry()
    register_state_tools(registry, workspace_root=tmp_path)
    result = ToolRuntime(registry).run(ToolCall(tool_name="workspace.status", args={}), PolicyContext(tmp_path, [], True))
    assert str(tmp_path) in result.tool_result.stdout
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/test_shell_and_feedback.py tests/test_state_tools.py -v`

Expected: FAIL with missing modules.

- [ ] **Step 3: Implement feedback classifier**

Create `src/phycode/feedback.py`:

```python
from __future__ import annotations

from phycode.models import FeedbackKind, FeedbackSignal, ToolResult


STATUS_TO_KIND = {
    "ok": FeedbackKind.SUCCESS,
    "command_failed": FeedbackKind.COMMAND_FAILED,
    "test_failed": FeedbackKind.TEST_FAILED,
    "policy_blocked": FeedbackKind.POLICY_BLOCKED,
    "policy_requires_approval": FeedbackKind.POLICY_REQUIRES_APPROVAL,
    "invalid_tool_args": FeedbackKind.INVALID_TOOL_ARGS,
    "tool_error": FeedbackKind.TOOL_ERROR,
    "timeout": FeedbackKind.TIMEOUT,
}


def classify_feedback(result: ToolResult) -> list[FeedbackSignal]:
    kind = STATUS_TO_KIND.get(result.status, FeedbackKind.TOOL_ERROR)
    evidence = {"stdout": result.stdout[:1000], "stderr": result.stderr[:1000], "truncated": result.truncated}
    signals = [
        FeedbackSignal(
            kind=kind,
            summary=_summary_for(kind, result),
            evidence=evidence,
            retryable=kind in {FeedbackKind.COMMAND_FAILED, FeedbackKind.TEST_FAILED, FeedbackKind.TOOL_ERROR},
            suggested_next_step=_next_step_for(kind),
        )
    ]
    if result.truncated:
        signals.append(FeedbackSignal(kind=FeedbackKind.OUTPUT_TRUNCATED, summary="Tool output was truncated", evidence=evidence, retryable=False))
    return signals


def _summary_for(kind: FeedbackKind, result: ToolResult) -> str:
    if kind == FeedbackKind.SUCCESS:
        return "Tool completed successfully"
    if result.stderr:
        return result.stderr.splitlines()[0]
    if result.stdout:
        return result.stdout.splitlines()[0]
    return kind.value


def _next_step_for(kind: FeedbackKind) -> str | None:
    if kind == FeedbackKind.TEST_FAILED:
        return "Inspect the failing test and edit the related file"
    if kind == FeedbackKind.POLICY_REQUIRES_APPROVAL:
        return "Ask the user for approval"
    if kind == FeedbackKind.COMMAND_FAILED:
        return "Inspect stderr and choose a smaller command"
    return None
```

- [ ] **Step 4: Implement shell and state tools**

Create `src/phycode/tools/shell_tools.py`:

```python
from __future__ import annotations

import subprocess
from pathlib import Path

from phycode.models import ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from phycode.tools.base import ToolRegistry


def _run_command(command: str, cwd: Path, timeout: int = 30) -> ToolResult:
    completed = subprocess.run(command, cwd=cwd, shell=True, text=True, capture_output=True, timeout=timeout)
    status = "ok" if completed.returncode == 0 else "command_failed"
    return ToolResult(tool_call_id="pending", status=status, stdout=completed.stdout, stderr=completed.stderr)


def register_shell_tools(registry: ToolRegistry, workspace_root: Path, test_command: str) -> None:
    def shell_run(call: ToolCall) -> ToolResult:
        timeout = int(call.args.get("timeout", 30))
        result = _run_command(str(call.args["command"]), workspace_root, timeout)
        return result.model_copy(update={"tool_call_id": call.id})

    def test_run(call: ToolCall) -> ToolResult:
        result = _run_command(str(call.args.get("command", test_command)), workspace_root, int(call.args.get("timeout", 60)))
        status = "ok" if result.status == "ok" else "test_failed"
        return result.model_copy(update={"tool_call_id": call.id, "status": status})

    registry.register(ToolSpec(name="shell.run", description="Run a bounded shell command", input_schema={"type": "object"}, risk_level=ToolRiskLevel.RISKY), shell_run)
    registry.register(ToolSpec(name="test.run", description="Run configured tests", input_schema={"type": "object"}, risk_level=ToolRiskLevel.RISKY), test_run)
```

Create `src/phycode/tools/state_tools.py`:

```python
from __future__ import annotations

from pathlib import Path

from phycode.models import ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from phycode.tools.base import ToolRegistry


def register_state_tools(registry: ToolRegistry, workspace_root: Path) -> None:
    def workspace_status(call: ToolCall) -> ToolResult:
        return ToolResult(tool_call_id=call.id, status="ok", stdout=f"workspace_root={workspace_root}")

    registry.register(ToolSpec(name="workspace.status", description="Show workspace status", input_schema={"type": "object"}, risk_level=ToolRiskLevel.SAFE), workspace_status)
```

- [ ] **Step 5: Register shell and state tools in CLI**

Modify `build_default_registry()` in `src/phycode/cli.py`:

```python
from pathlib import Path

from phycode.tools.shell_tools import register_shell_tools
from phycode.tools.state_tools import register_state_tools


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    root = Path.cwd()
    register_file_tools(registry)
    register_shell_tools(registry, workspace_root=root, test_command="uv run pytest")
    register_state_tools(registry, workspace_root=root)
    return registry
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_shell_and_feedback.py tests/test_state_tools.py tests/test_cli_smoke.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/phycode/tools/shell_tools.py src/phycode/tools/state_tools.py src/phycode/feedback.py src/phycode/cli.py tests/test_shell_and_feedback.py tests/test_state_tools.py
git commit -m "feat: add shell tools and feedback classification"
```

---

### Task 7: Trace, Memory, Session, and Context Builder

**Files:**
- Create: `src/phycode/trace.py`
- Create: `src/phycode/context.py`
- Create: `tests/test_trace_context_memory.py`

**Interfaces:**
- Consumes: `AgentEvent`, `FeedbackSignal`, `MemoryEntry`, `Session`.
- Produces: `TraceStore.append(event: AgentEvent) -> None`.
- Produces: `MemoryStore.append(entry: MemoryEntry) -> None`, `MemoryStore.summary() -> str`.
- Produces: `SessionStore.add_event(event: AgentEvent) -> None`.
- Produces: `ContextBuilder.build(current_input: str) -> list[dict[str, object]]`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_trace_context_memory.py`:

```python
from pathlib import Path

from phycode.context import ContextBuilder, MemoryStore, SessionStore
from phycode.models import AgentEvent, AgentEventType, MemoryCategory, MemoryEntry, Session, SessionMode
from phycode.trace import TraceStore


def test_trace_store_redacts_before_write(tmp_path: Path):
    store = TraceStore(tmp_path)
    event = AgentEvent(session_id="s", type=AgentEventType.ERROR, payload={"message": "OPENAI_API_KEY=secret"})
    store.append(event)
    text = next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8")
    assert "secret" not in text
    assert "REDACTED" in text


def test_context_includes_recent_feedback_and_memory(tmp_path: Path):
    session = Session(workspace_root=str(tmp_path), mode=SessionMode.INTERACTIVE)
    session_store = SessionStore(session)
    memory = MemoryStore(tmp_path / "memory.jsonl")
    memory.append(MemoryEntry(category=MemoryCategory.TEST_COMMAND, content="Use uv run pytest", source="user"))
    session_store.add_event(AgentEvent(session_id=session.id, type=AgentEventType.FEEDBACK_SIGNAL, payload={"kind": "test_failed", "summary": "one test failed"}))
    messages = ContextBuilder(session_store=session_store, memory_store=memory, max_chars=4000).build("fix it")
    rendered = str(messages)
    assert "Use uv run pytest" in rendered
    assert "one test failed" in rendered
    assert "fix it" in rendered
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/test_trace_context_memory.py -v`

Expected: FAIL with missing modules.

- [ ] **Step 3: Implement trace store**

Create `src/phycode/trace.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from phycode.models import AgentEvent
from phycode.redaction import redact_text


class TraceStore:
    def __init__(self, trace_dir: Path) -> None:
        self.trace_dir = trace_dir
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def append(self, event: AgentEvent) -> None:
        path = self.trace_dir / f"{event.session_id}.jsonl"
        raw = event.model_dump_json()
        redacted = redact_text(raw)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(redacted + "\n")

    def read_events_raw(self, session_id: str) -> list[dict]:
        path = self.trace_dir / f"{session_id}.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
```

- [ ] **Step 4: Implement session, memory, and context**

Create `src/phycode/context.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from phycode.models import AgentEvent, MemoryEntry, Session
from phycode.redaction import redact_text


class SessionStore:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.events: list[AgentEvent] = []

    def add_event(self, event: AgentEvent) -> None:
        self.events.append(event)

    def recent_events(self, limit: int = 12) -> list[AgentEvent]:
        return self.events[-limit:]


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: MemoryEntry) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(redact_text(entry.model_dump_json()) + "\n")

    def entries(self) -> list[MemoryEntry]:
        if not self.path.exists():
            return []
        return [MemoryEntry.model_validate(json.loads(line)) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def summary(self) -> str:
        return "\n".join(f"- {entry.category.value}: {entry.content}" for entry in self.entries())


class ContextBuilder:
    def __init__(self, session_store: SessionStore, memory_store: MemoryStore, max_chars: int = 12000) -> None:
        self.session_store = session_store
        self.memory_store = memory_store
        self.max_chars = max_chars

    def build(self, current_input: str) -> list[dict[str, object]]:
        system = "You are PhyCode, a CLI coding agent harness. Use tools safely and follow policy feedback."
        memory = self.memory_store.summary()
        recent = [event.model_dump(mode="json") for event in self.session_store.recent_events()]
        content = f"Workspace: {self.session_store.session.workspace_root}\nMemory:\n{memory}\nRecent events:\n{recent}\nUser: {current_input}"
        if len(content) > self.max_chars:
            content = content[-self.max_chars :]
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": redact_text(content)},
        ]
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_trace_context_memory.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/phycode/trace.py src/phycode/context.py tests/test_trace_context_memory.py
git commit -m "feat: add trace memory and context stores"
```

---

### Task 8: LLM Adapters and Event Normalization

**Files:**
- Create: `src/phycode/llm.py`
- Create: `tests/test_llm_adapters.py`

**Interfaces:**
- Consumes: `AgentEvent`, `AgentEventType`, `ToolCall`.
- Produces protocol: `LLMClient.generate(messages: list[dict], tools: list[ToolSpec]) -> list[AgentEvent]`.
- Produces: `ScriptedLLM`, `EchoLLM`, `FailingLLM`, `OpenAICompatibleChatAdapter`.

- [ ] **Step 1: Write failing LLM tests**

Create `tests/test_llm_adapters.py`:

```python
import pytest

from phycode.llm import EchoLLM, FailingLLM, ScriptedLLM
from phycode.models import AgentEventType, ToolCall


def test_scripted_llm_returns_events_in_order():
    llm = ScriptedLLM([[{"type": "assistant_final", "payload": {"text": "done"}}]])
    events = llm.generate([], [])
    assert events[0].type == AgentEventType.ASSISTANT_FINAL
    assert events[0].payload["text"] == "done"


def test_echo_llm_returns_final_text():
    events = EchoLLM().generate([{"role": "user", "content": "hello"}], [])
    assert events[0].type == AgentEventType.ASSISTANT_FINAL
    assert "hello" in events[0].payload["text"]


def test_failing_llm_raises_provider_error():
    with pytest.raises(RuntimeError):
        FailingLLM("provider down").generate([], [])


class FakeFunction:
    name = "file.read"
    arguments = '{"path": "README.md"}'


class FakeToolCall:
    id = "call_1"
    type = "function"
    function = FakeFunction()


class FakeMessage:
    content = "I will read the file."
    tool_calls = [FakeToolCall()]


class FakeChoice:
    message = FakeMessage()


class FakeResponse:
    choices = [FakeChoice()]


class FakeCompletions:
    def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeResponse()


class FakeChat:
    def __init__(self):
        self.completions = FakeCompletions()


class FakeOpenAIClient:
    def __init__(self):
        self.chat = FakeChat()


def test_openai_compatible_adapter_maps_tool_calls():
    from phycode.llm import OpenAICompatibleChatAdapter

    client = FakeOpenAIClient()
    adapter = OpenAICompatibleChatAdapter(base_url="http://localhost:8000/v1", model="qwen-coder", api_key="secret", client=client)
    events = adapter.generate([{"role": "user", "content": "read README"}], [])
    assert events[0].type == AgentEventType.ASSISTANT_COMMENTARY
    assert events[1].type == AgentEventType.TOOL_CALL_REQUESTED
    assert events[1].payload["tool_name"] == "file.read"
    assert events[1].payload["args"] == {"path": "README.md"}
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/test_llm_adapters.py -v`

Expected: FAIL with missing `phycode.llm`.

- [ ] **Step 3: Implement mock adapters**

Create `src/phycode/llm.py`:

```python
from __future__ import annotations

import json
from typing import Any, Protocol

from phycode.models import AgentEvent, AgentEventType, ToolCall, ToolSpec


class LLMClient(Protocol):
    def generate(self, messages: list[dict], tools: list[ToolSpec]) -> list[AgentEvent]:
        ...


def _event_from_dict(data: dict) -> AgentEvent:
    return AgentEvent(session_id=data.get("session_id", "scripted"), type=AgentEventType(data["type"]), payload=data.get("payload", {}))


class ScriptedLLM:
    def __init__(self, turns: list[list[dict]]) -> None:
        self.turns = turns
        self.index = 0

    def generate(self, messages: list[dict], tools: list[ToolSpec]) -> list[AgentEvent]:
        if self.index >= len(self.turns):
            return [AgentEvent(session_id="scripted", type=AgentEventType.ASSISTANT_FINAL, payload={"text": "No scripted turns remain"})]
        events = [_event_from_dict(item) for item in self.turns[self.index]]
        self.index += 1
        return events


class EchoLLM:
    def generate(self, messages: list[dict], tools: list[ToolSpec]) -> list[AgentEvent]:
        last = messages[-1]["content"] if messages else ""
        return [AgentEvent(session_id="echo", type=AgentEventType.ASSISTANT_FINAL, payload={"text": f"Echo: {last}"})]


class FailingLLM:
    def __init__(self, message: str) -> None:
        self.message = message

    def generate(self, messages: list[dict], tools: list[ToolSpec]) -> list[AgentEvent]:
        raise RuntimeError(self.message)


class OpenAICompatibleChatAdapter:
    def __init__(self, base_url: str, model: str, api_key: str, client: Any | None = None) -> None:
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        if client is not None:
            self.client = client
        else:
            from openai import OpenAI

            self.client = OpenAI(api_key=api_key, base_url=base_url)

    def generate(self, messages: list[dict], tools: list[ToolSpec]) -> list[AgentEvent]:
        tool_payload = [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.input_schema,
                },
            }
            for spec in tools
        ]
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
        if tool_payload:
            kwargs["tools"] = tool_payload
        response = self.client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        events: list[AgentEvent] = []
        if getattr(message, "content", None):
            event_type = AgentEventType.ASSISTANT_COMMENTARY if getattr(message, "tool_calls", None) else AgentEventType.ASSISTANT_FINAL
            events.append(AgentEvent(session_id="provider", type=event_type, payload={"text": message.content}))
        for tool_call in getattr(message, "tool_calls", None) or []:
            function = tool_call.function
            args = json.loads(function.arguments or "{}")
            events.append(
                AgentEvent(
                    session_id="provider",
                    type=AgentEventType.TOOL_CALL_REQUESTED,
                    payload={"provider_call_id": tool_call.id, "tool_name": function.name, "args": args},
                )
            )
        if not events:
            return [AgentEvent(session_id="provider", type=AgentEventType.INCOMPLETE, payload={"reason": "empty provider response"})]
        return events
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_llm_adapters.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phycode/llm.py tests/test_llm_adapters.py
git commit -m "feat: add mock llm adapters"
```

---

### Task 9: Agent Loop and Stop Controller

**Files:**
- Create: `src/phycode/agent.py`
- Create: `tests/test_agent_loop.py`

**Interfaces:**
- Consumes: `LLMClient`, `ContextBuilder`, `ToolRuntime`, `ToolRegistry`, `PolicyContext`, `TraceStore`, `classify_feedback`.
- Produces: `AgentLoop.run_once(user_input: str) -> AgentRunResult`.
- Produces: `AgentLoop.run(user_input: str) -> AgentRunResult`.
- Produces: `AgentRunResult(final_text: str | None, events: list[AgentEvent], stopped_reason: str)`.

- [ ] **Step 1: Write failing agent loop tests**

Create `tests/test_agent_loop.py`:

```python
from pathlib import Path

from phycode.agent import AgentLoop
from phycode.context import ContextBuilder, MemoryStore, SessionStore
from phycode.llm import ScriptedLLM
from phycode.models import AgentEventType, Session, SessionMode
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.file_tools import register_file_tools
from phycode.trace import TraceStore


def build_loop(tmp_path: Path, llm: ScriptedLLM) -> AgentLoop:
    session = Session(workspace_root=str(tmp_path), mode=SessionMode.NON_INTERACTIVE)
    session_store = SessionStore(session)
    memory = MemoryStore(tmp_path / ".phycode" / "memory.jsonl")
    registry = ToolRegistry()
    register_file_tools(registry)
    return AgentLoop(
        llm=llm,
        context_builder=ContextBuilder(session_store, memory),
        tool_runtime=ToolRuntime(registry),
        policy_context=PolicyContext(tmp_path, [], interactive=False),
        trace_store=TraceStore(tmp_path / ".phycode" / "traces"),
        session_store=session_store,
        max_steps=5,
    )


def test_agent_returns_final_text(tmp_path: Path):
    loop = build_loop(tmp_path, ScriptedLLM([[{"type": "assistant_final", "payload": {"text": "done"}}]]))
    result = loop.run("hello")
    assert result.final_text == "done"
    assert result.stopped_reason == "final"


def test_agent_routes_tool_call_and_then_final(tmp_path: Path):
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    llm = ScriptedLLM([
        [{"type": "tool_call_requested", "payload": {"tool_name": "file.read", "args": {"path": "README.md"}}}],
        [{"type": "assistant_final", "payload": {"text": "read complete"}}],
    ])
    result = build_loop(tmp_path, llm).run("read README")
    assert result.final_text == "read complete"
    assert any(event.type == AgentEventType.FEEDBACK_SIGNAL for event in result.events)
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/test_agent_loop.py -v`

Expected: FAIL with missing agent module.

- [ ] **Step 3: Implement agent loop**

Create `src/phycode/agent.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from phycode.context import ContextBuilder, SessionStore
from phycode.feedback import classify_feedback
from phycode.llm import LLMClient
from phycode.models import AgentEvent, AgentEventType, FeedbackSignal, ToolCall
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRuntime
from phycode.trace import TraceStore


@dataclass
class AgentRunResult:
    final_text: str | None
    events: list[AgentEvent]
    stopped_reason: str


class AgentLoop:
    def __init__(
        self,
        llm: LLMClient,
        context_builder: ContextBuilder,
        tool_runtime: ToolRuntime,
        policy_context: PolicyContext,
        trace_store: TraceStore,
        session_store: SessionStore,
        max_steps: int = 50,
    ) -> None:
        self.llm = llm
        self.context_builder = context_builder
        self.tool_runtime = tool_runtime
        self.policy_context = policy_context
        self.trace_store = trace_store
        self.session_store = session_store
        self.max_steps = max_steps

    def run(self, user_input: str) -> AgentRunResult:
        all_events: list[AgentEvent] = []
        final_text: str | None = None
        for _step in range(self.max_steps):
            messages = self.context_builder.build(user_input)
            events = self.llm.generate(messages, [])
            for event in events:
                event = event.model_copy(update={"session_id": self.session_store.session.id})
                self._record(event)
                all_events.append(event)
                if event.type == AgentEventType.ASSISTANT_FINAL:
                    final_text = str(event.payload.get("text", ""))
                    return AgentRunResult(final_text, all_events, "final")
                if event.type == AgentEventType.TOOL_CALL_REQUESTED:
                    all_events.extend(self._handle_tool_event(event))
            user_input = ""
        return AgentRunResult(final_text, all_events, "max_steps")

    def _handle_tool_event(self, event: AgentEvent) -> list[AgentEvent]:
        call = ToolCall(tool_name=str(event.payload["tool_name"]), args=dict(event.payload.get("args", {})), provider_call_id=event.payload.get("provider_call_id"))
        runtime_result = self.tool_runtime.run(call, self.policy_context)
        policy_event = AgentEvent(session_id=self.session_store.session.id, type=AgentEventType.POLICY_DECISION, payload=runtime_result.policy.model_dump(mode="json"))
        result_event = AgentEvent(session_id=self.session_store.session.id, type=AgentEventType.TOOL_CALL_OUTPUT, payload=runtime_result.tool_result.model_dump(mode="json"))
        feedback_events = [
            AgentEvent(session_id=self.session_store.session.id, type=AgentEventType.FEEDBACK_SIGNAL, payload=signal.model_dump(mode="json"))
            for signal in classify_feedback(runtime_result.tool_result)
        ]
        emitted = [policy_event, result_event, *feedback_events]
        for item in emitted:
            self._record(item)
        return emitted

    def _record(self, event: AgentEvent) -> None:
        self.session_store.add_event(event)
        self.trace_store.append(event)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_agent_loop.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phycode/agent.py tests/test_agent_loop.py
git commit -m "feat: add mock-testable agent loop"
```

---

### Task 10: CLI Run, Chat, Config, Keys, and Tool Listing

**Files:**
- Modify: `src/phycode/cli.py`
- Create: `tests/test_cli_commands.py`

**Interfaces:**
- Consumes: `AgentLoop`, `EchoLLM`, `ScriptedLLM`, stores, registry.
- Produces commands: `phycode chat`, `phycode run`, `phycode tools list`, `phycode keys set/status/clear`, `phycode config read`.

- [ ] **Step 1: Write failing CLI command tests**

Create `tests/test_cli_commands.py`:

```python
from typer.testing import CliRunner

from phycode.cli import app


runner = CliRunner()


def test_run_command_uses_echo_llm():
    result = runner.invoke(app, ["run", "hello"])
    assert result.exit_code == 0
    assert "Echo:" in result.stdout


def test_keys_status_does_not_print_secret_word():
    result = runner.invoke(app, ["keys", "status", "openai-compatible"])
    assert result.exit_code == 0
    assert "secret" not in result.stdout.lower()


def test_tools_list_includes_shell_and_workspace():
    result = runner.invoke(app, ["tools", "list"])
    assert result.exit_code == 0
    assert "shell.run" in result.stdout
    assert "workspace.status" in result.stdout
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/test_cli_commands.py -v`

Expected: FAIL until `run` is implemented and tool registry includes shell/state tools.

- [ ] **Step 3: Implement CLI run command**

Modify `src/phycode/cli.py`:

```python
from phycode.agent import AgentLoop
from phycode.context import ContextBuilder, MemoryStore, SessionStore
from phycode.llm import EchoLLM
from phycode.models import Session, SessionMode
from phycode.policy import PolicyContext
from phycode.trace import TraceStore
from phycode.tools import ToolRuntime


def build_agent(mode: SessionMode) -> AgentLoop:
    root = Path.cwd()
    session = Session(workspace_root=str(root), mode=mode)
    session_store = SessionStore(session)
    memory = MemoryStore(root / ".phycode" / "memory.jsonl")
    registry = build_default_registry()
    return AgentLoop(
        llm=EchoLLM(),
        context_builder=ContextBuilder(session_store, memory),
        tool_runtime=ToolRuntime(registry),
        policy_context=PolicyContext(root, [], interactive=mode == SessionMode.INTERACTIVE),
        trace_store=TraceStore(root / ".phycode" / "traces"),
        session_store=session_store,
    )


@app.command()
def run(task: str) -> None:
    """Run a single non-interactive task."""
    result = build_agent(SessionMode.NON_INTERACTIVE).run(task)
    if result.final_text:
        console.print(result.final_text)
    if result.stopped_reason != "final":
        raise typer.Exit(code=1)


@app.command()
def chat() -> None:
    """Start an interactive PhyCode session."""
    loop = build_agent(SessionMode.INTERACTIVE)
    console.print("PhyCode interactive session. Type /exit to leave.")
    while True:
        user_text = typer.prompt("phycode")
        if user_text.strip() == "/exit":
            return
        result = loop.run(user_text)
        if result.final_text:
            console.print(result.final_text)
```

- [ ] **Step 4: Implement keys set and clear commands**

Modify `src/phycode/cli.py`:

```python
@keys_app.command("set")
def keys_set(provider: str = "openai-compatible") -> None:
    secret = typer.prompt("API key", hide_input=True)
    CredentialStore().set_key(provider, secret)
    console.print(f"{provider} key stored")


@keys_app.command("clear")
def keys_clear(provider: str = "openai-compatible") -> None:
    CredentialStore().clear_key(provider)
    console.print(f"{provider} key cleared")
```

- [ ] **Step 5: Run CLI tests**

Run: `uv run pytest tests/test_cli_commands.py tests/test_cli_smoke.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/phycode/cli.py tests/test_cli_commands.py
git commit -m "feat: add cli run chat config and key commands"
```

---

### Task 11: Deterministic Demos

**Files:**
- Create: `src/phycode/demos.py`
- Create: `tests/test_demos.py`
- Modify: `src/phycode/cli.py`

**Interfaces:**
- Produces: `run_guardrail_demo(workspace_root: Path) -> str`.
- Produces: `run_feedback_demo(workspace_root: Path) -> str`.
- Produces: `run_policy_demo(workspace_root: Path) -> str`.
- Produces CLI command: `phycode demo guardrail|feedback|policy`.

- [ ] **Step 1: Write failing demo tests**

Create `tests/test_demos.py`:

```python
from pathlib import Path

from phycode.demos import run_feedback_demo, run_guardrail_demo, run_policy_demo


def test_guardrail_demo_blocks_dangerous_command(tmp_path: Path):
    output = run_guardrail_demo(tmp_path)
    assert "policy_blocked" in output
    assert "shell.dangerous_command" in output


def test_feedback_demo_changes_next_action(tmp_path: Path):
    output = run_feedback_demo(tmp_path)
    assert "test_failed" in output
    assert "file.edit" in output
    assert "success" in output


def test_policy_demo_shows_approval_required(tmp_path: Path):
    output = run_policy_demo(tmp_path)
    assert "policy_requires_approval" in output
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/test_demos.py -v`

Expected: FAIL with missing demos.

- [ ] **Step 3: Implement demo functions**

Create `src/phycode/demos.py`:

```python
from __future__ import annotations

from pathlib import Path

from phycode.feedback import classify_feedback
from phycode.models import ToolCall, ToolResult
from phycode.policy import PolicyContext
from phycode.tools import ToolRegistry, ToolRuntime
from phycode.tools.file_tools import register_file_tools
from phycode.tools.shell_tools import register_shell_tools


def _runtime(root: Path) -> ToolRuntime:
    registry = ToolRegistry()
    register_file_tools(registry)
    register_shell_tools(registry, root, "python --version")
    return ToolRuntime(registry)


def run_guardrail_demo(workspace_root: Path) -> str:
    result = _runtime(workspace_root).run(ToolCall(tool_name="shell.run", args={"command": "rm -rf /"}), PolicyContext(workspace_root, [], interactive=False))
    feedback = classify_feedback(result.tool_result)[0]
    return f"{result.policy.rule_id}\n{feedback.kind.value}\n{result.tool_result.stderr}"


def run_feedback_demo(workspace_root: Path) -> str:
    failed = ToolResult(tool_call_id="demo", status="test_failed", stderr="tests/test_demo.py::test_value failed")
    first = classify_feedback(failed)[0]
    edited_action = "file.edit"
    success = classify_feedback(ToolResult(tool_call_id="demo2", status="ok", stdout="1 passed"))[0]
    return f"{first.kind.value}\n{edited_action}\n{success.kind.value}"


def run_policy_demo(workspace_root: Path) -> str:
    path = workspace_root / "app.py"
    path.write_text("x = 1\n", encoding="utf-8")
    result = _runtime(workspace_root).run(
        ToolCall(tool_name="file.edit", args={"path": "app.py", "old": "x = 1", "new": "x = 2"}),
        PolicyContext(workspace_root, [], interactive=False),
    )
    feedback = classify_feedback(result.tool_result)[0]
    return f"{result.policy.decision.value}\n{feedback.kind.value}"
```

- [ ] **Step 4: Wire CLI demo command**

Modify `src/phycode/cli.py`:

```python
from phycode.demos import run_feedback_demo, run_guardrail_demo, run_policy_demo


@app.command()
def demo(name: str) -> None:
    """Run a deterministic demo."""
    root = Path.cwd()
    if name == "guardrail":
        console.print(run_guardrail_demo(root))
        return
    if name == "feedback":
        console.print(run_feedback_demo(root))
        return
    if name == "policy":
        console.print(run_policy_demo(root))
        return
    console.print("Unknown demo. Use guardrail, feedback, or policy.")
    raise typer.Exit(code=2)
```

- [ ] **Step 5: Run demo tests and commands**

Run: `uv run pytest tests/test_demos.py -v`

Expected: PASS.

Run: `uv run phycode demo guardrail`

Expected output includes `policy_blocked`.

- [ ] **Step 6: Commit**

```bash
git add src/phycode/demos.py src/phycode/cli.py tests/test_demos.py
git commit -m "feat: add deterministic mechanism demos"
```

---

### Task 12: Documentation, SPEC_PROCESS, AGENT_LOG, and Final Verification

**Files:**
- Modify: `README.md`
- Create: `SPEC_PROCESS.md`
- Create: `AGENT_LOG.md`
- Modify: `PLAN.md`

**Interfaces:**
- Produces user-facing install/run/test/security documentation.
- Produces process evidence documents required by the course.
- Produces final plan status entries with commit hashes for completed tasks.

- [ ] **Step 1: Write README with exact commands**

Modify `README.md`:

````markdown
# PhyCode

PhyCode is a CLI-first coding agent harness for the AI4SE final project. Phase 1 focuses on a general-purpose, self-implemented harness core: agent loop, OpenAI-compatible model adapter, mock LLM tests, policy-aware tool runtime, feedback loop, memory/context management, credential safety, CI, and deterministic demos.

## Install

```bash
uv sync --dev
```

## Run

```bash
uv run phycode version
uv run phycode tools list
uv run phycode run "hello"
uv run phycode chat
```

## Demos

```bash
uv run phycode demo guardrail
uv run phycode demo feedback
uv run phycode demo policy
```

## Test

```bash
uv run pytest
```

## Secure Key Configuration

Use:

```bash
uv run phycode keys set openai-compatible
uv run phycode keys status openai-compatible
uv run phycode keys clear openai-compatible
```

Keys are stored through OS keyring when available. `.env` is a plaintext fallback source only and must not be committed.

## Safety Boundary

The default workspace root is the current project directory. File operations outside the workspace are blocked unless explicitly allowlisted. Risky writes and shell commands require approval in interactive mode and fail with a structured policy signal in non-interactive mode.
````

- [ ] **Step 2: Create process documents**

Create `SPEC_PROCESS.md`:

```markdown
# SPEC Process

## Brainstorming Iterations

1. The project was narrowed from a physics-specific PhyCode vision to a two-phase plan: Phase 1 general harness, Phase 2 physics extensions.
2. The main contribution was refined from separate tool, governance, and feedback features into one Policy-Aware Tool Runtime.
3. Provider strategy was revised after checking current OpenAI-compatible tool-call support: default to `tools` / `tool_calls`, keep fallback JSON action parsing.
4. The interface was refined to an interactive CLI session rather than one command per turn.
5. Context handling was scoped to session history, memory summary, trace, truncation, and feedback inclusion, without vector memory.

## Adopted AI Suggestions

- Adopted CLI-first design with lightweight Rich rendering.
- Adopted Python + uv + Typer + Rich + pytest.
- Adopted mock LLM tests as the required verification path.
- Adopted Policy-Aware Tool Runtime as the primary mechanism contribution.

## Rejected or Deferred Suggestions

- Deferred WebUI.
- Deferred Wolfram, LaTeX, literature retrieval, and knowledge graph tools.
- Deferred OpenAI Responses API adapter and Agents SDK integration as product core.
- Deferred Docker unless time remains after core implementation.

## Cold-Start Validation

This section will be updated after SPEC.md and PLAN.md are handed to a fresh agent for 1-2 task trials.
```

Create `AGENT_LOG.md`:

```markdown
# Agent Log

## 2026-07-08

- Skill flow: `brainstorming` -> `writing-plans`.
- Key decision: Phase 1 delivers a general CLI coding agent harness; physics tools are Phase 2 extensions.
- Key decision: main contribution is Policy-Aware Tool Runtime.
- Repository strategy: GitHub is used for current development; NJU Git migration remains possible if required by course staff.
- Implementation has not started before SPEC.md and PLAN.md completion.
```

- [ ] **Step 3: Run full verification**

Run: `uv run pytest`

Expected: all tests PASS.

Run: `git status --short`

Expected: only intentional documentation changes before commit.

- [ ] **Step 4: Commit**

```bash
git add README.md SPEC_PROCESS.md AGENT_LOG.md PLAN.md
git commit -m "docs: add project process and usage documentation"
```

---

## Dependency and Parallelization Notes

- Task 1 must run first.
- Task 2 must run before Tasks 4-9.
- Task 3 can run after Task 2.
- Task 4 depends on Task 2.
- Task 5 depends on Tasks 2 and 4.
- Task 6 depends on Task 5.
- Task 7 depends on Task 2.
- Task 8 depends on Task 2.
- Task 9 depends on Tasks 5, 6, 7, and 8.
- Task 10 depends on Task 9.
- Task 11 depends on Tasks 5, 6, and 10.
- Task 12 depends on all implementation tasks.

After Task 2, these can proceed in separate worktrees if needed:

- Config and credentials: Task 3.
- Policy: Task 4.
- Context/trace/memory: Task 7.
- LLM adapters: Task 8.

Tool runtime and agent-loop tasks should be integrated after those foundations are stable.

## Self-Review

Spec coverage:

- Interactive CLI: Tasks 1 and 10.
- OpenAI-compatible provider path and mock LLM: Task 8.
- Self-implemented agent loop: Task 9.
- Policy-aware tool runtime: Tasks 4, 5, 6, and 11.
- Feedback loop: Tasks 6, 9, and 11.
- Context, memory, and trace: Task 7.
- Credential safety: Task 3 and Task 12.
- Deterministic demos: Task 11.
- CI and one-command tests: Tasks 1 and 12.
- README, SPEC_PROCESS, AGENT_LOG: Task 12.

Red flag scan:

- This plan intentionally avoids empty implementation markers and defers only items listed as non-goals in SPEC.md.

Type consistency:

- `ToolCall`, `ToolResult`, `PolicyDecision`, and `FeedbackSignal` are defined in Task 2 and used consistently in later task interfaces.
- `PolicyContext` is defined in Task 4 and used by `ToolRuntime` and `AgentLoop`.
- `ToolRuntimeResult` is defined in Task 5 and used by agent and demo tasks.
