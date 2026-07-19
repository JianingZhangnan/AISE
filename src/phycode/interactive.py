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
