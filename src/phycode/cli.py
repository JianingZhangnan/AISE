import sys
import tempfile
from pathlib import Path

import typer
from rich.console import Console

from phycode import __version__
from phycode.agent import AgentLoop
from phycode.config import ProjectConfig, load_project_config
from phycode.context import ContextBuilder, MemoryStore, SessionStore
from phycode.credentials import CredentialStore
from phycode.demos import run_feedback_demo, run_guardrail_demo, run_policy_demo
from phycode.llm import EchoLLM, LLMClient, OpenAICompatibleChatAdapter
from phycode.models import AgentProfile, PolicyDecision, Session, SessionMode, ToolCall
from phycode.policy import PolicyContext
from phycode.profiles import profile_spec
from phycode.redaction import redact_text
from phycode.trace import TraceStore
from phycode.tools import ToolRegistry
from phycode.tools import ToolRuntime
from phycode.tools.calculator_tools import register_calculator_tools
from phycode.tools.file_tools import register_file_tools
from phycode.tools.media_tools import register_media_tools
from phycode.tools.process_tools import register_process_tools
from phycode.tools.search_tools import register_search_tools
from phycode.tools.shell_tools import register_shell_tools
from phycode.tools.state_tools import register_state_tools
from phycode.tools.web_tools import register_web_tools
from phycode.visibility import PathVisibilityPolicy

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
    vision_inspector=None,
    visibility: PathVisibilityPolicy | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    config = load_project_config(Path.cwd())
    root = (workspace_root if workspace_root is not None else config.workspace.root).resolve()
    configured_test_command = test_command if test_command is not None else config.test.command
    register_file_tools(registry)
    register_calculator_tools(registry)
    register_search_tools(registry, workspace_root=root, visibility=visibility)
    register_process_tools(registry, root, frozenset({Path(sys.executable).name.casefold()}))
    register_shell_tools(registry, workspace_root=root, test_command=configured_test_command)
    register_state_tools(registry, workspace_root=root, memory_store=memory_store)
    register_web_tools(registry)
    register_media_tools(registry, vision_inspector)
    return registry


def _registry_subset(registry: ToolRegistry, names: frozenset[str]) -> ToolRegistry:
    selected = ToolRegistry()
    for spec in registry.list_specs():
        if spec.name not in names:
            continue
        executor = registry.executor_for(spec.name)
        if executor is not None:
            selected.register(spec, executor)
    return selected


def _build_llm(config: ProjectConfig, credential_store: CredentialStore | None = None) -> LLMClient:
    """Use the configured OpenAI-compatible provider when a key is stored, else EchoLLM.

    Keyring lookups are best-effort: any backend error (e.g. no keyring available)
    is treated as "no credential" so the offline EchoLLM path always works.
    """
    store = credential_store if credential_store is not None else CredentialStore()
    try:
        api_key = store.get_key(config.llm.provider)
    except Exception:
        api_key = None
    if api_key:
        return OpenAICompatibleChatAdapter(
            base_url=config.llm.base_url,
            model=config.llm.model,
            api_key=api_key,
            vision_model=config.llm.vision_model,
            timeout_seconds=config.llm.timeout_seconds,
            max_retries=config.llm.max_retries,
        )
    return EchoLLM()


def _interactive_approver(call: ToolCall, decision: PolicyDecision) -> bool:
    """Prompt the user to approve a risky tool call in an interactive session."""
    console.print(f"[approval needed] {call.tool_name}: {decision.reason}", markup=False)
    return typer.confirm("Approve this action?", default=False)


def build_agent(
    mode: SessionMode,
    llm: LLMClient | None = None,
    approval_handler=None,
    profile: AgentProfile = AgentProfile.CODING,
    max_tool_calls: int | None = None,
    vision_inspector=None,
) -> AgentLoop:
    """Build the default local agent loop for CLI commands."""
    spec = profile_spec(profile)
    config = load_project_config(Path.cwd())
    root = config.workspace.root
    phycode_dir = root / ".phycode"
    session_store = SessionStore(Session(workspace_root=str(root), mode=mode))
    memory_store = MemoryStore(phycode_dir / "memory.jsonl")
    resolved_llm = llm if llm is not None else _build_llm(config)
    configured_vision = vision_inspector
    if configured_vision is None and getattr(resolved_llm, "vision_model", None):
        configured_vision = getattr(resolved_llm, "inspect_image", None)
    policy_context = PolicyContext(
        workspace_root=root,
        allowlist=config.workspace.allowlist,
        interactive=mode == SessionMode.INTERACTIVE,
        profile_spec=spec,
    )
    registry = build_default_registry(
        root,
        config.test.command,
        memory_store,
        configured_vision,
        visibility=policy_context.visibility,
    )
    registry = _registry_subset(registry, spec.tool_names)
    return AgentLoop(
        llm=resolved_llm,
        context_builder=ContextBuilder(
            session_store,
            memory_store,
            max_chars=spec.max_context_chars,
            system_prompt=spec.system_prompt,
        ),
        tool_runtime=ToolRuntime(registry),
        policy_context=policy_context,
        trace_store=TraceStore(phycode_dir / "traces"),
        session_store=session_store,
        max_steps=config.agent.max_steps,
        max_tool_calls=max_tool_calls if max_tool_calls is not None else spec.max_tool_calls,
        approval_handler=approval_handler,
    )


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
