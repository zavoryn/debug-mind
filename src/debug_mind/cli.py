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

from debug_mind.memory.store import MemoryStore, MemoryBusyError
from debug_mind.schemas import DiagnosisResult


def _cli_audit(memory: MemoryStore, op: str, case_id: str, **details) -> None:
    """Write audit log entry from CLI."""
    audit_path = memory.memory_dir / "audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    from datetime import datetime as _dt, timezone as _tz
    entry = {
        "ts": _dt.now(_tz.utc).isoformat(),
        "actor": "cli",
        "op": op,
        "case_id": case_id,
        "details": details,
    }
    with open(audit_path, "a", encoding="utf-8") as f:
        f.write(_json.dumps(entry, ensure_ascii=False) + "\n")

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
@click.option("--max-cost", default=None, type=float, help="Max cost in USD (default 0.50)")
@click.option("--max-tokens", default=None, type=int, help="Max cumulative tokens (default 50000)")
@click.option("--no-retry", is_flag=True, help="Disable API retry on transient errors")
def diagnose(description: str, log: str, env: str, project: str, severity: str, no_stream: bool, max_cost: float | None, max_tokens: int | None, no_retry: bool):
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
    from debug_mind.budget import TokenBudget

    # Build budget from CLI args / env vars
    cost_limit = max_cost if max_cost is not None else float(os.environ.get("DEBUG_MIND_MAX_COST", "0.50"))
    token_limit = max_tokens if max_tokens is not None else int(os.environ.get("DEBUG_MIND_MAX_TOKENS", "50000"))
    budget = TokenBudget(
        max_input_tokens=token_limit,
        max_output_tokens=max(token_limit // 4, 1),
        max_cost_usd=cost_limit,
    )

    agent = DiagnosticAgent(memory=memory, project_path=project_path, api_key=api_key, budget=budget, no_retry=no_retry)

    if no_stream:
        try:
            with console.status("[bold blue]Agent is diagnosing..."):
                result = agent.diagnose(
                    bug_description=description,
                    error_log=error_log,
                    environment=environment,
                )
        except MemoryBusyError as e:
            console.print(f"[red]Error: {e}[/red]")
            sys.exit(1)
        _print_result(result)
    else:
        try:
            result = _stream_diagnose(agent, description, error_log, environment)
        except MemoryBusyError as e:
            console.print(f"[red]Error: {e}[/red]")
            sys.exit(1)
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
    try:
        with console.status("[bold blue]Rebuilding vector index..."):
            count = memory.rebuild_index()
    except MemoryBusyError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    console.print(f"[green]Rebuilt index with {count} cases.[/green]")


@main.command()
@click.argument("case_id")
def show(case_id: str):
    """Show detailed information about a specific bug case."""
    memory = _get_memory()
    case = memory.get(case_id)

    if not case:
        console.print(f"[red]Case '{case_id}' not found in memory.[/red]")
        sys.exit(1)

    env_lines = "\n".join(f"  {k}: {v}" for k, v in case.environment.items())
    steps_lines = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(case.diagnosis_steps))
    tags_str = ", ".join(case.tags) if case.tags else "none"

    console.print(Panel(
        f"[bold]Title:[/bold] {case.title}\n\n"
        f"[bold]Severity:[/bold] {case.severity.value}  |  [bold]Status:[/bold] {case.status.value}\n\n"
        f"[bold]Environment:[/bold]\n{env_lines or '  (not specified)'}\n\n"
        f"[bold]Symptoms:[/bold]\n  {case.symptoms}\n\n"
        f"[bold]Error Log:[/bold]\n  {case.error_log or '(none)'}\n\n"
        f"[bold]Root Cause:[/bold]\n  {case.root_cause or '(not yet diagnosed)'}\n\n"
        f"[bold]Fix Suggestion:[/bold]\n  {case.fix_suggestion or '(pending)'}\n\n"
        f"[bold]Diagnosis Steps:[/bold]\n{steps_lines or '  (none)'}\n\n"
        f"[bold]Tags:[/bold] {tags_str}\n\n"
        f"[dim]Created: {case.created_at.strftime('%Y-%m-%d %H:%M')}  "
        f"Updated: {case.updated_at.strftime('%Y-%m-%d %H:%M')}[/dim]",
        title=f"Case: {case.id}",
        border_style="blue",
    ))


@main.command()
@click.argument("case_id")
@click.confirmation_option(prompt="Are you sure you want to delete this case?")
def delete(case_id: str):
    """Delete a bug case from memory."""
    memory = _get_memory()
    try:
        deleted = memory.delete(case_id)
    except MemoryBusyError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    if deleted:
        _cli_audit(memory, "delete", case_id)
        console.print(f"[green]Case '{case_id}' deleted.[/green]")
    else:
        console.print(f"[red]Case '{case_id}' not found.[/red]")
        sys.exit(1)


@main.command()
@click.argument("case_id")
@click.option("--correct", "action", flag_value="correct", help="Mark case as verified correct")
@click.option("--wrong", "action", flag_value="wrong", help="Mark case as incorrect (soft delete)")
@click.option("--notes", default="", help="Verification or refutation notes")
def verify(case_id: str, action: str | None, notes: str):
    """Verify or reject a bug case diagnosis."""
    if not action:
        console.print("[red]Specify --correct or --wrong[/red]")
        sys.exit(1)

    memory = _get_memory()
    try:
        if action == "correct":
            ok = memory.verify(case_id, correct=True, notes=notes)
            if ok:
                _cli_audit(memory, "verify", case_id, correct=True)
                console.print(f"[green]Case '{case_id}' marked as verified.[/green]")
            else:
                console.print(f"[red]Case '{case_id}' not found.[/red]")
                sys.exit(1)
        elif action == "wrong":
            ok = memory.verify(case_id, correct=False, notes=notes)
            if ok:
                _cli_audit(memory, "verify", case_id, correct=False)
                console.print(f"[yellow]Case '{case_id}' rejected and removed from index.[/yellow]")
            else:
                console.print(f"[red]Case '{case_id}' not found.[/red]")
                sys.exit(1)
    except MemoryBusyError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@main.command()
def dedupe():
    """Scan for near-duplicate cases and list them for review."""
    memory = _get_memory()
    cases = memory.list_recent(limit=10000)

    if len(cases) < 2:
        console.print("[yellow]Not enough cases to check for duplicates.[/yellow]")
        return

    console.print(f"[bold blue]Checking {len(cases)} cases for near-duplicates...[/bold blue]")

    dupes_found = 0
    for i, c in enumerate(cases):
        results = memory.search(query=c.to_search_text(), top_k=3, min_score=0.9)
        for r in results:
            if r.case.id != c.id:
                dupes_found += 1
                console.print(
                    f"  [cyan]{c.id}[/cyan] <-> [cyan]{r.case.id}[/cyan] "
                    f"(score: {r.score:.2f})"
                )

    if dupes_found == 0:
        console.print("[green]No near-duplicates found.[/green]")
    else:
        console.print(f"\n[yellow]Found {dupes_found} potential duplicate pair(s).[/yellow]")
        console.print("[dim]Use 'debug-mind verify <id> --wrong' to reject duplicates.[/dim]")


@main.command()
def serve():
    """Start the MCP server for external clients."""
    console.print("[bold blue]Starting DebugMind MCP Server...[/bold blue]")
    from debug_mind.tools.mcp_server import mcp
    mcp.run()


@main.command()
@click.option("--since", default="24h", help="Time range: 1h, 24h, 7d (default 24h)")
@click.option("--op", default=None, help="Filter by operation: save, verify, delete, mark_used")
def audit(since: str, op: str | None):
    """Show audit log of write operations."""
    import json as _json
    from datetime import datetime as _dt, timedelta, timezone as _tz

    memory = _get_memory()
    audit_path = memory.memory_dir / "audit.jsonl"

    if not audit_path.exists():
        console.print("[yellow]No audit log found.[/yellow]")
        return

    # Parse --since
    since_map = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
    delta = since_map.get(since, timedelta(hours=24))
    cutoff = _dt.now() - delta

    entries = []
    with open(audit_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if op and entry.get("op") != op:
                continue
            ts_str = entry.get("ts", "")
            try:
                ts = _dt.fromisoformat(ts_str).replace(tzinfo=None)
                if ts < cutoff:
                    continue
            except (ValueError, TypeError):
                pass
            entries.append(entry)

    if not entries:
        console.print("[yellow]No matching audit entries.[/yellow]")
        return

    table = Table(title=f"Audit Log (last {since})")
    table.add_column("Time", width=19)
    table.add_column("Actor", width=6)
    table.add_column("Op", width=10)
    table.add_column("Case ID", width=14)
    table.add_column("Details", width=30)

    for e in reversed(entries[-50:]):
        details = ", ".join(f"{k}={v}" for k, v in e.get("details", {}).items())
        table.add_row(
            e.get("ts", "")[:19],
            e.get("actor", "?"),
            e.get("op", "?"),
            e.get("case_id", "?"),
            details[:30],
        )

    console.print(table)


@main.command()
@click.option("--search-only", is_flag=True, help="Only evaluate retrieval, no API key needed")
@click.option("--case", "case_id", default="", help="Run a single benchmark case by ID")
@click.option("--json", "json_path", default="", help="Write machine-readable results to file")
def eval(search_only: bool, case_id: str, json_path: str):
    """Evaluate memory retrieval quality against benchmark dataset."""
    from evaluation.dataset import load_all_cases, load_case
    from evaluation.benchmark import run_eval, format_results

    if case_id:
        bc = load_case(case_id)
        if not bc:
            console.print(f"[red]Benchmark case '{case_id}' not found.[/red]")
            sys.exit(1)
        cases = [bc]
    else:
        cases = load_all_cases()

    if not cases:
        console.print("[red]No benchmark cases found.[/red]")
        sys.exit(1)

    console.print(f"[bold blue]Running evaluation with {len(cases)} case(s)...[/bold blue]")

    result = run_eval(cases=cases, search_only=search_only)

    console.print()
    console.print(format_results(result))

    if json_path:
        import json as _json
        output = {
            "total": result.total,
            "hit_at_1": result.hit_at_1,
            "hit_at_3": result.hit_at_3,
            "hit_at_5": result.hit_at_5,
            "mrr": result.mrr,
            "keyword_recall": result.keyword_recall,
            "cases": [
                {
                    "id": cr.case_id,
                    "hit_at_1": cr.hit_at_1,
                    "hit_at_3": cr.hit_at_3,
                    "hit_at_5": cr.hit_at_5,
                    "mrr": cr.mrr,
                    "keyword_recall": cr.keyword_recall,
                    "top_hit_id": cr.top_hit_id,
                }
                for cr in result.case_results
            ],
        }
        Path(json_path).write_text(_json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(f"\n[dim]Results written to {json_path}[/dim]")


if __name__ == "__main__":
    main()
