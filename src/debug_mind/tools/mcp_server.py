"""MCP Server for DebugMind Memory — exposes the bug memory as MCP tools.

This allows any MCP-compatible client (Claude Code, Claude Desktop, etc.)
to search and contribute to the shared bug knowledge base.

Run: python -m debug_mind.tools.mcp_server

Tools exposed:
  - search_similar_bugs: Semantic search for past bug cases
  - save_bug_case: Save a new diagnosed case to memory
  - list_recent_bugs: List recently diagnosed cases
  - get_bug_stats: Get memory store statistics
"""

from __future__ import annotations

import json
import sys

from mcp.server.fastmcp import FastMCP

from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import BugCase, Severity, BugStatus

mcp = FastMCP("debug-mind-memory")

_memory: MemoryStore | None = None


def _get_memory() -> MemoryStore:
    global _memory
    if _memory is None:
        _memory = MemoryStore()
    return _memory


@mcp.tool()
def search_similar_bugs(query: str, top_k: int = 5) -> str:
    """Search past bug cases by symptom, error, or keyword description.

    Use this first when investigating a new bug to check if a similar
    issue has been diagnosed before.
    """
    memory = _get_memory()
    results = memory.search(query=query, top_k=top_k)

    if not results:
        return json.dumps({"found": 0, "message": "No similar bugs found in memory."}, ensure_ascii=False)

    output = {
        "found": len(results),
        "cases": [
            {
                "id": r.case.id,
                "title": r.case.title,
                "similarity": r.score,
                "root_cause": r.case.root_cause,
                "fix": r.case.fix_suggestion,
                "tags": r.case.tags,
            }
            for r in results
        ],
    }
    return json.dumps(output, indent=2, ensure_ascii=False)


@mcp.tool()
def save_bug_case(
    title: str,
    symptoms: str,
    root_cause: str,
    fix_suggestion: str,
    error_log: str = "",
    severity: str = "medium",
    tags: list[str] | None = None,
    environment: dict[str, str] | None = None,
    diagnosis_steps: list[str] | None = None,
) -> str:
    """Save a diagnosed bug case to the experiential memory.

    Call this after completing a bug diagnosis so future investigations
    can benefit from this knowledge.
    """
    memory = _get_memory()

    case = BugCase(
        title=title,
        symptoms=symptoms,
        error_log=error_log,
        root_cause=root_cause,
        fix_suggestion=fix_suggestion,
        severity=Severity(severity),
        status=BugStatus.ROOT_CAUSE_FOUND,
        tags=tags or [],
        environment=environment or {},
        diagnosis_steps=diagnosis_steps or [],
    )

    memory.save(case)
    return json.dumps({"saved": True, "case_id": case.id}, ensure_ascii=False)


@mcp.tool()
def list_recent_bugs(limit: int = 10) -> str:
    """List the most recently diagnosed bug cases."""
    memory = _get_memory()
    cases = memory.list_recent(limit=limit)
    return json.dumps(
        {
            "count": len(cases),
            "cases": [
                {
                    "id": c.id,
                    "title": c.title,
                    "severity": c.severity.value,
                    "status": c.status.value,
                    "tags": c.tags,
                    "created_at": c.created_at.isoformat(),
                }
                for c in cases
            ],
        },
        indent=2,
        ensure_ascii=False,
    )


@mcp.tool()
def get_bug_stats() -> str:
    """Get statistics about the bug memory store."""
    memory = _get_memory()
    stats = memory.stats()
    return json.dumps(
        {
            "total_cases": stats.total_cases,
            "by_severity": stats.by_severity,
            "by_status": stats.by_status,
            "top_tags": stats.top_tags,
        },
        indent=2,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    mcp.run()
