from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Literal

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document


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
        if folded == spec.name.casefold() or folded in {
            alias.casefold() for alias in spec.aliases
        }:
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
    lines.extend(
        f"  {spec.usage:<{width}}  {spec.description}" for spec in SLASH_COMMANDS
    )
    return "\n".join(lines)


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
