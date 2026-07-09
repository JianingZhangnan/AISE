from pathlib import Path

import typer
from rich.console import Console

from phycode import __version__
from phycode.config import load_project_config
from phycode.credentials import CredentialStore
from phycode.tools import ToolRegistry
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


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    root = Path.cwd()
    register_file_tools(registry)
    register_search_tools(registry, workspace_root=root)
    register_shell_tools(registry, workspace_root=root, test_command="uv run pytest")
    register_state_tools(registry, workspace_root=root)
    return registry


@app.command()
def version() -> None:
    """Print the PhyCode version."""
    console.print(f"phycode {__version__}")


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
