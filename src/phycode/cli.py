from pathlib import Path

import typer
from rich.console import Console

from phycode import __version__
from phycode.config import load_project_config
from phycode.credentials import CredentialStore

app = typer.Typer(help="PhyCode coding agent harness")
tools_app = typer.Typer(help="Inspect registered tools")
app.add_typer(tools_app, name="tools")
config_app = typer.Typer(help="Read and write non-sensitive configuration")
keys_app = typer.Typer(help="Manage provider credentials")
app.add_typer(config_app, name="config")
app.add_typer(keys_app, name="keys")
console = Console()


@app.command()
def version() -> None:
    """Print the PhyCode version."""
    console.print(f"phycode {__version__}")


@tools_app.command("list")
def list_tools() -> None:
    """List registered tools."""
    console.print("No tools registered yet")


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
