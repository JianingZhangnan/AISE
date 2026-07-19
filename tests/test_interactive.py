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
