import typer
from rich.console import Console

from phycode import __version__

app = typer.Typer(help="PhyCode coding agent harness")
tools_app = typer.Typer(help="Inspect registered tools")
app.add_typer(tools_app, name="tools")
console = Console()


@app.command()
def version() -> None:
    """Print the PhyCode version."""
    console.print(f"phycode {__version__}")


@tools_app.command("list")
def list_tools() -> None:
    """List registered tools."""
    console.print("No tools registered yet")
