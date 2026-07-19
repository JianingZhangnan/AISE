from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock, Thread
from time import monotonic, sleep

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text.utils import fragment_list_to_text
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenuControl
from prompt_toolkit.output import DummyOutput

from phycode.interactive import (
    BasicPrompt,
    InteractivePrompt,
    SLASH_COMMANDS,
    SessionModelCatalog,
    SlashAction,
    SlashCompleter,
    create_chat_prompt,
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


def test_parse_slash_marks_arguments_for_argumentless_commands_as_unexpected():
    assert parse_slash("/key accidental-secret").has_unexpected_argument is True
    assert parse_slash("/LOGIN accidental-secret").has_unexpected_argument is True
    assert parse_slash("/exit later").has_unexpected_argument is True
    assert parse_slash("/key").has_unexpected_argument is False
    assert parse_slash("/model manual-model").has_unexpected_argument is False


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


def test_model_catalog_refresh_returns_while_loader_is_in_flight():
    loader_started = Event()
    release_loader = Event()

    def load_models() -> list[str]:
        loader_started.set()
        assert release_loader.wait(timeout=2)
        return ["stale-model"]

    catalog = SessionModelCatalog(load_models)
    with ThreadPoolExecutor(max_workers=2) as executor:
        load_future = executor.submit(catalog.get_models)
        assert loader_started.wait(timeout=1)
        refresh_future = executor.submit(catalog.refresh)
        try:
            refresh_future.result(timeout=0.25)
        finally:
            release_loader.set()
        assert load_future.result(timeout=1) == ("stale-model",)


def test_model_catalog_refresh_starts_new_generation_without_stale_overwrite():
    first_loader_started = Event()
    release_first_loader = Event()
    calls_lock = Lock()
    calls = 0

    def load_models() -> list[str]:
        nonlocal calls
        with calls_lock:
            calls += 1
            call = calls
        if call == 1:
            first_loader_started.set()
            assert release_first_loader.wait(timeout=2)
            return ["stale-model"]
        return ["fresh-model"]

    catalog = SessionModelCatalog(load_models)
    with ThreadPoolExecutor(max_workers=3) as executor:
        old_generation = executor.submit(catalog.get_models)
        assert first_loader_started.wait(timeout=1)
        refresh_future = executor.submit(catalog.refresh)
        try:
            refresh_future.result(timeout=0.25)
            new_generation = executor.submit(catalog.get_models)
            assert new_generation.result(timeout=0.5) == ("fresh-model",)
        finally:
            release_first_loader.set()
        assert old_generation.result(timeout=1) == ("fresh-model",)

    assert catalog.get_models() == ("fresh-model",)
    assert calls == 2


def test_model_catalog_concurrent_first_access_is_single_flight():
    worker_count = 5
    callers_ready = Barrier(worker_count + 1)
    loader_started = Event()
    release_loader = Event()
    calls_lock = Lock()
    calls = 0

    def load_models() -> list[str]:
        nonlocal calls
        with calls_lock:
            calls += 1
        loader_started.set()
        assert release_loader.wait(timeout=2)
        return ["shared-model"]

    catalog = SessionModelCatalog(load_models)

    def get_models_together() -> tuple[str, ...]:
        callers_ready.wait(timeout=1)
        return catalog.get_models()

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(get_models_together) for _ in range(worker_count)]
        callers_ready.wait(timeout=1)
        assert loader_started.wait(timeout=1)
        try:
            assert calls == 1
        finally:
            release_loader.set()
        assert [future.result(timeout=1) for future in futures] == [
            ("shared-model",)
        ] * worker_count

    assert calls == 1


def test_model_completion_keeps_all_candidates_and_ninth_can_be_accepted():
    models = [f"model-{index:02d}" for index in range(10)]
    catalog = SessionModelCatalog(lambda: models)

    assert [
        item.text for item in _completions("/model ", SlashCompleter(catalog))
    ] == models

    with create_pipe_input() as pipe:
        prompt = InteractivePrompt(lambda: models, input=pipe, output=DummyOutput())
        menu_windows = {
            id(window): window
            for window in prompt._session.app.layout.find_all_windows()
            if type(window.content) is CompletionsMenuControl
        }
        assert len(menu_windows) == 1
        menu_window = next(iter(menu_windows.values()))
        menu_height = menu_window.height
        assert isinstance(menu_height, Dimension)
        assert menu_height.min == 1
        assert menu_height.max == 8

        with ThreadPoolExecutor(max_workers=1) as executor:
            read_future = executor.submit(prompt.read)
            try:
                pipe.send_text("/model ")
                deadline = monotonic() + 1
                while True:
                    state = prompt._session.default_buffer.complete_state
                    if state is not None and len(state.completions) > 8:
                        break
                    if monotonic() >= deadline:
                        pytest.fail("completion state did not retain more than eight models")
                    sleep(0.01)

                assert len(state.completions) == len(models)
                pipe.send_bytes(b"\x1b[B" * 9)
                pipe.send_text("\r")
                assert read_future.result(timeout=1) == "/model model-08"
            finally:
                if not read_future.done():
                    pipe.send_bytes(b"\x03")
                    try:
                        read_future.result(timeout=1)
                    except BaseException:
                        pass


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


def test_enter_executes_complete_no_argument_command():
    with create_pipe_input() as pipe:
        prompt = InteractivePrompt(
            lambda: ["deepseek-chat"], input=pipe, output=DummyOutput()
        )
        pipe.send_text("/he\r")
        assert prompt.read() == "/help"


def test_sensitive_slash_lines_are_filtered_from_history_and_arrow_recall():
    with create_pipe_input() as pipe:
        prompt = InteractivePrompt(lambda: [], input=pipe, output=DummyOutput())

        pipe.send_text("/KeY accidental-secret-one\r")
        assert prompt.read() == "/KeY accidental-secret-one"
        pipe.send_text("/LOGIN accidental-secret-two\r")
        assert prompt.read() == "/LOGIN accidental-secret-two"
        assert list(prompt._session.history.get_strings()) == []

        pipe.send_bytes(b"\x1b[A")
        pipe.send_text("ordinary chat\r")
        assert prompt.read() == "ordinary chat"
        assert list(prompt._session.history.get_strings()) == ["ordinary chat"]

        def recall_history() -> None:
            sleep(0.05)
            pipe.send_bytes(b"\x1b[A\r")

        sender = Thread(target=recall_history)
        sender.start()
        assert prompt.read() == "ordinary chat"
        sender.join(timeout=1)
        assert not sender.is_alive()


def test_enter_accepts_required_command_then_waits_for_argument():
    with create_pipe_input() as pipe:
        prompt = InteractivePrompt(
            lambda: ["deepseek-chat"], input=pipe, output=DummyOutput()
        )
        pipe.send_text("/mo\rmanually-entered\r")
        assert prompt.read() == "/model manually-entered"


def test_tab_accepts_completion_without_submitting():
    with create_pipe_input() as pipe:
        prompt = InteractivePrompt(
            lambda: ["deepseek-chat"], input=pipe, output=DummyOutput()
        )
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
