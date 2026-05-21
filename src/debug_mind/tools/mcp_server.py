"""MCP Server for DebugMind Memory — exposes the bug memory as MCP tools.

This allows any MCP-compatible client (Claude Code, Claude Desktop, etc.)
to search and contribute to the shared bug knowledge base.

Run: python -m debug_mind.tools.mcp_server

Tools exposed:
  - search_similar_bugs: Semantic search for past bug cases
  - save_bug_case: Save a new diagnosed case to memory
  - list_recent_bugs: List recently diagnosed cases
  - get_bug_stats: Get memory store statistics
  - verify_bug_case: Mark a case as verified correct or rejected
  - delete_bug_case: Delete a case from memory

Auth: Set DEBUG_MIND_MCP_TOKEN to require authentication on write tools.
"""

from __future__ import annotations

import collections
import json
import os
import time
import warnings
from typing import Optional

from mcp.server.fastmcp import FastMCP

from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import BugCase, Severity, BugStatus

mcp = FastMCP("debug-mind-memory")

_memory: MemoryStore | None = None
_mcp_token: str | None = os.environ.get("DEBUG_MIND_MCP_TOKEN", None)

if _mcp_token:
    pass  # Token is set, write tools will require it
else:
    warnings.warn(
        "DEBUG_MIND_MCP_TOKEN not set — MCP write tools are open to all clients. "
        "Set this env var to enable authentication.",
        stacklevel=2,
    )

# ── Rate limiting (write tools only) ──────────────────────────────────────────
# Sliding-window counter: at most DEBUG_MIND_MCP_RATE_LIMIT writes per 60 s.
_RATE_LIMIT = int(os.environ.get("DEBUG_MIND_MCP_RATE_LIMIT", "60"))
_rate_window: collections.deque[float] = collections.deque()


def _check_rate_limit() -> str | None:
    """Return a JSON error string if the write rate limit is exceeded, else None."""
    now = time.monotonic()
    while _rate_window and now - _rate_window[0] > 60.0:
        _rate_window.popleft()
    if len(_rate_window) >= _RATE_LIMIT:
        return json.dumps({"error": "rate_limit_exceeded", "retry_after_seconds": 60})
    _rate_window.append(now)
    return None


def _get_memory() -> MemoryStore:
    global _memory
    if _memory is None:
        # Read env var at call time — NOT module-level DEFAULT_MEMORY_DIR,
        # which is frozen at import time and immune to monkeypatch.setenv.
        memory_dir = os.environ.get("DEBUG_MIND_MEMORY_DIR", "memory")
        _memory = MemoryStore(memory_dir=memory_dir)
    return _memory


def _audit_log(op: str, case_id: str, actor: str = "mcp", **details) -> None:
    from debug_mind.observability.audit import write_audit

    write_audit(
        _get_memory().memory_dir / "audit.jsonl",
        op=op,
        case_id=case_id,
        actor=actor,
        **details,
    )


def _check_auth(token: str | None) -> str | None:
    """Return error message if auth fails, None if OK."""
    if not _mcp_token:
        return None
    if token != _mcp_token:
        return json.dumps({"error": "auth_required"}, ensure_ascii=False)
    return None


# ── Read tools (no auth required) ──────────────────────────────────────


@mcp.tool()
def search_similar_bugs(query: str, top_k: int = 5) -> str:
    """Search past bug cases by symptom, error, or keyword description.

    Use this first when investigating a new bug to check if a similar
    issue has been diagnosed before.
    """
    memory = _get_memory()
    results = memory.search(query=query, top_k=top_k)

    if not results:
        return json.dumps(
            {"found": 0, "message": "No similar bugs found in memory."}, ensure_ascii=False
        )

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
                "verified": r.case.verified,
                "hit_count": r.case.hit_count,
            }
            for r in results
        ],
    }
    return json.dumps(output, indent=2, ensure_ascii=False)


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


# ── Write tools (auth required if token is set) ────────────────────────


@mcp.tool()
def save_bug_case(
    title: str,
    symptoms: str,
    root_cause: str,
    fix_suggestion: str,
    error_log: str = "",
    severity: str = "medium",
    tags: Optional[list[str]] = None,
    environment: Optional[dict[str, str]] = None,
    diagnosis_steps: Optional[list[str]] = None,
    similar_case_ids: Optional[list[str]] = None,
    auth_token: str = "",
) -> str:
    """Save a diagnosed bug case to the experiential memory.

    Call this after completing a bug diagnosis so future investigations
    can benefit from this knowledge.
    """
    err = _check_auth(auth_token)
    if err:
        return err
    err = _check_rate_limit()
    if err:
        return err

    memory = _get_memory()

    from debug_mind.sanitize import sanitize_bug_input

    _, _, sanitized_env, sanitized_tags = sanitize_bug_input(
        description=title,
        error_log=error_log,
        environment=environment or {},
        tags=tags or [],
    )

    case = BugCase(
        title=title,
        symptoms=symptoms,
        error_log=error_log,
        root_cause=root_cause,
        fix_suggestion=fix_suggestion,
        severity=Severity(severity),
        status=BugStatus.ROOT_CAUSE_FOUND,
        tags=sanitized_tags,
        environment=sanitized_env,
        diagnosis_steps=diagnosis_steps or [],
        similar_case_ids=similar_case_ids or [],
    )

    memory.save(case)
    _audit_log("save", case.id, title=title)
    return json.dumps({"saved": True, "case_id": case.id}, ensure_ascii=False)


@mcp.tool()
def delete_bug_case(case_id: str, auth_token: str = "") -> str:
    """Delete a bug case from memory by its ID."""
    err = _check_auth(auth_token)
    if err:
        return err
    err = _check_rate_limit()
    if err:
        return err

    memory = _get_memory()
    deleted = memory.delete(case_id)
    if not deleted:
        return json.dumps({"error": f"Case {case_id} not found"}, ensure_ascii=False)
    _audit_log("delete", case_id)
    return json.dumps({"deleted": True, "case_id": case_id}, ensure_ascii=False)


@mcp.tool()
def verify_bug_case(case_id: str, correct: bool, notes: str = "", auth_token: str = "") -> str:
    """Verify a bug case as correct or mark it as wrong.

    correct=True marks the case as verified; correct=False removes it from
    the search index (soft delete — markdown file kept with .rejected suffix).
    """
    err = _check_auth(auth_token)
    if err:
        return err
    err = _check_rate_limit()
    if err:
        return err

    memory = _get_memory()
    ok = memory.verify(case_id, correct=correct, notes=notes)
    if not ok:
        return json.dumps({"error": f"Case {case_id} not found"}, ensure_ascii=False)
    _audit_log("verify", case_id, correct=correct)
    if correct:
        return json.dumps({"verified": True, "case_id": case_id}, ensure_ascii=False)
    return json.dumps({"rejected": True, "case_id": case_id}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
