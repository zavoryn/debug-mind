"""CLI interface for DebugMind — diagnose bugs from the terminal.

Usage:
    debug-mind diagnose "NPE on login endpoint"
    debug-mind diagnose --log error.log --env "java=17,framework=Spring Boot 3.2" "Service crashes on startup"
    debug-mind search "redis connection timeout"
    debug-mind list [--limit 10]
    debug-mind stats
    debug-mind rebuild
"""

from __future__ import annotations

import os
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn

from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import BugCase, Severity

console = Console()


def _get_memory() -> MemoryStore:
    return MemoryStore()


@click.group()
@click.version_option(version="0.1.0")
def main():
    """DebugMind — AI Bug Diagnosis Agent with Experiential Memory."""
    pass


@main.command()
@click.argument("description")
@click.option("--log", "-l", default="", help="Path to error log file or inline log text")
@click.option("--env", "-e", default="", help="Environment as key=value pairs, comma-separated")
@click.option("--severity", "-s", default="medium", type=click.Choice(["critical", "high", "medium", "low"]))
def diagnose(description: str, log: str, env: str, severity: str):
    """Diagnose a bug using AI + memory of past cases."""
    # Parse environment
    environment = {}
    if env:
        for pair in env.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                environment[k.strip()] = v.strip()

    # Read log file or use inline
    error_log = ""
    if log:
        if os.path.isfile(log):
            error_log = open(log, encoding="utf-8").read()
        else:
            error_log = log

    console.print(Panel(f"[bold]{description}[/bold]", title="Bug Report", border_style="red"))

    memory = _get_memory()

    # Show memory search step
    with Progress(SpinnerColumn(), TextColumn("[bold blue]Searching memory for similar cases..."), console=console, transient=True) as progress:
        progress.add_task("search", total=None)
        similar = memory.search(query=description, top_k=3)

    if similar:
        console.print(f"\n[green]Found {len(similar)} similar case(s) in memory:[/green]")
        for r in similar:
            console.print(f"  • [cyan]{r.case.title}[/cyan] (score: {r.score:.0%}) — {r.case.root_cause[:80]}")
    else:
        console.print("\n[yellow]No similar cases found — performing full diagnosis.[/yellow]")

    # Run diagnosis
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        console.print("[red]Error: ANTHROPIC_API_KEY environment variable not set.[/red]")
        console.print("Run: export ANTHROPIC_API_KEY=your-key-here")
        sys.exit(1)

    from debug_mind.agent import DiagnosticAgent

    agent = DiagnosticAgent(memory=memory, api_key=api_key)

    with Progress(SpinnerColumn(), TextColumn("[bold blue]Agent is diagnosing..."), console=console, transient=True) as progress:
        progress.add_task("diagnose", total=None)
        result = agent.diagnose(
            bug_description=description,
            error_log=error_log,
            environment=environment,
        )

    # Display result
    console.print()
    console.print(Panel(
        f"[bold green]Root Cause:[/bold green]\n{result.root_cause}\n\n"
        f"[bold yellow]Fix Suggestion:[/bold yellow]\n{result.fix_suggestion}\n\n"
        f"[bold blue]Confidence:[/bold blue] {result.confidence:.0%}\n"
        f"[bold blue]Similar Cases Used:[/bold blue] {result.similar_cases_found}",
        title=f"Diagnosis Result — {result.case_id}",
        border_style="green",
    ))

    console.print(f"\n[dim]Case saved to memory with ID: {result.case_id}[/dim]")


@main.command()
@click.argument("query")
@click.option("--top-k", "-k", default=5, help="Number of results")
def search(query: str, top_k: int):
    """Search the bug memory for similar cases."""
    memory = _get_memory()
    results = memory.search(query=query, top_k=top_k)

    if not results:
        console.print("[yellow]No similar bugs found in memory.[/yellow]")
        return

    table = Table(title=f"Search Results for: {query}")
    table.add_column("Score", style="bold", width=8)
    table.add_column("ID", style="cyan", width=12)
    table.add_column("Title", width=35)
    table.add_column("Root Cause", width=40)
    table.add_column("Tags", style="dim", width=20)

    for r in results:
        tags = ", ".join(r.case.tags[:3])
        table.add_row(
            f"{r.score:.0%}",
            r.case.id,
            r.case.title[:35],
            r.case.root_cause[:40],
            tags,
        )

    console.print(table)


@main.command(name="list")
@click.option("--limit", "-n", default=20, help="Max cases to show")
def list_cases(limit: int):
    """List recent bug cases in memory."""
    memory = _get_memory()
    cases = memory.list_recent(limit=limit)

    if not cases:
        console.print("[yellow]Memory is empty. Diagnose some bugs first![/yellow]")
        return

    table = Table(title=f"Recent Bug Cases (showing {len(cases)})")
    table.add_column("ID", style="cyan", width=12)
    table.add_column("Title", width=35)
    table.add_column("Severity", width=10)
    table.add_column("Status", width=15)
    table.add_column("Tags", style="dim", width=25)
    table.add_column("Created", width=19)

    severity_colors = {"critical": "red bold", "high": "yellow", "medium": "white", "low": "dim"}

    for c in cases:
        color = severity_colors.get(c.severity.value, "white")
        table.add_row(
            c.id,
            c.title[:35],
            f"[{color}]{c.severity.value}[/{color}]",
            c.status.value,
            ", ".join(c.tags[:4]),
            c.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@main.command()
def stats():
    """Show memory store statistics."""
    memory = _get_memory()
    s = memory.stats()

    console.print(Panel(
        f"[bold]Total Cases:[/bold] {s.total_cases}\n\n"
        f"[bold]By Severity:[/bold] {s.by_severity}\n"
        f"[bold]By Status:[/bold] {s.by_status}\n"
        f"[bold]Top Tags:[/bold] {s.top_tags}",
        title="Memory Store Stats",
        border_style="blue",
    ))


@main.command()
def rebuild():
    """Rebuild the vector index from Markdown files."""
    memory = _get_memory()
    with Progress(SpinnerColumn(), TextColumn("[bold blue]Rebuilding vector index..."), console=console, transient=True) as progress:
        progress.add_task("rebuild", total=None)
        count = memory.rebuild_index()
    console.print(f"[green]Rebuilt index with {count} cases.[/green]")


@main.command()
def serve():
    """Start the MCP server for external clients."""
    console.print("[bold blue]Starting DebugMind MCP Server...[/bold blue]")
    from debug_mind.tools.mcp_server import mcp
    mcp.run()


if __name__ == "__main__":
    main()
