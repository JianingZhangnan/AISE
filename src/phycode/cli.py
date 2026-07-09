import tempfile
from pathlib import Path

import typer
from rich.console import Console

from phycode import __version__
from phycode.agent import AgentLoop
from phycode.config import load_project_config
from phycode.context import ContextBuilder, MemoryStore, SessionStore
from phycode.credentials import CredentialStore
from phycode.demos import run_feedback_demo, run_guardrail_demo, run_policy_demo
from phycode.llm import EchoLLM
from phycode.models import Session, SessionMode
from phycode.policy import PolicyContext
from phycode.redaction import redact_text
from phycode.trace import TraceStore
from phycode.tools import ToolRegistry
from phycode.tools import ToolRuntime
from phycode.tools.file_tools import register_file_tools
from phycode.tools.search_tools import register_search_tools
from phycode.tools.shell_tools import register_shell_tools
from phycode.tools.state_tools import register_state_tools

app = typer.Typer(help="PhyCode coding agent harness")
tools_app = typer.Typer(help="Inspect registered tools")
app.add_typer(tools_app, name="tools")
config_app = typer.Typer(help="Read and write non-sensitive configuration")
keys_app = typer.Typer(help="Manage provider credentials")
app.add_typer(config_app, name="config")
app.add_typer(keys_app, name="keys")
console = Console()


def build_default_registry(
    workspace_root: Path | None = None,
    test_command: str | None = None,
    memory_store: MemoryStore | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    config = load_project_config(Path.cwd())
    root = (workspace_root if workspace_root is not None else config.workspace.root).resolve()
    configured_test_command = test_command if test_command is not None else config.test.command
    register_file_tools(registry)
    register_search_tools(registry, workspace_root=root)
    register_shell_tools(registry, workspace_root=root, test_command=configured_test_command)
    register_state_tools(registry, workspace_root=root, memory_store=memory_store)
    return registry


def build_agent(mode: SessionMode) -> AgentLoop:
    """Build the default local agent loop for CLI commands."""
    config = load_project_config(Path.cwd())
    root = config.workspace.root
    phycode_dir = root / ".phycode"
    session_store = SessionStore(Session(workspace_root=str(root), mode=mode))
    memory_store = MemoryStore(phycode_dir / "memory.jsonl")
    registry = build_default_registry(root, config.test.command, memory_store)
    return AgentLoop(
        llm=EchoLLM(),
        context_builder=ContextBuilder(session_store, memory_store),
        tool_runtime=ToolRuntime(registry),
        policy_context=PolicyContext(
            workspace_root=root,
            allowlist=config.workspace.allowlist,
            interactive=mode == SessionMode.INTERACTIVE,
        ),
        trace_store=TraceStore(phycode_dir / "traces"),
        session_store=session_store,
        max_steps=config.agent.max_steps,
    )


@app.command()
def version() -> None:
    """Print the PhyCode version."""
    console.print(f"phycode {__version__}")


@app.command()
def run(task: str = typer.Argument(..., help="Task to run non-interactively")) -> None:
    """Run a task once with the local EchoLLM-backed agent."""
    result = build_agent(SessionMode.NON_INTERACTIVE).run(task)
    if result.final_text is not None:
        console.print(redact_text(result.final_text), markup=False)
    if result.stopped_reason != "final":
        raise typer.Exit(code=1)


@app.command()
def chat() -> None:
    """Start an interactive local EchoLLM-backed session."""
    console.print("PhyCode interactive session. Type /exit to leave.")
    loop = build_agent(SessionMode.INTERACTIVE)
    while True:
        user_input = typer.prompt("phycode")
        if user_input == "/exit":
            return
        result = loop.run(user_input)
        if result.final_text is not None:
            console.print(redact_text(result.final_text), markup=False)
        if result.stopped_reason != "final":
            raise typer.Exit(code=1)


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
