# 斜杠命令实时补全实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `phycode chat` 增加接近 Claude Code 的斜杠命令候选、实时过滤、参数提示、键盘选择和真实模型候选，同时保持非 TTY、审批与 AgentLoop 行为不变。

**Architecture:** 新增一个聚焦的 `interactive.py`：声明式注册表成为命令名、别名、用法、帮助和补全的唯一来源；`prompt_toolkit` 只负责真实 TTY 输入与候选渲染，CLI 继续拥有配置、凭据和会话副作用。模型 ID 通过可注入 loader 在补全线程中获取并只缓存于当前会话；非 TTY 使用现有 Typer 整行输入回退。

**Tech Stack:** Python 3.11+、`uv`、Typer、Rich、`prompt-toolkit>=3.0.52,<4`、pytest、Pyright。

## Global Constraints

- 所有项目文档使用中文；代码注释与 commit message 可使用英文。
- Python 包管理、依赖更新、测试和构建一律使用 `uv`，不得使用 `pip` / `conda`。
- 必须先看到每个新增行为的有效失败测试，再写最少实现使其通过，最后才允许重构。
- 规范命令固定为 `/model`、`/url`、`/key`、`/models`、`/config`、`/status`、`/help`、`/exit`。
- `/login`、`/quit`、`/?` 等现有别名继续可执行，但候选默认只显示规范命令。
- `prompt-toolkit` 版本约束固定为 `>=3.0.52,<4`；不引入 Textual、全屏 TUI 或第二套应用级事件循环。
- 不实现 `@文件`、`!shell`、自定义命令、插件命令或跨会话持久历史。
- 不修改 AgentLoop、策略引擎、工具运行时、审批语义、trace 或凭据存储机制。
- `/key` 只进入现有隐藏输入，不生成参数候选，不进入普通输入历史。
- 模型候选只在当前会话内缓存；失败消息不得包含 API key、真实 URL 或原始供应商异常。
- 默认测试与 CI 必须确定性、离线且不要求真实凭据；真实 API smoke 只由主 agent 在最终门禁执行。
- 非 TTY、重定向输入和 `CliRunner` 必须继续使用当前整行输入路径。
- 每项实现只有通过 spec 合规复审和代码质量复审后才算完成。
- 用户拥有的未跟踪 `AGENTS.md` 不得修改、暂存或提交。

---

## 文件结构

- Create: `src/phycode/interactive.py` — 命令元数据、规范化解析、补全、会话模型缓存和输入端口。
- Create: `tests/test_interactive.py` — 注册表、解析、补全、缓存和真实按键输入的确定性测试。
- Modify: `src/phycode/cli.py` — 使用规范动作分发斜杠命令并选择 TTY/非 TTY 输入端口。
- Modify: `tests/test_cli_commands.py` — CLI 接线、Ctrl+C 恢复和模型缓存刷新回归。
- Modify: `pyproject.toml` — 增加 `prompt-toolkit>=3.0.52,<4` 直接依赖。
- Modify: `uv.lock` — 锁定解析后的 prompt-toolkit 3.x 版本。
- Modify: `README.md` — 记录候选菜单、键盘操作和参数补全。
- Modify: `SPEC.md` — 把斜杠补全加入 CLI 功能与可用性验收。
- Modify: `tests/test_docs_process.py` — 固化 README/SPEC 的用户可见合同。
- Modify: `PLAN.md` — 增加 Task 28–31 的完成记录和实际 commit hash。
- Modify: `AGENT_LOG.md` — 记录 brainstorm、TDD、subagent review、真实终端/API smoke 与安全扫描。

依赖关系：Task 1 → Task 2 → Task 3 → Task 4。三个实现任务修改同一新模块，不能并行；每个任务使用新的实现 subagent，并在下一个任务开始前完成两阶段复审。Task 4 涉及真实凭据，只由主 agent 执行。

---

### Task 1: 声明式命令注册表、解析与帮助

**Files:**
- Create: `src/phycode/interactive.py`
- Create: `tests/test_interactive.py`
- Modify: `src/phycode/cli.py:192-251`
- Test: `tests/test_cli_commands.py:83-161`

**Interfaces:**
- Consumes: 现有 `_handle_slash(line: str) -> str | None` 的用户可见行为和 `write_config_value()` / `CredentialStore` 副作用。
- Produces: `SlashAction`、`SlashArgumentSpec`、`SlashCommandSpec`、`ParsedSlashCommand`、`SLASH_COMMANDS`、`resolve_slash_command(name: str) -> SlashCommandSpec | None`、`parse_slash(line: str) -> ParsedSlashCommand`、`render_slash_help() -> str`。

- [ ] **Step 1: 写注册表与解析 RED 测试**

创建 `tests/test_interactive.py`，先只覆盖不依赖 prompt-toolkit 的合同：

```python
from __future__ import annotations

from phycode.interactive import (
    SLASH_COMMANDS,
    SlashAction,
    parse_slash,
    render_slash_help,
    resolve_slash_command,
)


def test_slash_registry_is_the_single_canonical_command_set():
    assert [spec.name for spec in SLASH_COMMANDS] == [
        "model",
        "url",
        "key",
        "models",
        "config",
        "status",
        "help",
        "exit",
    ]
    assert len({spec.action for spec in SLASH_COMMANDS}) == len(SLASH_COMMANDS)
    login = resolve_slash_command("login")
    quit_command = resolve_slash_command("quit")
    help_alias = resolve_slash_command("?")
    assert login is not None and login.action is SlashAction.KEY
    assert quit_command is not None and quit_command.action is SlashAction.EXIT
    assert help_alias is not None and help_alias.action is SlashAction.HELP


def test_parse_slash_normalizes_quotes_and_reports_missing_required_argument():
    missing = parse_slash("/model")
    assert missing.spec is not None
    assert missing.spec.action is SlashAction.MODEL
    assert missing.needs_argument is True

    parsed = parse_slash('/model "deepseek-chat"')
    assert parsed.argument == "deepseek-chat"
    assert parsed.needs_argument is False

    unknown = parse_slash("/bogus")
    assert unknown.spec is None
    assert unknown.raw_name == "bogus"


def test_render_slash_help_is_derived_from_every_canonical_spec():
    help_text = render_slash_help()
    for spec in SLASH_COMMANDS:
        assert spec.usage in help_text
        assert spec.description in help_text
```

- [ ] **Step 2: 运行聚焦测试，确认有效 RED**

运行：

```powershell
uv run pytest tests/test_interactive.py -q
```

预期：测试收集失败，明确报告 `ModuleNotFoundError: No module named 'phycode.interactive'`；失败不得来自环境、语法或第三方依赖。

- [ ] **Step 3: 写最小注册表与解析实现**

创建 `src/phycode/interactive.py`，实现以下不可变数据结构和固定注册表：

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class SlashAction(str, Enum):
    MODEL = "model"
    URL = "url"
    KEY = "key"
    MODELS = "models"
    CONFIG = "config"
    STATUS = "status"
    HELP = "help"
    EXIT = "exit"


@dataclass(frozen=True)
class SlashArgumentSpec:
    name: str
    description: str
    example: str
    required: bool = True
    completion_source: Literal["models"] | None = None


@dataclass(frozen=True)
class SlashCommandSpec:
    name: str
    action: SlashAction
    description: str
    aliases: tuple[str, ...] = ()
    argument: SlashArgumentSpec | None = None

    @property
    def usage(self) -> str:
        if self.argument is None:
            return f"/{self.name}"
        brackets = ("<", ">") if self.argument.required else ("[", "]")
        return f"/{self.name} {brackets[0]}{self.argument.name}{brackets[1]}"


@dataclass(frozen=True)
class ParsedSlashCommand:
    raw_name: str
    spec: SlashCommandSpec | None
    argument: str

    @property
    def needs_argument(self) -> bool:
        return bool(
            self.spec
            and self.spec.argument
            and self.spec.argument.required
            and not self.argument
        )


SLASH_COMMANDS = (
    SlashCommandSpec(
        "model",
        SlashAction.MODEL,
        "切换当前模型",
        argument=SlashArgumentSpec(
            "name", "供应商返回的模型 ID", "deepseek-chat", completion_source="models"
        ),
    ),
    SlashCommandSpec(
        "url",
        SlashAction.URL,
        "设置 OpenAI-compatible 接口地址",
        argument=SlashArgumentSpec("base_url", "HTTPS API 根地址", "https://example.com/v1"),
    ),
    SlashCommandSpec("key", SlashAction.KEY, "隐藏输入并保存 API key", aliases=("login",)),
    SlashCommandSpec("models", SlashAction.MODELS, "列出当前凭据可用的模型"),
    SlashCommandSpec("config", SlashAction.CONFIG, "显示当前非敏感配置"),
    SlashCommandSpec("status", SlashAction.STATUS, "显示凭据配置状态"),
    SlashCommandSpec("help", SlashAction.HELP, "显示斜杠命令帮助", aliases=("?",)),
    SlashCommandSpec("exit", SlashAction.EXIT, "退出交互式会话", aliases=("quit",)),
)


def resolve_slash_command(name: str) -> SlashCommandSpec | None:
    folded = name.casefold()
    for spec in SLASH_COMMANDS:
        if folded == spec.name.casefold() or folded in {alias.casefold() for alias in spec.aliases}:
            return spec
    return None


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_slash(line: str) -> ParsedSlashCommand:
    if not line.startswith("/"):
        raise ValueError("slash commands must start with '/'")
    parts = line[1:].split(maxsplit=1)
    raw_name = parts[0].casefold() if parts else ""
    argument = _strip_wrapping_quotes(parts[1].strip()) if len(parts) > 1 else ""
    spec = resolve_slash_command(raw_name) if raw_name else resolve_slash_command("help")
    return ParsedSlashCommand(raw_name=raw_name, spec=spec, argument=argument)


def render_slash_help() -> str:
    width = max(len(spec.usage) for spec in SLASH_COMMANDS)
    lines = ["Commands:"]
    lines.extend(f"  {spec.usage:<{width}}  {spec.description}" for spec in SLASH_COMMANDS)
    return "\n".join(lines)
```

如果 formatter 将长构造行拆开，可以调整排版，但字段名、类型、规范命令顺序和用户可见文字不得改变。

- [ ] **Step 4: 运行注册表测试，确认 GREEN**

运行：

```powershell
uv run pytest tests/test_interactive.py -q
```

预期：3 passed。

- [ ] **Step 5: 让 CLI 只按规范动作分发**

在 `src/phycode/cli.py` 导入 `SlashAction`、`parse_slash` 和 `render_slash_help`，并把手写 `_CHAT_HELP` 改为：

```python
_CHAT_HELP = render_slash_help()
```

用规范动作重写 `_handle_slash()` 的条件分支；副作用保持现状：

```python
def _handle_slash(line: str) -> str | None:
    parsed = parse_slash(line)
    if parsed.spec is None:
        console.print(f"unknown command: /{parsed.raw_name} (try /help)", markup=False)
        return None
    action = parsed.spec.action
    if action is SlashAction.EXIT:
        return "exit"
    if action is SlashAction.MODELS:
        _print_models()
        return "refresh_models"
    if action is SlashAction.HELP:
        console.print(_CHAT_HELP, markup=False)
        return None
    if parsed.needs_argument:
        console.print(f"usage: {parsed.spec.usage}", markup=False)
        return None
    if action in (SlashAction.MODEL, SlashAction.URL):
        key = "model" if action is SlashAction.MODEL else "base_url"
        try:
            write_config_value(Path.cwd(), "llm", key, parsed.argument)
        except (ValueError, TypeError, tomllib.TOMLDecodeError) as exc:
            console.print(str(exc), markup=False)
            return None
        console.print(f"llm.{key} = {parsed.argument}", markup=False)
        return "reload"
    if action is SlashAction.KEY:
        secret = typer.prompt("API key", hide_input=True)
        try:
            cleaned = _clean_api_key(secret)
        except ValueError as exc:
            console.print(str(exc), markup=False)
            return None
        provider = load_project_config(Path.cwd()).llm.provider
        CredentialStore().set_key(provider, cleaned)
        console.print(f"{provider} key stored", markup=False)
        return "reload"
    if action is SlashAction.CONFIG:
        config_read()
        return None
    if action is SlashAction.STATUS:
        provider = load_project_config(Path.cwd()).llm.provider
        console.print_json(CredentialStore().status(provider).model_dump_json())
        return None
    raise AssertionError(f"unhandled slash action: {action}")
```

- [ ] **Step 6: 运行 CLI 兼容回归**

运行：

```powershell
uv run pytest tests/test_interactive.py tests/test_cli_commands.py -q
```

预期：全部 PASS；既有 `/model`、`/url`、引号剥离、`/help`、未知命令和 `/exit` 行为无回归。

- [ ] **Step 7: 提交 Task 1**

```powershell
git add src/phycode/interactive.py src/phycode/cli.py tests/test_interactive.py
git commit -m "refactor(cli): centralize slash command metadata [slash_registry_impl]"
```

提交报告必须包含有效 RED、聚焦 GREEN、CLI 回归结果和 commit hash。随后执行 spec 合规复审和代码质量复审；问题修复完成前不得开始 Task 2。

---

### Task 2: 实时补全、模糊排序与会话模型缓存

**Files:**
- Modify: `pyproject.toml:7-23`
- Modify: `uv.lock`
- Modify: `src/phycode/interactive.py`
- Modify: `tests/test_interactive.py`

**Interfaces:**
- Consumes: Task 1 的 `SLASH_COMMANDS`、`SlashCommandSpec.usage`、`resolve_slash_command()` 和 `parse_slash()`。
- Produces: `SessionModelCatalog(loader: Callable[[], list[str]])`、`SessionModelCatalog.get_models() -> tuple[str, ...]`、`SessionModelCatalog.refresh() -> None`、`SlashCompleter(catalog: SessionModelCatalog)`。

- [ ] **Step 1: 通过 uv 增加直接依赖**

运行：

```powershell
uv add "prompt-toolkit>=3.0.52,<4"
```

预期：`pyproject.toml` 出现精确约束，`uv.lock` 锁定 prompt-toolkit 3.x；不得使用 `pip`。

- [ ] **Step 2: 写补全与缓存 RED 测试**

向 `tests/test_interactive.py` 增加：

```python
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from phycode.interactive import SessionModelCatalog, SlashCompleter


def _completions(text: str, completer: SlashCompleter):
    return list(
        completer.get_completions(
            Document(text=text, cursor_position=len(text)),
            CompleteEvent(completion_requested=True),
        )
    )


def test_command_completion_lists_filters_and_ranks_canonical_commands():
    completer = SlashCompleter(SessionModelCatalog(lambda: []))
    all_commands = _completions("/", completer)
    assert [item.display_text for item in all_commands] == [spec.usage for spec in SLASH_COMMANDS]

    filtered = _completions("/mo", completer)
    assert [item.display_text for item in filtered] == ["/model <name>", "/models"]
    assert [item.text for item in filtered] == ["/model ", "/models"]
    assert [item.display_meta_text for item in filtered] == ["切换当前模型", "列出当前凭据可用的模型"]

    assert [item.display_text for item in _completions("/mdl", completer)] == ["/model <name>", "/models"]
    assert [item.display_text for item in _completions("/lo", completer)] == ["/key"]
    assert _completions("please /model", completer) == []


def test_model_completion_is_cached_filtered_and_refreshable():
    calls = 0

    def load_models() -> list[str]:
        nonlocal calls
        calls += 1
        return ["deepseek-chat", "deepseek-reasoner", "kimi-k2", "deepseek-chat"]

    catalog = SessionModelCatalog(load_models)
    completer = SlashCompleter(catalog)
    assert [item.text for item in _completions("/model deep", completer)] == [
        "deepseek-chat",
        "deepseek-reasoner",
    ]
    _completions("/model d", completer)
    assert calls == 1
    catalog.refresh()
    _completions("/model d", completer)
    assert calls == 2


def test_completion_menu_never_returns_more_than_eight_visible_rows():
    catalog = SessionModelCatalog(lambda: [f"model-{index:02d}" for index in range(20)])
    assert len(_completions("/model ", SlashCompleter(catalog))) == 8


def test_model_completion_failure_is_generic_and_manual_values_remain_valid():
    def fail() -> list[str]:
        raise RuntimeError("endpoint=https://private.example key=sk-secret1234567890")

    catalog = SessionModelCatalog(fail)
    completer = SlashCompleter(catalog)
    assert _completions("/model ", completer) == []
    assert catalog.status == "模型列表暂不可用；可以手工输入模型名"
    assert "private.example" not in catalog.status
    assert "sk-secret" not in catalog.status
    assert parse_slash("/model manually-entered").needs_argument is False
    assert _completions("/url ", completer) == []
    assert _completions("/key ", completer) == []
```

- [ ] **Step 3: 运行补全测试，确认有效 RED**

运行：

```powershell
uv run pytest tests/test_interactive.py -k "completion or cached" -q
```

预期：收集失败或测试失败，明确指出 `SessionModelCatalog` / `SlashCompleter` 尚不存在；不得因 prompt-toolkit 安装失败而红。

- [ ] **Step 4: 写最小缓存和模糊匹配实现**

在 `src/phycode/interactive.py` 增加 prompt-toolkit imports、线程安全会话缓存和稳定排序：

```python
from collections.abc import Callable, Iterator
from threading import Lock

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document


class SessionModelCatalog:
    def __init__(self, loader: Callable[[], list[str]]) -> None:
        self._loader = loader
        self._lock = Lock()
        self._loaded = False
        self._models: tuple[str, ...] = ()
        self.status = ""

    def get_models(self) -> tuple[str, ...]:
        with self._lock:
            if self._loaded:
                return self._models
            try:
                values = self._loader()
            except Exception:
                self._models = ()
                self.status = "模型列表暂不可用；可以手工输入模型名"
            else:
                self._models = tuple(
                    dict.fromkeys(value.strip() for value in values if value.strip())
                )
                self.status = "" if self._models else "未返回模型；可以手工输入模型名"
            self._loaded = True
            return self._models

    def refresh(self) -> None:
        with self._lock:
            self._loaded = False
            self._models = ()
            self.status = ""


def _subsequence_score(query: str, candidate: str) -> tuple[int, int, int] | None:
    query = query.casefold()
    candidate = candidate.casefold()
    if not query:
        return (0, 0, 0)
    if candidate.startswith(query):
        return (0, 0, len(candidate))
    positions: list[int] = []
    start = 0
    for character in query:
        position = candidate.find(character, start)
        if position < 0:
            return None
        positions.append(position)
        start = position + 1
    return (1, positions[-1] - positions[0], positions[0])


def _ranked_commands(query: str) -> list[SlashCommandSpec]:
    ranked: list[tuple[tuple[int, int, int, int, int], SlashCommandSpec]] = []
    for index, spec in enumerate(SLASH_COMMANDS):
        canonical = _subsequence_score(query, spec.name)
        aliases = [
            score
            for alias in spec.aliases
            if (score := _subsequence_score(query, alias)) is not None
        ]
        candidates: list[tuple[int, tuple[int, int, int]]] = []
        if canonical is not None:
            candidates.append((0 if canonical[0] == 0 else 2, canonical))
        candidates.extend((1 if score[0] == 0 else 3, score) for score in aliases)
        if candidates:
            source_rank, score = min(candidates)
            ranked.append(((source_rank, *score, index), spec))
    return [spec for _, spec in sorted(ranked, key=lambda item: item[0])]


class SlashCompleter(Completer):
    def __init__(self, catalog: SessionModelCatalog) -> None:
        self.catalog = catalog

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterator[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/") or "\n" in text:
            return
        body = text[1:]
        if " " not in body:
            for spec in _ranked_commands(body)[:8]:
                insertion = f"/{spec.name}{' ' if spec.argument else ''}"
                yield Completion(
                    insertion,
                    start_position=-len(text),
                    display=spec.usage,
                    display_meta=spec.description,
                )
            return
        raw_name, partial = body.split(" ", 1)
        spec = resolve_slash_command(raw_name)
        if not spec or not spec.argument or spec.argument.completion_source != "models":
            return
        ranked_models = []
        for index, model in enumerate(self.catalog.get_models()):
            score = _subsequence_score(partial, model)
            if score is not None:
                ranked_models.append(((*score, index), model))
        for _, model in sorted(ranked_models)[:8]:
            yield Completion(
                model,
                start_position=-len(partial),
                display=model,
                display_meta="可用模型",
            )
```

删除未使用 imports；不得把原始 loader 异常保存到对象、日志或 CLI。

- [ ] **Step 5: 运行补全测试，确认 GREEN**

运行：

```powershell
uv run pytest tests/test_interactive.py -q
```

预期：Task 1 与 Task 2 测试全部 PASS；loader 只调用一次，refresh 后才调用第二次。

- [ ] **Step 6: 运行类型与依赖检查**

运行：

```powershell
uv tree | Select-String "prompt-toolkit"
uvx pyright src/phycode/interactive.py tests/test_interactive.py
```

预期：依赖树显示 prompt-toolkit 3.x；Pyright 为 0 errors / 0 warnings。

- [ ] **Step 7: 提交 Task 2**

```powershell
git add pyproject.toml uv.lock src/phycode/interactive.py tests/test_interactive.py
git commit -m "feat(cli): add slash completion engine [slash_completion_impl]"
```

提交报告必须包含依赖解析、有效 RED、GREEN、Pyright 和 commit hash。完成两阶段复审后才能开始 Task 3。

---

### Task 3: 真实终端输入、键盘生命周期与 CLI 接线

**Files:**
- Modify: `src/phycode/interactive.py`
- Modify: `src/phycode/cli.py:123-139,255-277`
- Modify: `tests/test_interactive.py`
- Modify: `tests/test_cli_commands.py:60-161,404-414`

**Interfaces:**
- Consumes: Task 2 的 `SlashCompleter`、`SessionModelCatalog` 和 Task 1 的 `parse_slash()`。
- Produces: `ChatPrompt` protocol、`BasicPrompt`、`InteractivePrompt`、`create_chat_prompt(model_loader, fallback_reader, force_interactive=None) -> ChatPrompt`、`ChatPrompt.read() -> str`、`ChatPrompt.refresh_models() -> None`；CLI 新增 `_list_model_ids() -> list[str]`。

- [ ] **Step 1: 写真实按键和输入端口 RED 测试**

向 `tests/test_interactive.py` 增加：

```python
from threading import Thread
from time import sleep

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.formatted_text.utils import fragment_list_to_text

from phycode.interactive import BasicPrompt, InteractivePrompt, create_chat_prompt


def test_enter_executes_complete_no_argument_command():
    with create_pipe_input() as pipe:
        prompt = InteractivePrompt(lambda: ["deepseek-chat"], input=pipe, output=DummyOutput())
        pipe.send_text("/he\r")
        assert prompt.read() == "/help"


def test_enter_accepts_required_command_then_waits_for_argument():
    with create_pipe_input() as pipe:
        prompt = InteractivePrompt(lambda: ["deepseek-chat"], input=pipe, output=DummyOutput())
        pipe.send_text("/mo\rmanually-entered\r")
        assert prompt.read() == "/model manually-entered"


def test_tab_accepts_completion_without_submitting():
    with create_pipe_input() as pipe:
        prompt = InteractivePrompt(lambda: ["deepseek-chat"], input=pipe, output=DummyOutput())
        pipe.send_text("/mo\tmanual-model\r")
        assert prompt.read() == "/model manual-model"


def test_down_arrow_changes_the_selected_command():
    with create_pipe_input() as pipe:
        prompt = InteractivePrompt(lambda: [], input=pipe, output=DummyOutput())

        def send_keys() -> None:
            pipe.send_text("/")
            sleep(0.1)
            pipe.send_bytes(b"\x1b[B\x1b[B")
            pipe.send_text("\rhttps://example.com/v1\r")

        sender = Thread(target=send_keys)
        sender.start()
        assert prompt.read() == "/url https://example.com/v1"
        sender.join(timeout=1)


def test_escape_closes_menu_and_preserves_text():
    with create_pipe_input() as pipe:
        prompt = InteractivePrompt(lambda: [], input=pipe, output=DummyOutput())

        def send_keys() -> None:
            pipe.send_text("/mo")
            sleep(0.05)
            pipe.send_bytes(b"\x1b")
            sleep(0.2)
            pipe.send_text("x\r")

        sender = Thread(target=send_keys)
        sender.start()
        assert prompt.read() == "/mox"
        sender.join(timeout=1)


def test_ctrl_c_cancels_and_ctrl_d_exits_empty_prompt():
    with create_pipe_input() as pipe:
        prompt = InteractivePrompt(lambda: [], input=pipe, output=DummyOutput())
        pipe.send_bytes(b"\x03")
        with pytest.raises(KeyboardInterrupt):
            prompt.read()
        pipe.send_bytes(b"\x04")
        with pytest.raises(EOFError):
            prompt.read()


def test_non_tty_factory_uses_basic_prompt():
    prompt = create_chat_prompt(lambda: [], lambda: "/exit", force_interactive=False)
    assert isinstance(prompt, BasicPrompt)
    assert prompt.read() == "/exit"


def test_bottom_toolbar_tracks_selected_usage_and_parameter_example():
    with create_pipe_input() as pipe:
        prompt = InteractivePrompt(lambda: [], input=pipe, output=DummyOutput())
        prompt._session.default_buffer.text = "/mo"
        assert "/model <name>" in fragment_list_to_text(prompt._bottom_toolbar())
        prompt._session.default_buffer.text = "/url "
        toolbar = fragment_list_to_text(prompt._bottom_toolbar())
        assert "/url <base_url>" in toolbar
        assert "https://example.com/v1" in toolbar
```

如果 `create_pipe_input()` 的具体返回类型使静态类型检查不接受上述通用 helper，可把 context manager 直接写进各测试；不得用 `Any` 或跳过测试掩盖真实类型问题。

- [ ] **Step 2: 运行按键测试，确认有效 RED**

运行：

```powershell
uv run pytest tests/test_interactive.py -k "enter or tab or arrow or escape or ctrl or non_tty or toolbar" -q
```

预期：测试收集失败或断言失败，明确指出 `InteractivePrompt` / `BasicPrompt` / `create_chat_prompt` 尚不存在。

- [ ] **Step 3: 实现输入端口、菜单样式与键绑定**

在 `src/phycode/interactive.py` 增加：

```python
import sys
from collections.abc import Callable
from typing import Protocol

from prompt_toolkit import PromptSession
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completion
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.output import Output
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.styles import Style


class ChatPrompt(Protocol):
    def read(self) -> str: ...
    def refresh_models(self) -> None: ...


class BasicPrompt:
    def __init__(self, reader: Callable[[], str]) -> None:
        self._reader = reader

    def read(self) -> str:
        return self._reader()

    def refresh_models(self) -> None:
        return None


def _selected_or_first(buffer: Buffer) -> Completion | None:
    state = buffer.complete_state
    if state is None or not state.completions:
        return None
    return state.current_completion or state.completions[0]


class InteractivePrompt:
    def __init__(
        self,
        model_loader: Callable[[], list[str]],
        *,
        input: Input | None = None,
        output: Output | None = None,
    ) -> None:
        self.catalog = SessionModelCatalog(model_loader)
        self.completer = SlashCompleter(self.catalog)
        bindings = KeyBindings()

        @bindings.add("tab")
        def accept_completion(event: KeyPressEvent) -> None:
            completion = self._completion_for_buffer(event.current_buffer)
            if completion is None:
                event.current_buffer.start_completion(select_first=True)
                return
            event.current_buffer.apply_completion(completion)

        @bindings.add("enter")
        def accept_or_submit(event: KeyPressEvent) -> None:
            buffer = event.current_buffer
            completion = self._completion_for_buffer(buffer)
            if completion is not None:
                buffer.apply_completion(completion)
            text = buffer.text
            if text.startswith("/") and parse_slash(text).needs_argument:
                buffer.start_completion(select_first=False)
                return
            buffer.validate_and_handle()

        @bindings.add("escape")
        def close_menu(event: KeyPressEvent) -> None:
            event.current_buffer.cancel_completion()

        self._session: PromptSession[str] = PromptSession(
            input=input,
            output=output,
            completer=self.completer,
            complete_while_typing=True,
            complete_in_thread=True,
            complete_style=CompleteStyle.COLUMN,
            reserve_space_for_menu=8,
            key_bindings=bindings,
            history=InMemoryHistory(),
            editing_mode=EditingMode.EMACS,
            style=Style.from_dict(
                {
                    "prompt": "ansigreen bold",
                    "completion-menu.completion.current": "bg:#1f6feb #ffffff",
                    "completion-menu.meta.completion.current": "bg:#1f6feb #dbeafe",
                    "bottom-toolbar": "bg:#161b22 #a0a0a0",
                }
            ),
            bottom_toolbar=self._bottom_toolbar,
        )

    def _completion_for_buffer(self, buffer: Buffer) -> Completion | None:
        completion = _selected_or_first(buffer)
        if completion is not None:
            return completion
        text = buffer.text
        if not text.startswith("/") or " " in text[1:]:
            return None
        return next(
            self.completer.get_completions(
                Document(text=text, cursor_position=len(text)),
                CompleteEvent(completion_requested=True),
            ),
            None,
        )

    def _bottom_toolbar(self) -> FormattedText:
        buffer = self._session.default_buffer
        text = buffer.text
        if not text.startswith("/"):
            return FormattedText([])
        body = text[1:].split(maxsplit=1)
        completion = self._completion_for_buffer(buffer)
        if completion is not None and completion.text.startswith("/"):
            selected_name = completion.text[1:].strip().split(maxsplit=1)[0]
            spec = resolve_slash_command(selected_name)
        else:
            spec = resolve_slash_command(body[0]) if body else None
        if spec is None:
            return FormattedText([("class:bottom-toolbar", "↑↓ 选择 · Tab 补全 · Enter 执行 · Esc 关闭")])
        detail = f"用法：{spec.usage} · {spec.description}"
        if spec.action is SlashAction.URL and spec.argument:
            detail += f" · 示例：{spec.argument.example}"
        if spec.action is SlashAction.MODEL and self.catalog.status:
            detail += f" · {self.catalog.status}"
        return FormattedText([("class:bottom-toolbar", detail)])

    def read(self) -> str:
        return self._session.prompt([("class:prompt", "phycode › ")])

    def refresh_models(self) -> None:
        self.catalog.refresh()


def create_chat_prompt(
    model_loader: Callable[[], list[str]],
    fallback_reader: Callable[[], str],
    *,
    force_interactive: bool | None = None,
) -> ChatPrompt:
    interactive = (
        sys.stdin.isatty() and sys.stdout.isatty()
        if force_interactive is None
        else force_interactive
    )
    if interactive:
        return InteractivePrompt(model_loader)
    return BasicPrompt(fallback_reader)
```

- [ ] **Step 4: 运行按键测试，确认 GREEN**

运行：

```powershell
uv run pytest tests/test_interactive.py -q
```

预期：所有注册表、补全、缓存和键盘测试 PASS；测试在 5 秒内完成且无后台线程泄漏。

- [ ] **Step 5: 写 CLI 生命周期 RED 测试**

向 `tests/test_cli_commands.py` 增加 fake prompt，证明 Ctrl+C 不退出且 reload/模型列表会刷新候选：

```python
def test_chat_prompt_ctrl_c_recovers_and_reload_refreshes_models(tmp_path, monkeypatch):
    import phycode.cli as cli

    monkeypatch.chdir(tmp_path)
    _force_no_credentials(monkeypatch)

    class FakePrompt:
        def __init__(self) -> None:
            self.inputs = iter([KeyboardInterrupt(), "/model manual-model", "/exit"])
            self.refresh_count = 0

        def read(self) -> str:
            value = next(self.inputs)
            if isinstance(value, BaseException):
                raise value
            return value

        def refresh_models(self) -> None:
            self.refresh_count += 1

    prompt = FakePrompt()
    monkeypatch.setattr(cli, "create_chat_prompt", lambda *a, **k: prompt)

    result = runner.invoke(app, ["chat"])

    assert result.exit_code == 0, result.stdout
    assert "llm.model = manual-model" in result.stdout
    assert prompt.refresh_count == 1
```

- [ ] **Step 6: 运行 CLI 生命周期测试，确认有效 RED**

运行：

```powershell
uv run pytest tests/test_cli_commands.py::test_chat_prompt_ctrl_c_recovers_and_reload_refreshes_models -q
```

预期：FAIL，明确显示 `chat()` 仍直接调用 `typer.prompt()` 或没有 refresh；不得因 fake agent 或 keyring 访问失败。

- [ ] **Step 7: 接入 CLI 并复用模型列表函数**

在 `src/phycode/cli.py` 把模型获取与输出拆开：

```python
def _list_model_ids() -> list[str]:
    llm = _build_llm(load_project_config(Path.cwd()))
    lister = getattr(llm, "list_models", None)
    if lister is None:
        raise RuntimeError("No provider key configured. Run 'phycode keys set' (or /key in chat) first.")
    return [str(model_id) for model_id in lister()]


def _print_models() -> bool:
    try:
        model_ids = _list_model_ids()
    except Exception as exc:
        _safe_print(f"[error] {redact_text(str(exc))}", style="red", markup=False)
        return False
    for model_id in model_ids:
        console.print(model_id, markup=False)
    return True
```

导入 `create_chat_prompt`，并把 `chat()` 的输入边界改为：

```python
prompt = create_chat_prompt(_list_model_ids, lambda: typer.prompt("phycode"))
while True:
    try:
        user_input = prompt.read()
    except KeyboardInterrupt:
        _safe_print("^C", style="dim", markup=False)
        continue
    except EOFError:
        return
    if user_input.startswith("/"):
        action = _handle_slash(user_input)
        if action == "exit":
            return
        if action in ("reload", "refresh_models"):
            prompt.refresh_models()
        if action == "reload":
            loop = build_agent(
                SessionMode.INTERACTIVE,
                approval_handler=_interactive_approver,
                event_sink=_render_agent_event,
            )
        continue
    # 保留现有 _run_turn()、最终文本和 stopped_reason 处理
```

- [ ] **Step 8: 运行 CLI 与审批回归**

运行：

```powershell
uv run pytest tests/test_interactive.py tests/test_cli_commands.py -q
uv run pytest tests/test_cli_smoke.py -q
```

预期：全部 PASS；非 TTY `CliRunner`、spinner 感知审批、配置重载、隐藏 key 和普通多轮 chat 无回归。

- [ ] **Step 9: 运行 Pyright 并提交 Task 3**

运行：

```powershell
uvx pyright
```

预期：0 errors / 0 warnings。

提交：

```powershell
git add src/phycode/interactive.py src/phycode/cli.py tests/test_interactive.py tests/test_cli_commands.py
git commit -m "feat(cli): add interactive slash command menu [interactive_prompt_impl]"
```

完成两阶段复审，尤其检查 Rich spinner/审批与 PromptSession 不会同时控制终端、异常不泄漏 URL/key、Ctrl+C 不杀死会话。

---

### Task 4: 文档合同、完整门禁与真实 API 验收

**Files:**
- Modify: `tests/test_docs_process.py`
- Modify: `README.md:177-190`
- Modify: `SPEC.md:52-77,326-355`
- Modify: `PLAN.md`
- Modify: `AGENT_LOG.md`

**Interfaces:**
- Consumes: Task 1–3 的最终用户行为和实际 commit hash。
- Produces: 可审计的中文使用说明、SPEC 验收合同、Task 28–31 过程记录、Windows/WSL/构建门禁证据和一次不落盘的真实供应商 smoke。

- [ ] **Step 1: 写文档合同 RED 测试**

向 `tests/test_docs_process.py` 增加：

```python
def test_docs_specify_interactive_slash_completion_contract():
    readme = _read("README.md")
    spec = _read("SPEC.md")
    for text in (
        "输入 `/`",
        "实时过滤",
        "↑/↓",
        "Tab",
        "Enter",
        "Esc",
        "`/model `",
        "真实模型候选",
        "非 TTY",
    ):
        assert text in readme
    for text in (
        "斜杠命令候选",
        "参数提示",
        "非 TTY",
        "prompt_toolkit",
    ):
        assert text in spec
```

- [ ] **Step 2: 运行文档测试，确认有效 RED**

运行：

```powershell
uv run pytest tests/test_docs_process.py::test_docs_specify_interactive_slash_completion_contract -q
```

预期：FAIL，指出 README/SPEC 尚缺候选、键盘和非 TTY 合同。

- [ ] **Step 3: 更新 README 与 SPEC，使合同 GREEN**

在 README 的 chat 斜杠命令段落明确写入：

```markdown
在真实终端中，输入 `/` 会立即展示全部候选；继续输入会实时过滤。候选同时显示命令用法、参数占位和说明。使用 ↑/↓ 选择、Tab 补全、Enter 执行、Esc 关闭菜单；Ctrl+C 取消当前输入，Ctrl+D 在空输入时退出。输入 `/model ` 后会用当前安全凭据加载真实模型候选，加载失败时仍可手工输入模型 ID。`/key` 始终进入独立隐藏输入，不显示或补全 key。非 TTY、重定向输入和测试管道自动使用整行输入回退。
```

在 `SPEC.md` 5.1 行为、6.3 可用性和模块列表中分别加入：

```markdown
- 真实 TTY 输入 `/` 时展示斜杠命令候选并随输入实时过滤；候选展示参数提示和说明，支持方向键、Tab、Enter 与 Esc。
- `/model ` 使用会话级缓存展示真实供应商模型 ID；失败时允许手工输入。`/key` 不进入普通历史或补全。
- 非 TTY 与重定向输入保持整行读取兼容路径。
```

并把模块列表中的 CLI 描述拆为：

```markdown
- `interactive`：基于 prompt_toolkit 的 TTY 输入、斜杠补全、参数提示和非 TTY 回退。
- `cli`：Typer 命令、Rich 渲染、斜杠副作用分发和审批提示。
```

重新运行聚焦文档测试，预期 PASS。

- [ ] **Step 4: 更新过程证据并收集实现提交身份**

运行以下命令取得实际提交身份：

```powershell
git log --reverse --format="%h %s" main..HEAD
```

在 `AGENT_LOG.md` 新增按时间顺序的 Task 28–31 记录，包含：

- Claude Code/OpenCode 一手资料与用户选择的双层候选设计。
- `prompt-toolkit` 方案胜出、手写终端和完整 TUI 被拒绝的原因。
- 每个有效 RED 的确切失败原因、GREEN 命令和两阶段 reviewer 结论。
- 主 agent 真实终端/API smoke 的模型候选与完成状态，但不写模型完整响应、URL 或 key。
- 未修改 AgentLoop、审批、策略、工具权限或凭据存储的范围声明。

- [ ] **Step 5: Windows 完整门禁**

依次运行：

```powershell
uv run pytest -q
uvx pyright
uv build
git diff --check
```

预期：pytest 完整进度达到 100% 且 exit 0；Pyright 为 0 errors / 0 warnings；`uv build` 成功生成当前 `0.1.1` wheel/sdist；diff check 无输出。此任务不自行升级版本或发布 Release。

- [ ] **Step 6: WSL/Linux 兼容门禁**

从当前 worktree 根目录执行：

```powershell
$worktreePath = (Get-Location).Path
$wslPath = (wsl.exe wslpath -a $worktreePath).Trim()
wsl.exe bash -lc "cd '$wslPath' && CI=true GITHUB_ACTIONS=true uv run pytest -q"
```

预期：WSL 全量测试达到 100% 且 exit 0；prompt-toolkit 的 DummyOutput/pipe 测试不依赖 Windows 专用行为。

- [ ] **Step 7: 主 agent 执行真实终端与真实 API smoke**

先运行只显示存在性的安全检查：

```powershell
uv run phycode keys status openai-compatible
```

预期：`configured` 为 `true`，且输出不含 key。若已配置则不得再次读取明文凭据文件；若未配置，由主 agent 停止并请求用户在真实终端运行 `/key`，subagent 不得接触凭据。

随后在真实 PTY 中运行：

```powershell
uv run phycode chat
```

人工验证顺序：

1. 输入 `/`，不按回车，确认八个规范候选立即出现。
2. 输入 `mo`，确认只剩 `/model <name>` 与 `/models`，并显示说明和完整用法。
3. 接受 `/model `，确认真实供应商模型候选出现；选择当前配置模型或一个明确可用模型。
4. 输入“只回复 PHYCODE_LIVE_OK”，确认供应商真实返回非 Echo 的 `PHYCODE_LIVE_OK`。
5. 输入 `/exit` 正常退出。

不得把 PTY transcript、真实响应、URL 或 key 重定向到仓库文件。若模型枚举失败但真实对话成功，任务仍不通过：必须诊断并修复 `/model` 候选路径。

- [ ] **Step 8: 凭据与工作区扫描**

主 agent 在不输出明文值的进程内，把已授权凭据源中的 URL/key 作为固定字符串扫描当前 worktree、构建物和 Git 历史；只输出命中计数与文件名，不输出匹配行。预期 URL/key 命中均为 0。另运行：

```powershell
git status --short --branch
```

预期：只有本任务计划内的文档/测试变更；用户未跟踪 `AGENTS.md` 仍未暂存。

- [ ] **Step 9: 提交 Task 4**

```powershell
git add README.md SPEC.md AGENT_LOG.md tests/test_docs_process.py
git commit -m "docs(cli): document interactive slash completion [root]"
```

提交后取得该提交的实际 hash，并把 Task 1–3 的实现 hash 与本提交 hash 原样写入 `PLAN.md` 新章节“2026-07-19 斜杠命令实时补全（Task 28–31）”。Task 28–30 对应三个实现提交，Task 31 对应本步骤的文档与真实验收提交；不得写占位 hash。

- [ ] **Step 10: 提交可追溯收尾记录并复验**

运行：

```powershell
git log --reverse --format="%h %s" main..HEAD
git add PLAN.md AGENT_LOG.md
git commit -m "docs: close slash completion task [root]"
```

收尾提交后重跑：

```powershell
uv run pytest tests/test_docs_process.py -q
git status --short --branch
```

预期：文档测试全部 PASS；工作区只保留用户拥有的未跟踪 `AGENTS.md`。随后进行 whole-branch 最终 review；只有 Critical、Important、Minor 均关闭且 reviewer 判定 Ready to merge，才进入 finishing-a-development-branch。

## 自我审查

- 规约覆盖：候选显示、模糊过滤、参数占位、键盘操作、动态模型、失败回退、敏感 key、非 TTY、Ctrl+C/Ctrl+D、帮助漂移、Windows/WSL 和真实 API 均对应到具体任务与测试。
- 范围控制：不触碰 AgentLoop、策略、工具、审批语义、trace 或凭据存储；不引入完整 TUI、`@`、`!` 或持久历史。
- 类型一致性：Task 1 定义的 `SlashAction` / spec / parser 被 Task 2 completer 和 Task 3 prompt 直接复用；Task 2 的 `SessionModelCatalog.refresh()` 满足 Task 3 `ChatPrompt.refresh_models()` 接线。
- 安全一致性：模型 loader 原始异常从不进入缓存状态；真实凭据只由主 agent 在最终 smoke 使用，默认测试与 subagent 均不读取。
- 红旗扫描：计划不含占位标记、未定义函数或模糊的延后处理；所有实现步骤给出精确接口、代码、命令与预期结果。
