"""CLI interface for DebugMind — diagnose bugs from the terminal.

Usage:
    # Basic diagnosis (memory only)
    debug-mind diagnose "NPE on login endpoint"

    # With codebase access (real code search!)
    debug-mind diagnose --project /path/to/project "Service crashes on startup"

    # With log file and environment
    debug-mind diagnose --project ./my-app --log error.log --env "java=17,framework=Spring Boot 3.2" "NPE in UserService"

    # Memory management
    debug-mind search "redis connection timeout"
    debug-mind list
    debug-mind stats
    debug-mind rebuild
    debug-mind serve
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import DiagnosisResult

# Load .env before anything else
load_dotenv()

console = Console()

DEFAULT_MEMORY_DIR = Path(os.environ.get("DEBUG_MIND_MEMORY_DIR", "memory"))


def _get_memory() -> MemoryStore:
    return MemoryStore(memory_dir=DEFAULT_MEMORY_DIR)


@click.group()
@click.version_option(version="0.1.0")
def main():
    """DebugMind — AI Bug Diagnosis Agent with Experiential Memory."""
    pass


@main.command()
@click.argument("description")
@click.option("--log", "-l", default="", help="Error log file path or inline log text")
@click.option("--env", "-e", default="", help="Environment: key=value pairs, comma-separated")
@click.option("--project", "-p", default="", help="Project root path for codebase access")
@click.option("--severity", "-s", default="medium", type=click.Choice(["critical", "high", "medium", "low"]))
@click.option("--no-stream", is_flag=True, help="Disable streaming output (show spinner instead)")
def diagnose(description: str, log: str, env: str, project: str, severity: str, no_stream: bool):
    """Diagnose a bug using AI + memory + optional codebase search."""
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
            with open(log, encoding="utf-8") as f:
                error_log = f.read()
        else:
            error_log = log

    # Validate project path
    project_path = None
    if project:
        project = os.path.abspath(project)
        if not os.path.isdir(project):
            console.print(f"[red]Error: {project} is not a directory[/red]")
            sys.exit(1)
        project_path = project

    console.print(Panel(
        f"[bold]{description}[/bold]"
        + (f"\n[dim]Project: {project_path}[/dim]" if project_path else ""),
        title="Bug Report",
        border_style="red",
    ))

    memory = _get_memory()

    # Step 1: Memory search
    with console.status("[bold blue]Searching memory for similar cases..."):
        similar = memory.search(query=description, top_k=3)

    if similar:
        console.print(f"\n[green]Found {len(similar)} similar case(s) in memory:[/green]")
        for r in similar:
            console.print(f"  [cyan]{r.case.title}[/cyan] (score: {r.score:.0%})")
            console.print(f"  [dim]  Root cause: {r.case.root_cause[:100]}[/dim]")
    else:
        console.print("\n[yellow]No similar cases found — performing full diagnosis.[/yellow]")

    # Step 2: Run agent
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        console.print("[red]Error: ANTHROPIC_API_KEY not set.[/red]")
        console.print("Create a .env file with: ANTHROPIC_API_KEY=your-key-here")
        sys.exit(1)

    from debug_mind.agent import DiagnosticAgent

    agent = DiagnosticAgent(memory=memory, project_path=project_path, api_key=api_key)

    if no_stream:
        with console.status("[bold blue]Agent is diagnosing..."):
            result = agent.diagnose(
                bug_description=description,
                error_log=error_log,
                environment=environment,
            )
        _print_result(result)
    else:
        result = _stream_diagnose(agent, description, error_log, environment)
        _print_result(result)


def _stream_diagnose(agent, description: str, error_log: str, environment: dict) -> DiagnosisResult:
    """Stream the agent's thinking and tool calls in real-time."""
    result = None

    thinking_parts: list[str] = []
    tool_lines: list[str] = []

    def _render():
        parts = []
        if tool_lines:
            parts.append(Text("\n".join(tool_lines), style="dim cyan"))
        if thinking_parts:
            combined = "\n".join(thinking_parts)
            parts.append(Markdown(combined))
        return Group(*parts) if parts else Text("")

    with Live(_render(), console=console, refresh_per_second=4, vertical_overflow="visible") as live:
        for event_type, data in agent.diagnose_stream(
            bug_description=description,
            error_log=error_log,
            environment=environment,
        ):
            if event_type == "thinking":
                thinking_parts.append(data)
                live.update(_render())

            elif event_type == "tool_call":
                name = data["name"]
                inp = data["input"]
                if name == "search_memory":
                    detail = inp.get("query", "")[:50]
                    tool_lines.append(f"  [search_memory] {detail}...")
                elif name == "search_code":
                    detail = inp.get("pattern", "")[:50]
                    tool_lines.append(f"  [search_code] {detail}...")
                elif name == "read_file":
                    detail = inp.get("file_path", "")
                    tool_lines.append(f"  [read_file] {detail}")
                elif name == "list_project_structure":
                    tool_lines.append("  [list_project_structure]")
                elif name == "save_to_memory":
                    tool_lines.append("  [save_to_memory]")
                live.update(_render())

            elif event_type == "tool_result":
                res = data["result"]
                name = data["name"]
                if name == "search_memory":
                    found = res.get("found", 0)
                    tool_lines.append(f"    -> {found} similar case(s)")
                elif name == "search_code":
                    found = res.get("found", 0)
                    tool_lines.append(f"    -> {found} code matches")
                elif name == "read_file":
                    lines = res.get("total_lines", 0)
                    tool_lines.append(f"    -> {lines} lines read")
                elif name == "save_to_memory":
                    cid = res.get("case_id", "?")
                    tool_lines.append(f"    -> saved as {cid}")
                live.update(_render())

            elif event_type == "done":
                result = data

    return result


def _print_result(result: DiagnosisResult):
    """Print the final diagnosis result."""
    console.print()
    console.print(Panel(
        f"[bold green]Root Cause:[/bold green]\n{result.root_cause}\n\n"
        f"[bold yellow]Fix Suggestion:[/bold yellow]\n{result.fix_suggestion}\n\n"
        f"[bold blue]Similar Cases Used:[/bold blue] {result.similar_cases_found}",
        title=f"Diagnosis — {result.case_id}",
        border_style="green",
    ))
    console.print(f"[dim]Case ID: {result.case_id} | Steps: {len(result.diagnosis_steps)}[/dim]")


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

    table = Table(title=f"Search: {query}")
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
    table.add_column("Title", width=40)
    table.add_column("Severity", width=10)
    table.add_column("Status", width=15)
    table.add_column("Tags", style="dim", width=25)
    table.add_column("Created", width=19)

    severity_colors = {"critical": "red bold", "high": "yellow", "medium": "white", "low": "dim"}

    for c in cases:
        color = severity_colors.get(c.severity.value, "white")
        table.add_row(
            c.id,
            c.title[:40],
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
    with console.status("[bold blue]Rebuilding vector index..."):
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
