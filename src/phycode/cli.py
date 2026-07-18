import tempfile
from pathlib import Path

import typer
from rich.console import Console

from phycode import __version__
from phycode.composition import build_agent, build_default_registry, build_llm as _build_llm
from phycode.config import load_project_config
from phycode.credentials import CredentialStore
from phycode.demos import run_feedback_demo, run_guardrail_demo, run_policy_demo
from phycode.models import AgentProfile, PolicyDecision, SessionMode, ToolCall
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


def _interactive_approver(call: ToolCall, decision: PolicyDecision) -> bool:
    """Prompt the user to approve a risky tool call in an interactive session."""
    console.print(f"[approval needed] {call.tool_name}: {decision.reason}", markup=False)
    return typer.confirm("Approve this action?", default=False)


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
    result = build_agent(SessionMode.NON_INTERACTIVE, profile=profile, max_tool_calls=max_tool_calls).run(task)
    if result.final_text is not None:
        console.print(redact_text(result.final_text), markup=False)
    if result.stopped_reason != "final":
        raise typer.Exit(code=1)


@app.command()
def chat() -> None:
    """Start an interactive session. Uses the configured provider when a key is stored, else EchoLLM."""
    console.print("PhyCode interactive session. Type /exit to leave.")
    loop = build_agent(SessionMode.INTERACTIVE, approval_handler=_interactive_approver)
    while True:
        user_input = typer.prompt("phycode")
        if user_input == "/exit":
            return
        result = loop.run(user_input)
        if result.final_text is not None:
            console.print(redact_text(result.final_text), markup=False)
        if result.stopped_reason != "final":
            # Report and keep the session alive rather than killing it on one bad turn.
            console.print(f"[stopped: {result.stopped_reason}]", markup=False)


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


@keys_app.command("status")
def keys_status(provider: str = typer.Argument("openai-compatible")) -> None:
    """Show credential status for a provider."""
    status = CredentialStore().status(provider)
    console.print_json(status.model_dump_json())


@keys_app.command("set")
def keys_set(provider: str) -> None:
    """Store an API key for a provider."""
    secret = typer.prompt("API key", hide_input=True)
    if not secret.strip():
        typer.echo("API key cannot be blank.", err=True)
        raise typer.Exit(code=1)
    CredentialStore().set_key(provider, secret)
    console.print(f"{provider} stored", markup=False)


@keys_app.command("clear")
def keys_clear(provider: str) -> None:
    """Clear an API key for a provider."""
    CredentialStore().clear_key(provider)
    console.print(f"{provider} cleared", markup=False)


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
