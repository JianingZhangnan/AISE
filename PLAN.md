# PhyCode 第一阶段 Agent Harness 实施计划

> **面向智能体工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实施本计划。步骤使用复选框（`- [ ]`）语法进行追踪。

**目标：** 构建一个 CLI 优先、自主实现的 coding agent harness，包含策略感知工具运行时、mock LLM 测试路径、安全凭据处理、上下文/记忆/trace 支持、确定性演示和 CI。

**架构：** 项目使用 Python `src/phycode` 包。CLI 调用自有 agent 循环；循环将 LLM 输出规范化为事件，通过策略感知运行时路由工具调用，分类反馈，写入 trace，并构建下一轮上下文。核心测试路径使用脚本化 mock LLM 和 fake 工具执行器，因此必需的验证永远不依赖网络或真实 API key。

**技术栈：** Python 3.11+、`uv`、Typer、Rich、Pydantic v2、pytest、keyring、cryptography、OpenAI-compatible Chat Completions。

## 完成标记说明

每个任务完成后，应在任务标题后标注完成日期和 commit hash，例如：
- **Task 1: 项目脚手架、CLI 冒烟测试和 CI 骨架** - ✅ 完成于 2026-07-08 - commit: `abc1234`

任务内的步骤使用 `- [x]` 标记已完成的 checkbox。

## 全局约束

- 使用 Python 和 `uv`；不使用 pip 或 conda 工作流。
- 第一阶段无 WebUI。
- 不使用 OpenAI Agents SDK、LangChain `AgentExecutor`、AutoGen、CrewAI、LlamaIndex agent 或宿主编码智能体 SDK 的 agent loop 作为产品核心。
- 测试和 CI 不得要求真实 LLM 供应商、网络访问或 API key。
- 在 `SPEC_PROCESS.md` 和 `AGENT_LOG.md` 建立初始记录，并完成陌生 agent 冷启动验证之前，不得编写实现代码。
- 主要供应商路径是 OpenAI-compatible Chat Completions，使用 `tools` / `tool_calls`。
- 每个工具调用必须流经 schema 验证、策略决策、执行包装、反馈映射和 trace 记录。
- 脱敏必须接在统一输出出口上；`redaction.py` 是最后兜底，不能替代“不要让 key 进入日志、trace、错误报告、LLM 消息记录和 CLI 输出”的设计。
- 策略决策准确为 `allow`、`ask` 和 `deny`。
- 工具风险等级准确为 `safe`、`risky` 和 `dangerous`。
- 记忆分类准确为 `decision`、`preference`、`project_fact` 和 `test_command`。
- 反馈类型准确为 `success`、`command_failed`、`test_failed`、`policy_blocked`、`policy_requires_approval`、`invalid_tool_args`、`tool_error`、`timeout` 和 `output_truncated`。
- 默认最大 agent 步数为 50。
- 默认工作区根目录是当前项目目录；额外的根目录需要显式白名单配置。
- `.env`、私钥、token 存储、`.phycode/`、traces、日志和缓存不得提交。
- `.gitlab-ci.yml` 必须包含运行 `uv run pytest` 的 `unit-test` job。

---

## 文件结构

创建这些文件及其职责：

- `pyproject.toml`: 包元数据、依赖、console script、pytest 设置。
- `README.md`: 安装、运行、测试、安全、分发和项目概览。
- `SPEC_PROCESS.md`: brainstorm、AI 建议取舍、冷启动验证和规范修订证据。
- `AGENT_LOG.md`: 按时间顺序记录 agent 工作流、关键决策、commit 和人工干预。
- `.gitlab-ci.yml`: 必需的 `unit-test` CI job。
- `.github/workflows/test.yml`: 便利的 GitHub CI（在 GitHub 上开发期间）。
- `src/phycode/__init__.py`: 包版本。
- `src/phycode/cli.py`: Typer 命令和 Rich 渲染入口点。
- `src/phycode/models.py`: 跨模块共享的 Pydantic 数据模型和枚举。
- `src/phycode/redaction.py`: 秘密脱敏辅助函数。
- `src/phycode/config.py`: 用户/项目配置加载和保存。
- `src/phycode/credentials.py`: keyring 和加密文件凭据存储。
- `src/phycode/policy.py`: 工作区、shell、凭据和审批策略。
- `src/phycode/tools/base.py`: 工具注册表、运行时、执行器协议。
- `src/phycode/tools/file_tools.py`: 文件和搜索工具。
- `src/phycode/tools/shell_tools.py`: shell 和测试工具。
- `src/phycode/tools/state_tools.py`: 工作区、记忆、配置和 key 状态工具。
- `src/phycode/feedback.py`: 反馈分类器。
- `src/phycode/context.py`: 会话存储、记忆存储、上下文构建器。
- `src/phycode/trace.py`: JSONL trace 写入器和读取器。
- `src/phycode/llm.py`: LLM 客户端协议、脚本化 mock、OpenAI-compatible 适配器。
- `src/phycode/agent.py`: agent 循环和停止控制器。
- `src/phycode/demos.py`: 确定性演示场景。
- `tests/`: 单元、集成、CLI、凭据和演示测试。

---

### Task 0: 实现前过程记录和冷启动门禁 - ✅ 完成于 2026-07-08 - commit: `84b8277`

**文件：**
- 修改：`PLAN.md`
- 修改：`docs/superpowers/specs/2026-07-08-phycode-phase1-agent-harness-design.md`
- 修改：`docs/superpowers/plans/2026-07-08-phycode-phase1-agent-harness.md`
- 创建：`SPEC_PROCESS.md`
- 创建：`AGENT_LOG.md`

**接口：**
- 产出实现前过程记录：`SPEC_PROCESS.md`。
- 产出 agent 工作日志：`AGENT_LOG.md`。
- 产出与根目录规范一致的 Superpowers 发现入口。
- 产出实现前门禁状态：冷启动验证通过后，Task 1 才能开始。

- [x] **步骤 1：创建 SPEC_PROCESS 初始记录**

创建 `SPEC_PROCESS.md`，记录 brainstorm 迭代、AI 建议取舍、供应商策略修订、仓库平台策略和冷启动验证状态。初始版本必须明确写出冷启动验证尚未执行，以及进入 Task 1 前必须完成。

- [x] **步骤 2：创建 AGENT_LOG 初始记录**

创建 `AGENT_LOG.md`，按日期记录本项目已经完成的 `brainstorming`、`writing-plans`、Claude review、规范合流，以及当前尚未开始实现代码。

- [x] **步骤 3：同步 Superpowers 发现文档**

将 `docs/superpowers/specs/2026-07-08-phycode-phase1-agent-harness-design.md` 和 `docs/superpowers/plans/2026-07-08-phycode-phase1-agent-harness.md` 改为中文，并同步新版供应商策略：仅支持 OpenAI-compatible `tools` / `tool_calls`，不引入备用 JSON action 解析器。

- [x] **步骤 4：执行陌生 agent 冷启动验证**

由当前人工操作者或项目维护者启动一个未参与前期讨论的外部 agent，只提供 `SPEC.md`、`PLAN.md`、`CLAUDE.md`、过程文档和仓库当前状态，让它复述 Task 1 的目标、文件、红绿测试路径和可能歧义。外部 agent 只输出报告，不直接修改仓库。报告开头必须包含 `git rev-parse HEAD`、`git status --short --branch`，以及 `docs/superpowers/specs/2026-07-08-phycode-phase1-agent-harness-design.md` 的前 5 行，以证明它读取的是最新仓库状态。

如果报告 verdict 为 `FAIL`，当前维护者必须先修订相关文档，把问题、偏差、修订摘要和复验要求写入 `SPEC_PROCESS.md` / `AGENT_LOG.md`，然后重新进行冷启动验证。只有报告 verdict 为 `PASS` 或维护者确认 `PASS_WITH_NOTES` 中的 notes 不阻塞实现时，才可进入 Task 1。

- [x] **步骤 5：提交实现前门禁文档**

```bash
git add PLAN.md SPEC_PROCESS.md AGENT_LOG.md docs/superpowers/specs/2026-07-08-phycode-phase1-agent-harness-design.md docs/superpowers/plans/2026-07-08-phycode-phase1-agent-harness.md
git commit -m "docs: align pre-implementation planning records"
```

---

### Task 1: 项目脚手架、CLI 冒烟测试和 CI 骨架 - ✅ 完成于 2026-07-08 - commit: `0ed8f0f`

**文件：**
- 创建：`pyproject.toml`
- 创建：`uv.lock`
- 创建：`README.md`
- 创建：`.gitlab-ci.yml`
- 创建：`.github/workflows/test.yml`
- 创建：`src/phycode/__init__.py`
- 创建：`src/phycode/cli.py`
- 创建：`tests/test_cli_smoke.py`
- 修改：`.gitignore`

**接口：**
- 产出：console script `phycode = phycode.cli:app`。
- 产出：`phycode.__version__: str`。
- 产出：Typer app 对象 `phycode.cli.app`。

- [x] **步骤 1：编写失败的 CLI 冒烟测试**

创建 `tests/test_cli_smoke.py`：

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

- [x] **步骤 2：运行失败的冒烟测试**

运行：`uv run pytest tests/test_cli_smoke.py -v`

预期：FAIL，报 `phycode` 或 `phycode.cli` 导入错误。

- [x] **步骤 3：创建包脚手架**

创建 `pyproject.toml`：

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

创建 `src/phycode/__init__.py`：

```python
__version__ = "0.1.0"
```

创建 `src/phycode/cli.py`：

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

- [x] **步骤 4：添加 CI 骨架**

创建 `.gitlab-ci.yml`：

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

创建 `.github/workflows/test.yml`：

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

若 `.gitignore` 中没有以下行，则追加：

```gitignore
.phycode/
dist/
build/
*.egg-info/
```

- [x] **步骤 5：运行冒烟测试**

运行：`uv run pytest tests/test_cli_smoke.py -v`

预期：两个冒烟测试均 PASS。

- [x] **步骤 6：提交**

```bash
git add pyproject.toml uv.lock README.md .gitlab-ci.yml .github/workflows/test.yml src/phycode/__init__.py src/phycode/cli.py tests/test_cli_smoke.py .gitignore
git commit -m "chore: scaffold phycode package"
```

---

### Task 2: 核心模型和脱敏 - ✅ 完成于 2026-07-08 - commit: `304ab79`

**文件：**
- 创建：`src/phycode/models.py`
- 创建：`src/phycode/redaction.py`
- 创建：`tests/test_models.py`
- 创建：`tests/test_redaction.py`

**接口：**
- 产出枚举：`AgentEventType`、`ToolRiskLevel`、`PolicyAction`、`FeedbackKind`、`MemoryCategory`、`SessionMode`。
- 产出模型：`AgentEvent`、`ToolSpec`、`ToolCall`、`PolicyDecision`、`ToolResult`、`FeedbackSignal`、`MemoryEntry`、`Session`、`ProviderConfig`。
- 产出函数：`redact_text(text: str) -> str`。

- [x] **步骤 1：编写失败的模型测试**

创建 `tests/test_models.py`：

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

创建 `tests/test_redaction.py`：

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

- [x] **步骤 2：运行测试以验证失败**

运行：`uv run pytest tests/test_models.py tests/test_redaction.py -v`

预期：FAIL，报缺少模块或缺少类。

- [x] **步骤 3：实现核心模型**

创建 `src/phycode/models.py`：

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

- [x] **步骤 4：实现脱敏**

创建 `src/phycode/redaction.py`：

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

- [x] **步骤 5：运行测试**

运行：`uv run pytest tests/test_models.py tests/test_redaction.py -v`

预期：PASS。

- [x] **步骤 6：提交**

```bash
git add src/phycode/models.py src/phycode/redaction.py tests/test_models.py tests/test_redaction.py
git commit -m "feat: add core event models and redaction"
```

---

### Task 3: 配置和凭据存储

**文件：**
- 创建：`src/phycode/config.py`
- 创建：`src/phycode/credentials.py`
- 创建：`tests/test_config.py`
- 创建：`tests/test_credentials.py`
- 修改：`src/phycode/cli.py`

**接口：**
- 消费：`src/phycode/models.py` 中的 `ProviderConfig`。
- 产出：`ProjectConfig`、`UserConfig`、`load_project_config(path: Path) -> ProjectConfig`。
- 产出：`CredentialStore`，包含 `set_key(provider: str, secret: str)`、`get_key(provider: str) -> str | None`、`clear_key(provider: str)`、`status(provider: str) -> CredentialStatus`。
- 产出 CLI 命令：`phycode config read`、`phycode keys status`。

- [x] **步骤 1：编写失败的配置和凭据测试**

创建 `tests/test_config.py`：

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

创建 `tests/test_credentials.py`：

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

- [x] **步骤 2：运行测试以验证失败**

运行：`uv run pytest tests/test_config.py tests/test_credentials.py -v`

预期：FAIL，报缺少模块。

- [x] **步骤 3：实现配置模型**

创建 `src/phycode/config.py`：

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

- [x] **步骤 4：实现凭据存储**

创建 `src/phycode/credentials.py`：

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

- [x] **步骤 5：添加 CLI 状态命令**

通过添加 config 和 keys app 修改 `src/phycode/cli.py`：

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

- [x] **步骤 6：运行测试**

运行：`uv run pytest tests/test_config.py tests/test_credentials.py tests/test_cli_smoke.py -v`

预期：PASS。

- [x] **步骤 7：提交**

```bash
git add src/phycode/config.py src/phycode/credentials.py src/phycode/cli.py tests/test_config.py tests/test_credentials.py
git commit -m "feat: add config and credential foundations"
```

---

### Task 4: 工作区策略和防护栏

**文件：**
- 创建：`src/phycode/policy.py`
- 创建：`tests/test_policy.py`

**接口：**
- 消费：`ToolCall`、`PolicyDecision`、`PolicyAction`。
- 产出：`PolicyContext(workspace_root: Path, allowlist: list[Path], interactive: bool)`。
- 产出：`PolicyEngine.decide(call: ToolCall, context: PolicyContext) -> PolicyDecision`。
- 产出：`resolve_workspace_path(path: str, context: PolicyContext) -> Path`。

- [x] **步骤 1：编写失败的策略测试**

创建 `tests/test_policy.py`：

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

- [x] **步骤 2：运行失败的测试**

运行：`uv run pytest tests/test_policy.py -v`

预期：FAIL，报缺少 policy 模块。

- [x] **步骤 3：实现策略**

创建 `src/phycode/policy.py`：

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

- [x] **步骤 4：运行测试**

运行：`uv run pytest tests/test_policy.py -v`

预期：PASS。

- [x] **步骤 5：提交**

```bash
git add src/phycode/policy.py tests/test_policy.py
git commit -m "feat: add deterministic policy engine"
```

---

### Task 5: 工具注册表和文件/搜索工具

**文件：**
- 创建：`src/phycode/tools/__init__.py`
- 创建：`src/phycode/tools/base.py`
- 创建：`src/phycode/tools/file_tools.py`
- 创建：`tests/test_tool_registry.py`
- 创建：`tests/test_file_tools.py`
- 修改：`src/phycode/cli.py`

**接口：**
- 消费：`PolicyEngine`、`PolicyContext`、`ToolCall`、`ToolResult`。
- 产出：`ToolRegistry.register(spec: ToolSpec, executor: ToolExecutor) -> None`。
- 产出：`ToolRuntime.run(call: ToolCall, context: PolicyContext, approved: bool = False) -> ToolRuntimeResult`。
- 产出：`register_file_tools(registry: ToolRegistry) -> None`。

- [x] **步骤 1：编写失败的注册表和文件工具测试**

创建 `tests/test_tool_registry.py`：

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

创建 `tests/test_file_tools.py`：

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

- [x] **步骤 2：运行失败的测试**

运行：`uv run pytest tests/test_tool_registry.py tests/test_file_tools.py -v`

预期：FAIL，报缺少工具模块。

- [x] **步骤 3：实现注册表和运行时**

创建 `src/phycode/tools/base.py`：

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

创建 `src/phycode/tools/__init__.py`：

```python
from phycode.tools.base import ToolRegistry, ToolRuntime, ToolRuntimeResult

__all__ = ["ToolRegistry", "ToolRuntime", "ToolRuntimeResult"]
```

- [x] **步骤 4：实现文件和搜索工具**

创建 `src/phycode/tools/file_tools.py`：

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

- [x] **步骤 5：连接工具列表 CLI**

修改 `src/phycode/cli.py`：

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

- [x] **步骤 6：运行测试**

运行：`uv run pytest tests/test_tool_registry.py tests/test_file_tools.py tests/test_cli_smoke.py -v`

预期：更新 `tests/test_cli_smoke.py::test_tools_list_command_exists` 以断言 `"file.read"` 出现，然后 PASS。

- [x] **步骤 7：提交**

```bash
git add src/phycode/tools src/phycode/cli.py tests/test_tool_registry.py tests/test_file_tools.py tests/test_cli_smoke.py
git commit -m "feat: add tool registry and file tools"
```

---

### Task 6: Shell、测试、工作区和反馈工具

**文件：**
- 创建：`src/phycode/tools/shell_tools.py`
- 创建：`src/phycode/tools/state_tools.py`
- 创建：`src/phycode/feedback.py`
- 创建：`tests/test_shell_and_feedback.py`
- 创建：`tests/test_state_tools.py`
- 修改：`src/phycode/cli.py`

**接口：**
- 产出：`register_shell_tools(registry: ToolRegistry, workspace_root: Path, test_command: str) -> None`。
- 产出：`register_state_tools(registry: ToolRegistry, workspace_root: Path) -> None`。
- 产出：`classify_feedback(result: ToolResult) -> list[FeedbackSignal]`。

- [x] **步骤 1：编写失败的测试**

创建 `tests/test_shell_and_feedback.py`：

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

创建 `tests/test_state_tools.py`：

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

- [x] **步骤 2：运行失败的测试**

运行：`uv run pytest tests/test_shell_and_feedback.py tests/test_state_tools.py -v`

预期：FAIL，报缺少模块。

- [x] **步骤 3：实现反馈分类器**

创建 `src/phycode/feedback.py`：

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

- [x] **步骤 4：实现 shell 和状态工具**

创建 `src/phycode/tools/shell_tools.py`：

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

创建 `src/phycode/tools/state_tools.py`：

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

- [x] **步骤 5：在 CLI 中注册 shell 和状态工具**

修改 `src/phycode/cli.py` 中的 `build_default_registry()`：

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

- [x] **步骤 6：运行测试**

运行：`uv run pytest tests/test_shell_and_feedback.py tests/test_state_tools.py tests/test_cli_smoke.py -v`

预期：PASS。

- [x] **步骤 7：提交**

```bash
git add src/phycode/tools/shell_tools.py src/phycode/tools/state_tools.py src/phycode/feedback.py src/phycode/cli.py tests/test_shell_and_feedback.py tests/test_state_tools.py
git commit -m "feat: add shell tools and feedback classification"
```

---

### Task 7: Trace、记忆、会话和上下文构建器

**文件：**
- 创建：`src/phycode/trace.py`
- 创建：`src/phycode/context.py`
- 创建：`tests/test_trace_context_memory.py`

**接口：**
- 消费：`AgentEvent`、`FeedbackSignal`、`MemoryEntry`、`Session`。
- 产出：`TraceStore.append(event: AgentEvent) -> None`。
- 产出：`MemoryStore.append(entry: MemoryEntry) -> None`、`MemoryStore.summary() -> str`。
- 产出：`SessionStore.add_event(event: AgentEvent) -> None`。
- 产出：`ContextBuilder.build(current_input: str) -> list[dict[str, object]]`。
- 安全 TODO：在本任务中建立 trace、memory、LLM 消息历史和 CLI/错误报告的统一安全出口；禁止后续业务代码直接记录原始 key、原始模型响应或原始工具输出。

- [x] **步骤 1：编写失败的测试**

创建 `tests/test_trace_context_memory.py`：

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

- [x] **步骤 2：运行失败的测试**

运行：`uv run pytest tests/test_trace_context_memory.py -v`

预期：FAIL，报缺少模块。

- [x] **步骤 3：实现 trace 存储**

创建 `src/phycode/trace.py`：

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

- [x] **步骤 4：实现会话、记忆和上下文**

创建 `src/phycode/context.py`：

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

- [x] **步骤 5：运行测试**

运行：`uv run pytest tests/test_trace_context_memory.py -v`

预期：PASS。

- [x] **步骤 6：提交**

```bash
git add src/phycode/trace.py src/phycode/context.py tests/test_trace_context_memory.py
git commit -m "feat: add trace memory and context stores"
```

---

### Task 8: LLM 适配器和事件规范化

**文件：**
- 创建：`src/phycode/llm.py`
- 创建：`tests/test_llm_adapters.py`

**接口：**
- 消费：`AgentEvent`、`AgentEventType`、`ToolCall`。
- 产出协议：`LLMClient.generate(messages: list[dict], tools: list[ToolSpec]) -> list[AgentEvent]`。
- 产出：`ScriptedLLM`、`EchoLLM`、`FailingLLM`、`OpenAICompatibleChatAdapter`。

- [x] **步骤 1：编写失败的 LLM 测试**

创建 `tests/test_llm_adapters.py`：

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

- [x] **步骤 2：运行失败的测试**

运行：`uv run pytest tests/test_llm_adapters.py -v`

预期：FAIL，报缺少 `phycode.llm`。

- [x] **步骤 3：实现 mock 适配器**

创建 `src/phycode/llm.py`：

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

- [x] **步骤 4：运行测试**

运行：`uv run pytest tests/test_llm_adapters.py -v`

预期：PASS。

- [x] **步骤 5：提交**

```bash
git add src/phycode/llm.py tests/test_llm_adapters.py
git commit -m "feat: add mock llm adapters"
```

---

### Task 9: Agent 循环和停止控制器

**文件：**
- 创建：`src/phycode/agent.py`
- 创建：`tests/test_agent_loop.py`

**接口：**
- 消费：`LLMClient`、`ContextBuilder`、`ToolRuntime`、`ToolRegistry`、`PolicyContext`、`TraceStore`、`classify_feedback`。
- 产出：`AgentLoop.run_once(user_input: str) -> AgentRunResult`。
- 产出：`AgentLoop.run(user_input: str) -> AgentRunResult`。
- 产出：`AgentRunResult(final_text: str | None, events: list[AgentEvent], stopped_reason: str)`。

- [x] **步骤 1：编写失败的 agent 循环测试**

创建 `tests/test_agent_loop.py`：

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

- [x] **步骤 2：运行失败的测试**

运行：`uv run pytest tests/test_agent_loop.py -v`

预期：FAIL，报缺少 agent 模块。

- [x] **步骤 3：实现 agent 循环**

创建 `src/phycode/agent.py`：

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

- [x] **步骤 4：运行测试**

运行：`uv run pytest tests/test_agent_loop.py -v`

预期：PASS。

- [x] **步骤 5：提交**

```bash
git add src/phycode/agent.py tests/test_agent_loop.py
git commit -m "feat: add mock-testable agent loop"
```

---

### Task 10: CLI Run、Chat、Config、Keys 和工具列表 - ✅ 完成于 2026-07-09 - commit: `e858161`

**文件：**
- 修改：`src/phycode/cli.py`
- 创建：`tests/test_cli_commands.py`

**接口：**
- 消费：`AgentLoop`、`EchoLLM`、`ScriptedLLM`、stores、registry。
- 产出命令：`phycode chat`、`phycode run`、`phycode tools list`、`phycode keys set/status/clear`、`phycode config read`。

- [x] **步骤 1：编写失败的 CLI 命令测试**

创建 `tests/test_cli_commands.py`：

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

- [x] **步骤 2：运行失败的测试**

运行：`uv run pytest tests/test_cli_commands.py -v`

预期：FAIL，直到 `run` 实现且工具注册表包含 shell/state 工具。

- [x] **步骤 3：实现 CLI run 命令**

修改 `src/phycode/cli.py`：

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

- [x] **步骤 4：实现 keys set 和 clear 命令**

修改 `src/phycode/cli.py`：

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

- [x] **步骤 5：运行 CLI 测试**

运行：`uv run pytest tests/test_cli_commands.py tests/test_cli_smoke.py -v`

预期：PASS。

- [x] **步骤 6：提交**

```bash
git add src/phycode/cli.py tests/test_cli_commands.py
git commit -m "feat: add cli run chat config and key commands"
```

**收尾记录（2026-07-09）：** Task 10 的严格 CLI 行为由 `tests/test_cli_commands.py` 覆盖，包含 `run`/`chat`、完整默认工具列表、`config read`、`keys set/status/clear`、trace 脱敏和非 final 退出码。实现提交为 `e858161`，最终验证范围纳入 `uv run pytest` 与 `uvx pyright`，随 Task 12 文档收尾一起进入 `codex/task-10-12` review-ready 分支。

---

### Task 11: 确定性演示 - ✅ 完成于 2026-07-09 - commit: `64d0b9c`

> **实现偏离说明（2026-07-09 代码评审后）：** 本任务下方步骤 3 的草案把反馈 demo 的「下一步动作」硬编码为字符串 `"file.edit"`，未真正驱动 agent loop，违反「移除真实 LLM 后核心机制仍应能确定性验证」。实际实现已推翻该草案：新增确定性的 `ReactiveLLM`（输出取决于上下文反馈），用真实 agent loop 呈现 `test.run` 失败 → 因反馈改选 `file.edit` 修复 → 重跑通过 → `assistant_final` 的完整闭环。`run_guardrail_demo`/`run_policy_demo` 亦改为驱动真实 `ToolRuntime`。相关支撑改动（reactive mock、审批接线、停机控制器、`validate_args`、缺失工具补齐、shell 凭据/危险模式加固）见 commits `dc8ca66`、`86af4d9`、`6aa2b6e`、`eb0391f`、`64d0b9c`，评审结论见 `SPEC_PROCESS.md`。下方步骤保留为历史草案，不代表最终实现。
>
> **收尾验证（2026-07-09）：** 三个 demo 命令 `uv run phycode demo guardrail`、`uv run phycode demo feedback`、`uv run phycode demo policy` 已纳入 README 用户命令；最终验证范围继续记录 `uv run pytest` 和 `uvx pyright`。

**文件：**
- 创建：`src/phycode/demos.py`
- 创建：`tests/test_demos.py`
- 修改：`src/phycode/cli.py`

**接口：**
- 产出：`run_guardrail_demo(workspace_root: Path) -> str`。
- 产出：`run_feedback_demo(workspace_root: Path) -> str`。
- 产出：`run_policy_demo(workspace_root: Path) -> str`。
- 产出 CLI 命令：`phycode demo guardrail|feedback|policy`。

- [ ] **步骤 1：编写失败的演示测试**

创建 `tests/test_demos.py`：

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

- [ ] **步骤 2：运行失败的测试**

运行：`uv run pytest tests/test_demos.py -v`

预期：FAIL，报缺少演示。

- [ ] **步骤 3：实现演示函数**

创建 `src/phycode/demos.py`：

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

- [ ] **步骤 4：连接 CLI 演示命令**

修改 `src/phycode/cli.py`：

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

- [ ] **步骤 5：运行演示测试和命令**

运行：`uv run pytest tests/test_demos.py -v`

预期：PASS。

运行：`uv run phycode demo guardrail`

预期输出包含 `policy_blocked`。

- [ ] **步骤 6：提交**

```bash
git add src/phycode/demos.py src/phycode/cli.py tests/test_demos.py
git commit -m "feat: add deterministic mechanism demos"
```

---

### Task 12: README、过程记录收尾和最终验证 - ✅ 完成于 2026-07-09 - review-ready branch: `codex/task-10-12`

**文件：**
- 修改：`README.md`
- 修改：`SPEC_PROCESS.md`
- 修改：`AGENT_LOG.md`
- 修改：`PLAN.md`

**接口：**
- 产出面向用户的安装/运行/测试/安全文档。
- 补全课程要求的过程证据文档。
- 产出最终计划状态条目，包含已完成任务的 commit hash。

- [x] **步骤 1：编写包含确切命令的 README**

修改 `README.md`：

````markdown
# PhyCode

PhyCode 是面向 AI4SE 期末项目的 CLI 优先 coding agent harness。第一阶段聚焦通用、自主实现的 harness 核心：agent 循环、OpenAI-compatible 模型适配器、mock LLM 测试、策略感知工具运行时、反馈闭环、记忆/上下文管理、凭据安全、CI 和确定性演示。

## 安装

```bash
uv sync --dev
```

## 运行

```bash
uv run phycode version
uv run phycode tools list
uv run phycode run "hello"
uv run phycode chat
```

## 演示

```bash
uv run phycode demo guardrail
uv run phycode demo feedback
uv run phycode demo policy
```

## 测试

```bash
uv run pytest
uvx pyright
```

## 安全 Key 配置

使用：

```bash
uv run phycode keys set openai-compatible
uv run phycode keys status openai-compatible
uv run phycode keys clear openai-compatible
```

Key 默认通过操作系统钥匙串存储。`.env` 仅作为明文回退来源，不得提交。

## 安全边界

默认工作区根目录是当前项目目录。超出工作区的文件操作被阻止，除非显式加入白名单。交互模式下，危险写入和 shell 命令需要审批；非交互模式下则返回结构化策略信号并失败。
````

- [x] **步骤 2：补全过程证据**

更新 `SPEC_PROCESS.md`，确保包含：

- brainstorm 关键追问与至少 3 轮关键迭代。
- 采纳、拒绝和修正的 AI 建议及原因。
- 陌生 agent 冷启动验证暴露的问题、产出偏差、SPEC / PLAN 修订前后关键 diff。
- GitHub 与 NJU Git 平台策略的最终状态。

更新 `AGENT_LOG.md`，确保包含：

- 每个 task 的日期、执行方式、使用的 Superpowers 技能和 commit hash。
- subagent 或 inline 执行的关键输出摘要。
- 人工干预、规范偏离、review 结论和修正记录。

- [x] **步骤 3：运行完整验证**

运行：`uv run pytest`

预期：所有测试 PASS。

运行：`uvx pyright`

预期：0 errors。

运行：`git status --short`

预期：提交前仅有刻意的文档变更。

- [x] **步骤 4：提交**

```bash
git add README.md SPEC_PROCESS.md AGENT_LOG.md PLAN.md
git commit -m "docs: add project process and usage documentation"
```

**收尾记录（2026-07-09）：** 文档严格测试由 `tests/test_docs_process.py` 固化，要求 README 包含安装、运行、demo、key 管理、测试与类型检查命令，并要求 PLAN/SPEC_PROCESS/AGENT_LOG 明确 Task 10–12 完成状态、严格 CLI 测试策略、最终验证命令和 `codex/task-10-12` review-ready 分支。最终测试范围记录为 `uv run pytest` 与 `uvx pyright`。

---

## 依赖和并行化说明

- Task 0 必须在任何实现任务之前完成。
- Task 1 必须在 Task 0 通过后运行。
- Task 2 必须在 Task 4-9 之前运行。
- Task 3 可以在 Task 2 之后运行。
- Task 4 依赖 Task 2。
- Task 5 依赖 Task 2 和 Task 4。
- Task 6 依赖 Task 5。
- Task 7 依赖 Task 2。
- Task 8 依赖 Task 2。
- Task 9 依赖 Task 5、6、7 和 8。
- Task 10 依赖 Task 9。
- Task 11 依赖 Task 5、6 和 10。
- Task 12 依赖 Task 0 和所有实现任务。

Task 2 完成后，以下任务可在独立 worktree 中并行进行：

- 配置和凭据：Task 3。
- 策略：Task 4。
- 上下文/trace/记忆：Task 7。
- LLM 适配器：Task 8。

工具运行时和 agent 循环任务应在这些基础稳定后集成。

## 自我审查

规约覆盖：

- 交互式 CLI：Task 1 和 10。
- OpenAI-compatible 供应商路径和 mock LLM：Task 8。
- 自主实现的 agent 循环：Task 9。
- 策略感知工具运行时：Task 4、5、6 和 11。
- 反馈闭环：Task 6、9 和 11。
- 上下文、记忆和 trace：Task 7。
- 凭据安全：Task 3 和 12。
- 确定性演示：Task 11。
- CI 和一键测试：Task 1 和 12。
- README：Task 12。
- SPEC_PROCESS、AGENT_LOG：Task 0 建立初始记录，Task 12 补全最终证据。

红旗扫描：

- 本计划刻意避免空的实现标记，仅推迟 SPEC.md 中列为非目标的项目。

类型一致性：

- `ToolCall`、`ToolResult`、`PolicyDecision` 和 `FeedbackSignal` 在 Task 2 定义，并在后续任务接口中一致使用。
- `PolicyContext` 在 Task 4 定义，并由 `ToolRuntime` 和 `AgentLoop` 使用。
- `ToolRuntimeResult` 在 Task 5 定义，并由 agent 和演示任务使用。

---

## 2026-07-18 PRBench 运行时真正重构（Task 14-25）

批准设计：`docs/superpowers/specs/2026-07-18-prbench-runtime-refactor-design.md`。

逐步实施计划：`docs/superpowers/plans/2026-07-18-prbench-runtime-refactor.md`。

- [x] Task 14：Profile 单一来源与路径可见性。commits：`6f8dd6a`、`468ac42`；spec/quality review clean。
- [x] Task 15：结构化 `process.run(argv)` 与一次性审批。commits：`7ed3423`、`c37467f`、`65fca5c`、`d198f5c`；spec/quality review clean。
- [x] Task 16：Execution journal、公开任务契约与 artifact verifier。commits：`4bd700f`、`a429dbe`、`67b7c3e`；spec/quality review clean。
- [x] Task 17：AgentLoop 完成门禁与连续无进展停机。commits：`5d561b3`、`9b7fa50`；spec/quality review clean。
- [x] Task 18：PRBench runner 与 CLI 状态契约。commits：`537b8dc` 至 `148df4f`；spec/quality review clean。
- [x] Task 19：固定版本官方 evaluator adapter。commits：`31f58a4`、`1b0a448`；spec/quality review clean。
- [x] Task 20：中文文档、过程证据和可重复真实 smoke 命令。commit：本次文档提交；真实凭据与官方运行保留给主 agent。
- [x] 真实运行反馈重构：provider 原生 tool conversation、因果 feedback/blocker、no-progress epoch、安全事件计数与 stale batch。commits：`c02de06`、`5f109de`、`9c6db1a`、`d955592`、`d5496b6`。
- [x] 安全 Python alias 与 normalizer identity 门禁：commits `84ee1e8`、`a2fce92`；独立复审 Critical 0 / Important 0。
- [x] Task 22：审批清单瞬时无效时安全继续轮询。commit：`8954cbd`。
- [x] Task 23：审批 request/grant 严格单一 schema。commit：`9c37d04`。
- [x] Task 24：将连续重复失败绑定到完整动作身份。commit：`49db986`。
  - 目标：同一工具使用不同参数或不同脚本内容进行纠错时，不因共享 tool/feedback
    类型而被误判为重复失败；完全相同动作连续失败达到阈值仍必须停机。
  - 文件：`src/phycode/agent.py`、`tests/test_agent_loop.py`、`AGENT_LOG.md`、
    `PLAN.md`。
  - TDD RED：同一路径连续三次使用不同 `old` 参数执行失败的 `file.edit`，然后执行
    正确 edit 并 final；旧实现第三次失败后提前返回 `repeated_failure`。
  - 实现：复用执行前已有的 `_ActionIdentity`（tool name、args SHA-256，以及
    `process.run` 的脚本 SHA-256），与 feedback kind 共同构成 failure streak key。
  - 验证：新恢复用例与既有相同动作重复停机用例同时通过；完整 agent/prbench loop
    回归和 pyright 通过。
- [x] Task 25：让同一 process target 的新脚本版本成功执行淘汰过期 blocker。commit：
  `f31112e`。
  - 目标：旧脚本的 `approval_required` / `process_failed` 在同一调用目标的新内容版本
    成功执行后不再污染最终状态；不同 process target 以及 read/write 成功不能清除。
  - 文件：`src/phycode/agent.py`、`tests/test_prbench_loop.py`、`AGENT_LOG.md`、
    `PLAN.md`。
  - TDD RED：旧脚本审批失败或执行失败，随后 `file.edit` 修复并成功重跑同一脚本；
    旧实现仍分别返回 `approval_required` / `process_failed`。
  - 实现：完整 action identity 继续服务重复失败；blocker 额外保存由原始参数解析的
    workspace cwd、脚本规范路径与尾随 argv。executable 和脚本内容 SHA 不属于跨版本
    target identity，因此裸 `python` 与绝对解释器不会制造假差异；工具 normalizer 仍
    只在实际 runtime 边界执行一次。
  - 验证：同 target 的审批/执行失败恢复，以及不同 target、read/write 不清除的反例均
    固化；完整 agent/prbench loop 回归和 pyright 通过。
- [x] 官方真实验收：固定 evaluator commit 上 `aaatest_helloworld`、`bbbtest_alphabet` 均由白色 runner `completed`，绿色 grader 均为 1.0；trace/journal/artifact/hash 与凭据泄漏扫描全部通过。

基础依赖关系为 Task 14 → 15 → 16 → 17 → 18 → 19 → 20；真实运行反馈再依次触发原生对话/因果状态、安全 alias、Task 22 与 Task 23，最终 review 触发 Task 24 → Task 25。实现任务由独立 subagent 按 TDD 完成并接受复审；最终由主 agent 在不暴露凭据的前提下使用真实 `deepseek-v4-pro` 和固定官方 evaluator commit 完成双任务验收。

本批 PRBench 运行时真正重构明确拒绝继续维护旧 parser：字符串级 shell
lexer/state machine 无法覆盖解释器拼接、变量展开、symlink 和不同 shell 语义，
其复杂度增长也没有形成权威安全边界。替代方案是结构化 argv、路径 visibility、
一次性审批、execution provenance、artifact verifier 和 evaluator 生命周期隔离。

真实 API 验收边界在 Task 20 文档/确定性全量测试之后：仅主 agent 可把真实
provider 值读入当前子进程并运行 `aaatest_helloworld`、`bbbtest_alphabet`；
subagent、默认 `uv run pytest` 和 CI 均不得读取凭据或把 mock 成功表述为真实能力。
官方 fresh 环境还必须通过 uv 临时 exact overlay
`a2a-sdk[http-server]==0.3.8` 启动固定 commit，避免普通解析选择 1.1.1 后产生
upstream import API 漂移；overlay 不修改上游依赖声明。最终只有 runner
`completed`、expected outputs、官方 evaluator 报告和 key/URL 泄漏扫描全部通过，
才能记录真实验收成功。2026-07-18 最终验收已满足该门禁：两项 runner 均为
`completed`、两份官方报告均为 1.0，声明/实际 trace 计数一致，journal 与 artifact
哈希可复算，真实 URL/key 文件扫描和 Git 历史扫描均为 0 命中。

---

## 2026-07-19 交互式审批提示可见性修复（Task 26）

批准设计：`docs/superpowers/specs/2026-07-19-interactive-approval-visibility-design.md`。

逐步实施计划：`docs/superpowers/plans/2026-07-19-interactive-approval-visibility.md`。

- [x] Task 26：在阻塞式审批前暂停 Rich spinner，审批结束后恢复，并保证原始
  `approval_handler` 在正常和异常路径都被恢复；用 PTY 根因证据和确定性事件顺序
  测试固化。修复构建版本为 `0.1.1`，不在本任务内发布 GitHub Release。commits：
  `7b22800`、`8484cdc`、`b900998`；task spec/quality review 与最终全分支 review
  clean（Critical 0 / Important 0 / Minor 0，Ready to merge）。
- [x] Task 27：修复 PR #1 在 Ubuntu CI 暴露的跨平台门禁差异。CLI 断言先去除
  ANSI 样式；PowerShell smoke 在 Windows 创建 `uv.cmd`、在 POSIX 创建可执行
  `uv`；process runtime 测试保留精确裸 `python` alias 合同并用其他相对名验证拒绝；
  策略层对 POSIX NUL 路径产生的 `ValueError` 失败关闭。commit：`aa061b2`。
  TDD RED 为 GitHub Actions 的 9 failures 与 WSL 定向复现；Windows 全量测试、
  `CI=true/GITHUB_ACTIONS=true` 的 WSL 全量测试、Pyright 和构建均通过。

---

## 2026-07-19 斜杠命令实时补全（Task 28–31）

批准设计：`docs/superpowers/specs/2026-07-19-slash-command-completion-design.md`。

逐步实施计划：`docs/superpowers/plans/2026-07-19-slash-command-completion.md`。

- [x] Task 28：建立唯一声明式斜杠命令注册表，统一规范命令、别名、参数、帮助与
  CLI 副作用分发。commit：`47e218b`；独立审查 Critical/Important/Minor 为 0/0/0。
- [x] Task 29：增加 `prompt-toolkit>=3.0.52,<4`、稳定模糊补全、八行候选上限与
  线程安全的会话模型缓存。commit：`6a74122`；独立审查无 Critical/Important，记录
  一项并发首次加载直接测试的非阻塞 Minor，交由 whole-branch review 最终裁决。
- [x] Task 30：接入真实 TTY `PromptSession`、键盘行为、动态底栏、真实模型后台候选
  与非 TTY 整行回退。commit：`6ebf027`；独立审查 Critical/Important/Minor 为 0/0/0。
- [x] Task 31：补齐 README/SPEC/过程合同并完成真实 Windows PTY、真实模型枚举、
  真实 LLM 响应、Windows/WSL 全量测试、Pyright、0.1.1 构建与凭据泄漏扫描。
  文档与真实验收 commit：`bb6efc8`。真实验收发现并修复旧 `/models` 供应商异常可能
  暴露凭据片段的问题，安全修复 commit：`14ec52e`；URL/key 在 worktree、解包构建物
  与全部 Git 历史均为 0 命中。whole-branch review 发现的候选滚动、模型加载并发、
  敏感 history 与真实菜单高度问题分别由 `35f9c11`、`b0428c6` 关闭；最终复审为
  Critical 0、Important 0、Minor 0，Ready to merge。

本批次不修改 AgentLoop、策略、工具权限、审批语义、trace、凭据存储、版本或 Release
元数据；默认测试和 CI 保持离线。最终主 agent 复验 Windows/WSL 全量测试均达到
100% exit 0，Pyright 0/0，0.1.1 wheel/sdist 构建成功；含 22 个真实模型候选的 PTY
滚动、补全和真实 LLM 响应通过，最终 URL/key 泄漏扫描仍为 0。分支已满足收尾门禁。

---

## 2026-07-19 PRBench 完整公开任务（Task 32–36）

本批目标是在 `codex/prbench-public-test` 上为单个**完整公开任务**
`task_white_1993` 建立确定性的 artifact provenance、紧凑上下文、固定 evaluator 入口
与正式运行前文档门禁；它不代表 holdout 或课程最终成绩。实际依赖链为
Task 32 → Task 33 → Task 34 → Task 35 → Task 34B/34C/34D/34E → 主 agent 最多五次
正式验收 → Task 36A 脱敏记录 → Task 36B whole-branch review 与最终复验。

- [x] Task 32：把可执行 Python artifact 与普通 expected Python 分开建模，只允许
  `execution_entrypoints` 为 CSV 提供成功 execution provenance，并增加 CSV 数据行数
  约束。实际范围 `bfae0be7eb3d7f9373929ef18a0a236e718be375..959eb44fb5af1cc897f1ec4c274013681f30fdb8`；
  实现 `564659dd8aa66f2dfed2b5c2833a74db50865758`，审查测试修复
  `959eb44fb5af1cc897f1ec4c274013681f30fdb8`；最终 review clean，Critical / Important /
  Minor 为 0 / 0 / 0。
- [x] Task 33：用仅含公开路径和产物清单的 compact brief 替换长正文 prompt，并贯通
  `max_context_chars`；上下文不足时在 provider 前 fail closed。实际范围
  `959eb44fb5af1cc897f1ec4c274013681f30fdb8..7fe73aa7bba48f9def3a97c0b8e8ebcbc5439139`；
  实现 `69dadc1ef3a8314e4ebed7b88b8668006f2f0d71`，审查修复
  `7fe73aa7bba48f9def3a97c0b8e8ebcbc5439139`；最终 review clean，0 / 0 / 0。
- [x] Task 33 分支级回归纠正：删除过期的 runner-side read 测试假设，并锁定公开
  instruction 的一次安全验证发生在首次 provider 调用前。实际范围
  `e51a82ca50ddde519f353bbd4b7962a1d87ca8f7..7547db2a9ed8db98cb6b86d6ea95c186e30192d7`；提交
  `0d4582b483530653ba1220b1d8e79673f7ca310c` 与
  `7547db2a9ed8db98cb6b86d6ea95c186e30192d7`；完整 692 项 exit 0，修复后 review
  clean，0 / 0 / 0。
- [x] Task 34：增加 `task_white_1993` 完整 contract、固定 adapter 的
  `max-tool-calls` / `max-context-chars` 参数链和 `run_public_full.ps1`。实际范围
  `7fe73aa7bba48f9def3a97c0b8e8ebcbc5439139..e51a82ca50ddde519f353bbd4b7962a1d87ca8f7`；
  实现 `4cde23b4edf95d98cc6ea50e4c66ee2a80fd8d43`，独立审查修复 `e51a82ca50ddde519f353bbd4b7962a1d87ca8f7`；
  最终 review clean，0 / 0 / 0。
- [x] Task 35：补齐中文 README、PLAN、SPEC_PROCESS、AGENT_LOG 和文档合同，完成
  full pytest、Pyright、build、fresh adapter/patch、双 PowerShell AST、凭据与本地产物
  门禁。主实现实际范围为
  `7547db2a9ed8db98cb6b86d6ea95c186e30192d7..71656cf630ee1f7e87b1805b53e502596818b707`，
  提交信息为
  `docs(prbench): document full public evaluation gate`，主实现 commit 为
  `71656cf630ee1f7e87b1805b53e502596818b707`。文档合同 27 passed，最终全仓收集 693
  项、Pyright 0 / 0，构建、
  fresh adapter/patch、双 PowerShell AST、wheel 解包、凭据与新增运行产物扫描均通过；
  随后的独立 review 为 **Changes requested**，0 Critical / 3 Important / 0 Minor：正式
  运行手册的 active workspace 错误、审批判别条件缺失、文档合同跨全文偶然命中。当前
  review 修复以 section-scoped 合同得到自然 RED，再以旧 workspace 文本 mutation 得到
  判别性 RED；恢复后聚焦测试 1 passed、完整文档 27 passed，全仓 693 项达到 100%
  exit 0，Pyright 0 / 0，diff、凭据与新增运行产物扫描通过。修复提交信息为
  `docs(prbench): harden full-run approval guide`，commit 为
  `0c0b5b0f6e322e1c6a8e0f57d23716f24a1ec23f`。该阶段遵守先修复、后复审的顺序，没有
  预写 clean 结论；随后 Task 35 修复后独立复审完成，核对范围为
  `7547db2a9ed8db98cb6b86d6ea95c186e30192d7..0c0b5b0f6e322e1c6a8e0f57d23716f24a1ec23f`，
  Review clean，Critical / Important / Minor 为 0 / 0 / 0，规格与文档/测试质量均通过。
- [x] Task 34B / 34C：分别修复 green evaluator 与 white evaluator 的 UTF-8 文本
  I/O，提交 `31704dd6aa2ca72dd9367a07ed7e00000f431d1d` 与
  `86251927aa00e3967408637e2838eb7b03816c79`。
- [x] Task 34D：为正式运行增加分页、保留发现阶段工具配额，并保持 positional event
  sink 兼容；提交 `ee11a097baa1dcc42d4bf5f526f527d64ef960a8`、
  `4da448b0ec3f6072c94576c1a7699297317de776`，最终 review clean，0 / 0 / 0。
- [x] Task 34E：把 PRBench adapter 的 provider deadline 调整为有界 600 秒，提交
  `23b67474d73d206d531a199e7dbcb95456022080`，最终 review clean，0 / 0 / 0；普通
  adapter 的 120 秒 deadline 保持不变。
- [x] Task 36A：脱敏结果记录。Task 36 脱敏结果记录已完成：只消费主 agent 提供的
  脱敏摘要；初次三次尝试记录的 commit 为 `b303d52cf1e7cf2811d42c0aab917f056bc92922`。
  用户把上限扩展到五次后，本次继续在 Task 36A 范围内补齐最终五次未成功验收、review
  与泄漏扫描，只修改过程文档及其合同测试。
- [x] Task 36B：whole-branch review 与最终复验。Task 36 whole-branch review 与最终复验已完成；
  commit：`7c623d7`。最终审查范围为
  `588aa08ab56f929b4ac61895227574306a16ee13..50f1089f47eda141b8715bf937ecd318c49d2a48`，
  共 35 commits / 30 files，结论为 Critical / Important / Minor = 0 / 0 / 3，Ready。
  Task 36 整体按过程门禁完成，但五次正式尝试仍未跑通，绝不改写为成功。
  三项非阻塞 Minor 为：每个 CSV 的 capture 上限均为 8 MiB，但没有全局 capture 总预算；
  没有真实 Windows junction/reparse 集成覆盖，现有覆盖为 synthetic；没有任意未知
  output-group 名的专门变异测试，但实现对动态 values 的处理正确。
  最终复验在清除 evaluator/provider 环境变量后完成：离线 pytest exit 0、766 collected；
  Pyright 0 errors / 0 warnings / 0 informations；fresh、固定到 evaluator commit
  `3e5bee4545cad2138832f06302e9c98bd81f5216` 的 clean adapter 为
  128 collected / 126 passed / 2 skipped；`uv build` 成功；`pwsh` 与 Windows PowerShell AST
  均通过；`git diff --check` 通过；worktree clean。HEAD 的 109 个 tracked regular blobs 中
  credential filenames 0、高置信 secret 0，35 commits 历史相同项 0；branch diff 运行产物
  路径 0；7 个 evaluator/provider 环境变量均 absent；`dist`、`.pytest_cache`、`.venv` 与
  授权 source 均 ignored。

### task_white_1993 完整公开任务真实验收

- 固定 evaluator commit 为 `3e5bee4545cad2138832f06302e9c98bd81f5216`。用户把正式尝试上限从 3 次扩展到 5 次，
  最后两次指定模型 `glm-5.2`；正式尝试次数为 5，上限已经用尽，
  没有第 6 次。
- 尝试 1：模型 `deepseek-v4-pro`，runner `tool_budget_exhausted`，50 次工具调用，`overall_score` 0.0。
- 尝试 2：模型 `deepseek-v4-pro`，runner `provider_error`，13 次工具调用，`overall_score` 0.0。
- 尝试 3：模型 `deepseek-v4-pro`，runner `approval_required`，42 次工具调用，20 项声明产物存在 13 项，7 项 CSV 存在 0 项，`overall_score` 0.17。
- 尝试 4：模型 `glm-5.2`，runner `provider_error`，11 次工具调用，20 项声明产物存在 0 项，`overall_score` 0.0，约 720 秒。
- 尝试 5：模型 `glm-5.2`，runner `provider_error`，11 次工具调用，20 项声明产物存在 0 项，white 约 662 秒、grader 约 700 秒，`overall_score` 0.0。
- 最佳结果仍是未成功的尝试 3。成功标准保持为 runner `completed` 与有效 green report
  同时成立；五次均未满足，因此完整公开任务未跑通，不得声称成功。
- 首次模型响应前的基础设施/预检失败不计次数：两次 OpenCode 安装相关失败、一次旧
  exact-equality contract preflight 失败，以及一次手动预检后的 double-adapter
  clean-check 失败。
- 正式运行期间修复/review 关键 commits：`4e831d1`、`a0f8df9`、`c3be45e..fb42598`、
  `2011e84`、`1d30458`、`a5be873`、`1c410ab`、`f99cec8`。最终 contract spec review 为
  Critical / Important / Minor = 0 / 0 / 0；quality review 为 0 / 0 / 1，Ready，唯一 Minor
  是没有用任意未知组名做专门变异测试。artifact review 曾有两个非阻塞 Minor：缺少全局
  CSV capture 总预算，以及缺少真实 Windows junction 集成覆盖。
- 当前凭据泄漏扫描：HEAD 的 109 个 tracked regular blobs（仅 mode 100644/100755，排除
  gitlink）中，两组 exact key 匹配 0、读取错误 0；本地 `.superpowers/sdd` 与 `dist` 排除
  `.git`、`.venv`、`node_modules`、`_ground_truth`、`groundtruth`、`reference` 后的 1000 个
  文件中，两组 exact key 匹配 0、读取错误 0，其中日志/trace/report/wheel 筛选出的 81 个
  文件同样为两组 exact key 匹配 0、读取错误 0。7 个 provider/PRBench 相关环境变量均
  absent，容器数 0。相关本地产物继续保持 ignored；评测产物未提交。
- Task 36 脱敏结果记录已完成；Task 36 whole-branch review 与最终复验已完成。过程门禁完成
  不改变真实验收结论：五次正式尝试仍未跑通。
