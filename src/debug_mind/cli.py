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
from debug_mind.schemas import BugCase, DiagnosisResult


def _cli_audit(memory: MemoryStore, op: str, case_id: str, **details) -> None:
    from debug_mind.observability.audit import write_audit

    write_audit(memory.memory_dir / "audit.jsonl", op=op, case_id=case_id, actor="cli", **details)


# Load .env before anything else
load_dotenv()

console = Console()


def _get_memory() -> MemoryStore:
    # Read env var at call time — NOT module-level constant,
    # which is frozen at import time and immune to monkeypatch.setenv.
    memory_dir = os.environ.get("DEBUG_MIND_MEMORY_DIR", "memory")
    return MemoryStore(memory_dir=memory_dir)


@click.group()
@click.version_option(version="0.1.0")
@click.pass_context
def main(ctx: click.Context):
    """DebugMind — AI Bug Diagnosis Agent with Experiential Memory."""
    if ctx.invoked_subcommand not in (None, "serve", "web"):
        _warn_invalid_env()


_KNOWN_BACKENDS = {"chroma", "sqlite"}
_KNOWN_EMBEDDINGS = {"default", "openai", "voyage", "bge"}
_KNOWN_PROVIDERS = {"anthropic", "openai"}
_KNOWN_LOG_FORMATS = {"text", "json"}


def _warn_invalid_env() -> None:
    """Emit warnings for obviously wrong DEBUG_MIND_* environment variable values."""
    checks = [
        ("DEBUG_MIND_BACKEND", os.environ.get("DEBUG_MIND_BACKEND"), _KNOWN_BACKENDS),
        ("DEBUG_MIND_EMBEDDING", os.environ.get("DEBUG_MIND_EMBEDDING"), _KNOWN_EMBEDDINGS),
        ("DEBUG_MIND_PROVIDER", os.environ.get("DEBUG_MIND_PROVIDER"), _KNOWN_PROVIDERS),
        ("DEBUG_MIND_LOG_FORMAT", os.environ.get("DEBUG_MIND_LOG_FORMAT"), _KNOWN_LOG_FORMATS),
    ]
    for var, val, valid in checks:
        if val is not None and val.lower() not in valid:
            console.print(
                f"[yellow]Warning:[/yellow] {var}={val!r} is not recognised. "
                f"Valid values: {', '.join(sorted(valid))}"
            )
    for float_var in (
        "DEBUG_MIND_MAX_COST",
        "DEBUG_MIND_DEDUP_THRESHOLD",
        "DEBUG_MIND_HIT_COUNT_WEIGHT",
    ):
        raw = os.environ.get(float_var)
        if raw is not None:
            try:
                float(raw)
            except ValueError:
                console.print(
                    f"[yellow]Warning:[/yellow] {float_var}={raw!r} — expected a number, got a string."
                )


@main.command()
@click.argument("description")
@click.option("--log", "-l", default="", help="Error log file path or inline log text")
@click.option("--env", "-e", default="", help="Environment: key=value pairs, comma-separated")
@click.option("--project", "-p", default="", help="Project root path for codebase access")
@click.option(
    "--severity", "-s", default="medium", type=click.Choice(["critical", "high", "medium", "low"])
)
@click.option("--no-stream", is_flag=True, help="Disable streaming output (show spinner instead)")
@click.option("--max-cost", default=None, type=float, help="Max cost in USD (default 0.50)")
@click.option("--max-tokens", default=None, type=int, help="Max cumulative tokens (default 50000)")
@click.option("--no-retry", is_flag=True, help="Disable API retry on transient errors")
@click.option(
    "--skills",
    default=None,
    help=(
        "Comma-separated skill names to load (e.g. memory,jvm). "
        "Default: memory + codebase (if --project). Run 'debug-mind skills' to list."
    ),
)
def diagnose(
    description: str,
    log: str,
    env: str,
    project: str,
    severity: str,
    no_stream: bool,
    max_cost: float | None,
    max_tokens: int | None,
    no_retry: bool,
    skills: str | None,
):
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

    console.print(
        Panel(
            f"[bold]{description}[/bold]"
            + (f"\n[dim]Project: {project_path}[/dim]" if project_path else ""),
            title="Bug Report",
            border_style="red",
        )
    )

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
    cost_limit = (
        max_cost if max_cost is not None else float(os.environ.get("DEBUG_MIND_MAX_COST", "0.50"))
    )
    token_limit = (
        max_tokens
        if max_tokens is not None
        else int(os.environ.get("DEBUG_MIND_MAX_TOKENS", "50000"))
    )
    budget = TokenBudget(
        max_input_tokens=token_limit,
        max_output_tokens=max(token_limit // 4, 1),
        max_cost_usd=cost_limit,
    )

    skill_names: list[str] | None = None
    if skills:
        skill_names = [s.strip() for s in skills.split(",") if s.strip()]

    try:
        agent = DiagnosticAgent(
            memory=memory,
            project_path=project_path,
            api_key=api_key,
            budget=budget,
            no_retry=no_retry,
            skills=skill_names,
        )
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)

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
    turn_header = Text("")

    def _render():
        parts = [turn_header]
        if tool_lines:
            parts.append(Text("\n".join(tool_lines), style="dim cyan"))
        if thinking_parts:
            combined = "\n".join(thinking_parts)
            parts.append(Markdown(combined))
        return Group(*parts) if parts else Text("")

    with Live(
        _render(), console=console, refresh_per_second=4, vertical_overflow="visible"
    ) as live:
        for event_type, data in agent.diagnose_stream(
            bug_description=description,
            error_log=error_log,
            environment=environment,
        ):
            if event_type == "turn":
                turn_header.plain = f"[Turn {data['turn']}/{data['max_turns']}]"
                turn_header.stylize("bold dim")
                live.update(_render())

            elif event_type == "thinking":
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
    console.print(
        Panel(
            f"[bold green]Root Cause:[/bold green]\n{result.root_cause}\n\n"
            f"[bold yellow]Fix Suggestion:[/bold yellow]\n{result.fix_suggestion}\n\n"
            f"[bold blue]Similar Cases Used:[/bold blue] {result.similar_cases_found}",
            title=f"Diagnosis — {result.case_id}",
            border_style="green",
        )
    )
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

    console.print(
        Panel(
            f"[bold]Total Cases:[/bold] {s.total_cases}\n\n"
            f"[bold]By Severity:[/bold] {s.by_severity}\n"
            f"[bold]By Status:[/bold] {s.by_status}\n"
            f"[bold]Top Tags:[/bold] {s.top_tags}",
            title="Memory Store Stats",
            border_style="blue",
        )
    )


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
    steps_lines = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(case.diagnosis_steps))
    tags_str = ", ".join(case.tags) if case.tags else "none"

    console.print(
        Panel(
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
        )
    )


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
                    f"  [cyan]{c.id}[/cyan] <-> [cyan]{r.case.id}[/cyan] (score: {r.score:.2f})"
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
@click.option("--port", "-p", default=7860, help="Port to listen on (default 7860)")
@click.option("--share", is_flag=True, help="Create a public share link")
def web(port: int, share: bool):
    """Launch the Gradio web UI."""
    from debug_mind.web import launch_ui

    launch_ui(port=port, share=share)


@main.command()
def skills():
    """List built-in skill packs available to --skills."""
    from debug_mind.skills.registry import get_default_registry

    registry = get_default_registry()
    all_skills = registry.list_all()

    table = Table(title=f"Built-in skills ({len(all_skills)})")
    table.add_column("Name", style="cyan", width=12)
    table.add_column("Description", width=70)

    for s in all_skills:
        table.add_row(s.name, s.description)

    console.print(table)
    console.print(
        "[dim]Use --skills name1,name2 on `diagnose` to compose a custom skill set.[/dim]"
    )


@main.command()
def plugins():
    """List loaded plugins from DEBUG_MIND_PLUGINS."""
    from debug_mind.plugins import discover_plugins

    loaded, registry = discover_plugins()
    if not loaded:
        console.print("No plugins loaded. Set DEBUG_MIND_PLUGINS env var to load plugins.")
        return

    console.print(f"[bold]Loaded {len(loaded)} plugin(s):[/bold]")
    for p in loaded:
        console.print(f"  - {p}")

    if registry.tools:
        console.print(f"\n[bold]Custom tools:[/bold] {len(registry.tools)}")
        for t in registry.tools:
            console.print(f"  - {t['name']}")

    if registry.embedding_fns:
        console.print(f"\n[bold]Custom embeddings:[/bold] {', '.join(registry.embedding_fns)}")

    if registry.reranker_fns:
        console.print(f"\n[bold]Custom rerankers:[/bold] {', '.join(registry.reranker_fns)}")


@main.command()
@click.option("--days", "-d", default=30, help="Days of inactivity to consider stale")
@click.option("--dry-run", is_flag=True, help="List stale cases without changes")
def decay(days: int, dry_run: bool):
    """List or mark cases that have been inactive for N days."""
    from datetime import datetime, timezone

    memory = _get_memory()
    result = memory.decay(days=days, dry_run=dry_run)

    action = "Would mark" if dry_run else "Found"
    console.print(f"[bold]{action} {result['stale_count']} stale case(s):[/bold]")
    for cid in result["stale_ids"][:20]:
        case = memory.get(cid)
        if case:
            age = (
                "never used"
                if not case.last_used_at
                else (f"last used {(datetime.now(timezone.utc) - case.last_used_at).days}d ago")
            )
            console.print(f"  - [yellow]{cid}[/yellow] — {case.title} ({age})")
    if len(result["stale_ids"]) > 20:
        console.print(f"  ... and {len(result['stale_ids']) - 20} more")


@main.command()
@click.option("--days", "-d", default=90, help="Days since last verification to flag")
def reverify(days: int):
    """List verified cases that haven't been re-verified recently."""
    from datetime import datetime, timezone

    memory = _get_memory()
    stale = memory.reverify(days=days)

    if not stale:
        console.print(f"No verified cases need re-verification (> {days}d ago).")
        return

    console.print(f"[bold]{len(stale)} verified case(s) need re-verification:[/bold]")
    for cid in stale[:20]:
        case = memory.get(cid)
        if case:
            age = (
                "never re-verified"
                if not case.last_verified_at
                else (
                    f"last verified {(datetime.now(timezone.utc) - case.last_verified_at).days}d ago"
                )
            )
            console.print(f"  - [yellow]{cid}[/yellow] — {case.title} ({age})")
    if len(stale) > 20:
        console.print(f"  ... and {len(stale) - 20} more")


@main.command()
@click.argument("case_a")
@click.argument("case_b")
@click.option(
    "--relation",
    "-r",
    default="related",
    help="Relation type: variant, caused_by, fixed_by, related",
)
def link(case_a: str, case_b: str, relation: str):
    """Link two bug cases with a relation type."""
    memory = _get_memory()
    if memory.link(case_a, case_b, relation):
        console.print(f"[green]Linked {case_a} ↔ {case_b} ({relation})[/green]")
    else:
        console.print("[red]Failed to link — check case IDs exist.[/red]")


@main.command()
@click.argument("case_a")
@click.argument("case_b")
def unlink(case_a: str, case_b: str):
    """Remove all links between two bug cases."""
    memory = _get_memory()
    if memory.unlink(case_a, case_b):
        console.print(f"[green]Unlinked {case_a} ↔ {case_b}[/green]")
    else:
        console.print("[red]Failed to unlink.[/red]")


@main.command()
@click.option("--since", default="24h", help="Time range: 1h, 24h, 7d (default 24h)")
@click.option("--op", default=None, help="Filter by operation: save, verify, delete, mark_used")
def audit(since: str, op: str | None):
    """Show audit log of write operations."""
    import json as _json
    from datetime import datetime as _dt, timedelta

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
@click.option(
    "--min-hit-at-5", default=None, type=float, help="Fail if hit@5 is below this threshold"
)
def eval(search_only: bool, case_id: str, json_path: str, min_hit_at_5: float | None):
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

    if min_hit_at_5 is not None and result.hit_at_5 < min_hit_at_5:
        console.print(
            f"[red]hit@5 below threshold: {result.hit_at_5:.2f} < {min_hit_at_5:.2f}[/red]"
        )
        sys.exit(1)

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
        Path(json_path).write_text(
            _json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        console.print(f"\n[dim]Results written to {json_path}[/dim]")


@main.command()
@click.option("--fix", is_flag=True, help="Fix missing vectors by re-indexing")
@click.option("--delete-orphans", is_flag=True, help="Delete orphan vectors (requires --fix)")
def doctor(fix: bool, delete_orphans: bool):
    """Diagnose and fix inconsistencies between markdown and vector store."""
    memory = _get_memory()
    result = memory.doctor(fix=fix, delete_orphans=delete_orphans and fix)

    table = Table(title="Memory Health Check")
    table.add_column("Issue", width=25)
    table.add_column("Count", width=8)

    issues = [
        ("Missing vectors", result["missing_vectors"]),
        ("Orphan vectors", result["orphan_vectors"]),
        ("Pending rejections", result["pending_rejected"]),
    ]
    for label, count in issues:
        color = "green" if count == 0 else "yellow" if count < 5 else "red"
        table.add_row(label, f"[{color}]{count}[/{color}]")

    if fix:
        table.add_row("Fixed missing", f"[green]{result['fixed_missing']}[/green]")
        table.add_row("Fixed pending", f"[green]{result['fixed_pending']}[/green]")
    if delete_orphans and fix:
        table.add_row("Deleted orphans", f"[yellow]{result['deleted_orphans']}[/yellow]")

    console.print(table)
    if not fix and (
        result["missing_vectors"] or result["orphan_vectors"] or result["pending_rejected"]
    ):
        console.print(
            "[dim]Run with --fix to repair, add --delete-orphans to remove orphan vectors.[/dim]"
        )


@main.command(name="export")
@click.option(
    "--output", "-o", default="debug-mind-export.json", show_default=True, help="Output file path"
)
@click.option("--limit", "-n", default=0, type=int, help="Max cases (0 = all)")
def export_cases(output: str, limit: int):
    """Export all memory cases to a JSON file for backup or sharing.

    The exported file can be imported on another machine with 'debug-mind import'.
    """
    import json as _json

    memory = _get_memory()
    n = limit if limit > 0 else 999_999
    cases = memory.list_recent(limit=n)

    if not cases:
        console.print("[yellow]Memory is empty — nothing to export.[/yellow]")
        return

    payload = {
        "version": 1,
        "exported_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "cases": [c.model_dump(mode="json") for c in cases],
    }
    Path(output).write_text(_json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"[green]Exported {len(cases)} case(s) to {output}[/green]")


@main.command(name="import")
@click.argument("input_file")
@click.option("--skip-existing", is_flag=True, help="Skip cases whose ID already exists")
@click.option("--dry-run", is_flag=True, help="Show what would be imported without writing")
def import_cases(input_file: str, skip_existing: bool, dry_run: bool):
    """Import memory cases from a JSON file produced by 'debug-mind export'.

    By default existing cases are overwritten. Use --skip-existing to keep them.
    """
    import json as _json

    src = Path(input_file)
    if not src.exists():
        console.print(f"[red]File not found: {input_file}[/red]")
        sys.exit(1)

    try:
        payload = _json.loads(src.read_text(encoding="utf-8"))
    except _json.JSONDecodeError as e:
        console.print(f"[red]Invalid JSON: {e}[/red]")
        sys.exit(1)

    if payload.get("version") != 1:
        console.print("[red]Unsupported export format version.[/red]")
        sys.exit(1)

    raw_cases = payload.get("cases", [])
    if not raw_cases:
        console.print("[yellow]No cases found in export file.[/yellow]")
        return

    memory = _get_memory()
    imported = skipped = 0

    for raw in raw_cases:
        case = BugCase.model_validate(raw)
        if skip_existing and memory.get(case.id) is not None:
            skipped += 1
            continue
        if dry_run:
            console.print(f"  [dim]would import[/dim] {case.id} — {case.title}")
        else:
            memory.save(case)
            _cli_audit(memory, "import", case.id, source=input_file)
        imported += 1

    if dry_run:
        console.print(f"\n[bold]Dry run:[/bold] would import {imported}, skip {skipped} existing.")
    else:
        console.print(f"[green]Imported {imported} case(s), skipped {skipped}.[/green]")


if __name__ == "__main__":
    main()
