from __future__ import annotations

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from phycode.interactive import (
    SLASH_COMMANDS,
    SessionModelCatalog,
    SlashAction,
    SlashCompleter,
    parse_slash,
    render_slash_help,
    resolve_slash_command,
)


def _completions(text: str, completer: SlashCompleter):
    return list(
        completer.get_completions(
            Document(text=text, cursor_position=len(text)),
            CompleteEvent(completion_requested=True),
        )
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


def test_command_completion_lists_filters_and_ranks_canonical_commands():
    completer = SlashCompleter(SessionModelCatalog(lambda: []))
    all_commands = _completions("/", completer)
    assert [item.display_text for item in all_commands] == [
        spec.usage for spec in SLASH_COMMANDS
    ]

    filtered = _completions("/mo", completer)
    assert [item.display_text for item in filtered] == ["/model <name>", "/models"]
    assert [item.text for item in filtered] == ["/model ", "/models"]
    assert [item.display_meta_text for item in filtered] == [
        "切换当前模型",
        "列出当前凭据可用的模型",
    ]

    assert [item.display_text for item in _completions("/mdl", completer)] == [
        "/model <name>",
        "/models",
    ]
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
