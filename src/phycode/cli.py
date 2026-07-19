import json
import tempfile
import tomllib
from pathlib import Path

import typer
from rich.console import Console

from phycode import __version__
from phycode.agent import AgentLoop
from phycode.composition import (
    build_agent as _compose_agent,
    build_default_registry,
    build_llm as _build_llm,
)
from phycode.config import load_project_config, write_config_value
from phycode.credentials import CredentialStore
from phycode.demos import run_feedback_demo, run_guardrail_demo, run_policy_demo
from phycode.interactive import SlashAction, parse_slash, render_slash_help
from phycode.models import (
    AgentEvent,
    AgentEventType,
    AgentProfile,
    PolicyDecision,
    SessionMode,
    ToolCall,
)
from phycode.prbench_eval import prbench_app
from phycode.redaction import redact_text

app = typer.Typer(help="PhyCode coding agent harness")
tools_app = typer.Typer(help="Inspect registered tools")
app.add_typer(tools_app, name="tools")
config_app = typer.Typer(help="Read and write non-sensitive configuration")
keys_app = typer.Typer(help="Manage provider credentials")
app.add_typer(config_app, name="config")
app.add_typer(keys_app, name="keys")
app.add_typer(prbench_app, name="prbench")
console = Console()

def _clean_api_key(secret: str) -> str:
    """Validate/normalize an API key: strip surrounding whitespace, require non-empty ASCII.

    Non-ASCII or invisible characters (e.g. a zero-width space pasted from a web page)
    would otherwise crash later as an 'ascii' codec error when the Authorization header
    is encoded, so reject them up front with a clear message.
    """
    cleaned = secret.strip()
    if not cleaned:
        raise ValueError("API key cannot be blank.")
    if not cleaned.isascii():
        raise ValueError(
            "API key contains non-ASCII or invisible characters. Re-copy just the key "
            "(no quotes, labels, or hidden spaces) and try again."
        )
    return cleaned


def _interactive_approver(call: ToolCall, decision: PolicyDecision) -> bool:
    """Prompt the user to approve a risky tool call in an interactive session."""
    console.print(f"[approval needed] {call.tool_name}: {decision.reason}", markup=False)
    return typer.confirm("Approve this action?", default=False)

def _safe_print(text: str, **kwargs) -> None:
    """console.print that never crashes on a legacy codepage (e.g. Windows GBK)."""
    try:
        console.print(text, **kwargs)
    except UnicodeEncodeError:
        console.print(str(text).encode("ascii", "replace").decode("ascii"), **kwargs)


def _render_agent_event(event: AgentEvent) -> None:
    """Render one loop event as a live activity line (final answer is printed by the caller).

    Markers are ASCII-only so they render on any console codepage.
    """
    payload = event.payload
    if event.type in (AgentEventType.ASSISTANT_COMMENTARY, AgentEventType.REASONING_SUMMARY):
        text = redact_text(str(payload.get("text", ""))).strip()
        if text:
            label = "thinking" if event.type == AgentEventType.REASONING_SUMMARY else "assistant"
            _safe_print(f"{label}: {text}", style="dim", markup=False)
    elif event.type == AgentEventType.TOOL_CALL_REQUESTED:
        args = json.dumps(payload.get("args", {}), ensure_ascii=False)
        _safe_print(f"-> {payload.get('tool_name', '')} {args}"[:200], style="cyan", markup=False)
    elif event.type == AgentEventType.POLICY_DECISION:
        if payload.get("decision") != "allow":  # allow is the common case; only surface ask/deny
            _safe_print(f"  policy: {payload.get('decision')} ({payload.get('rule_id')})", style="yellow", markup=False)
    elif event.type == AgentEventType.TOOL_CALL_OUTPUT:
        status = str(payload.get("status", ""))
        if status == "ok":
            _safe_print("  [ok]", style="green", markup=False)
        else:
            detail = redact_text(str(payload.get("stderr") or payload.get("stdout") or "")).strip().splitlines()
            _safe_print(f"  [!] {status}: {detail[0][:200] if detail else ''}", style="red", markup=False)
    elif event.type == AgentEventType.ERROR:
        _safe_print(f"[error] {redact_text(str(payload.get('message', '')))}", style="red", markup=False)


def _run_turn(loop: AgentLoop, text: str):
    """Run one turn, showing an ASCII spinner while the model works (only on a real terminal)."""
    if console.is_terminal:
        with console.status("thinking...", spinner="line") as status:
            original_approval_handler = loop.approval_handler
            if original_approval_handler is not None:

                def approval_with_visible_prompt(call: ToolCall, decision: PolicyDecision) -> bool:
                    status.stop()
                    try:
                        approved = original_approval_handler(call, decision)
                    except BaseException as approval_error:
                        try:
                            status.start()
                        except BaseException:
                            approval_error.add_note("The activity status could not be restarted.")
                        raise
                    else:
                        status.start()
                        return approved

                loop.approval_handler = approval_with_visible_prompt
            try:
                return loop.run(text)
            finally:
                loop.approval_handler = original_approval_handler
    return loop.run(text)


def _print_models() -> bool:
    """List the provider's available model ids. Returns False if unavailable."""
    llm = _build_llm(load_project_config(Path.cwd()))
    lister = getattr(llm, "list_models", None)
    if lister is None:
        console.print("No provider key configured. Run 'phycode keys set' (or /key in chat) first.", markup=False)
        return False
    try:
        model_ids = lister()
    except Exception as exc:
        _safe_print(f"[error] {redact_text(str(exc))}", style="red", markup=False)
        return False
    for model_id in model_ids:
        console.print(str(model_id), markup=False)
    return True


def build_agent(
    mode: SessionMode,
    llm=None,
    approval_handler=None,
    event_sink=None,
    profile: AgentProfile = AgentProfile.CODING,
    max_tool_calls: int | None = None,
) -> AgentLoop:
    """Build the profile-aware runtime and attach the optional CLI event renderer."""
    loop = _compose_agent(
        mode,
        llm=llm if llm is not None else _build_llm(load_project_config(Path.cwd())),
        approval_handler=approval_handler,
        profile=profile,
        max_tool_calls=max_tool_calls,
    )
    loop.event_sink = event_sink
    return loop


@app.command()
def version() -> None:
    """Print the PhyCode version."""
    console.print(f"phycode {__version__}")


@app.command()
def run(
    task: str = typer.Argument(..., help="Task to run non-interactively"),
    profile: AgentProfile = typer.Option(AgentProfile.CODING, help="Agent profile: coding or gaia"),
    max_tool_calls: int | None = typer.Option(None, min=1, help="Override the profile's tool-call budget"),
) -> None:
    """Run a task once. Uses the configured provider when a key is stored, else EchoLLM."""
    loop = build_agent(
        SessionMode.NON_INTERACTIVE,
        event_sink=_render_agent_event,
        profile=profile,
        max_tool_calls=max_tool_calls,
    )
    result = _run_turn(loop, task)
    if result.final_text is not None:
        _safe_print(redact_text(result.final_text), markup=False)
    if result.stopped_reason != "final":
        _safe_print(f"[stopped: {result.stopped_reason}]", markup=False)
        raise typer.Exit(code=1)


_CHAT_HELP = render_slash_help()


def _handle_slash(line: str) -> str | None:
    """Handle a /command typed in chat; return exit, reload, refresh_models, or None."""
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


@app.command()
def chat() -> None:
    """Start an interactive session. Uses the configured provider when a key is stored, else EchoLLM."""
    console.print("PhyCode interactive session. Type /help for commands, /exit to leave.")
    loop = build_agent(SessionMode.INTERACTIVE, approval_handler=_interactive_approver, event_sink=_render_agent_event)
    while True:
        user_input = typer.prompt("phycode")
        if user_input.startswith("/"):
            action = _handle_slash(user_input)
            if action == "exit":
                return
            if action == "reload":
                loop = build_agent(
                    SessionMode.INTERACTIVE, approval_handler=_interactive_approver, event_sink=_render_agent_event
                )
            continue
        result = _run_turn(loop, user_input)
        if result.final_text is not None:
            _safe_print(redact_text(result.final_text), markup=False)
        if result.stopped_reason != "final":
            # Report and keep the session alive rather than killing it on one bad turn.
            _safe_print(f"[stopped: {result.stopped_reason}]", markup=False)


@tools_app.command("list")
def list_tools() -> None:
    """List registered tools."""
    registry = build_default_registry()
    for spec in registry.list_specs():
        console.print(f"{spec.name}\t{spec.risk_level.value}\t{spec.description}")


@config_app.command("read")
def config_read() -> None:
    """Read project configuration."""
    config = load_project_config(Path.cwd())
    console.print_json(config.model_dump_json())


@config_app.command("set")
def config_set(section: str, key: str, value: str) -> None:
    """Set a non-sensitive config value in ./phycode.toml (e.g. config set llm base_url <url>)."""
    try:
        write_config_value(Path.cwd(), section, key, value)
    except (ValueError, TypeError, tomllib.TOMLDecodeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    console.print(f"set {section}.{key}", markup=False)


@keys_app.command("status")
def keys_status(provider: str = typer.Argument("openai-compatible")) -> None:
    """Show credential status for a provider."""
    status = CredentialStore().status(provider)
    console.print_json(status.model_dump_json())


@keys_app.command("set")
def keys_set(provider: str) -> None:
    """Store an API key for a provider."""
    secret = typer.prompt("API key", hide_input=True)
    try:
        cleaned = _clean_api_key(secret)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    CredentialStore().set_key(provider, cleaned)
    console.print(f"{provider} stored", markup=False)


@keys_app.command("clear")
def keys_clear(provider: str) -> None:
    """Clear an API key for a provider."""
    CredentialStore().clear_key(provider)
    console.print(f"{provider} cleared", markup=False)


@app.command()
def models() -> None:
    """List the model ids your configured provider/token exposes (GET /v1/models)."""
    if not _print_models():
        raise typer.Exit(code=1)


@app.command()
def demo(name: str = typer.Argument(..., help="guardrail | feedback | policy")) -> None:
    """Run a deterministic mock-LLM mechanism demo in an isolated temp workspace."""
    root = Path(tempfile.mkdtemp(prefix="phycode-demo-"))
    demos = {
        "guardrail": run_guardrail_demo,
        "feedback": run_feedback_demo,
        "policy": run_policy_demo,
    }
    runner = demos.get(name)
    if runner is None:
        console.print("Unknown demo. Use guardrail, feedback, or policy.")
        raise typer.Exit(code=2)
    console.print(runner(root))
