"""Built-in skill implementations: memory, codebase, jvm.

Why this exists:
Concrete `Skill` subclasses the default registry ships with. Each one wraps an
existing capability (memory search, code search) or adds a new domain pack
(jvm pattern matching). The agent no longer hard-codes tool lists; instead it
loads skills by name from `get_default_registry()`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from debug_mind.observability.logger import get_logger
from debug_mind.schemas import BugCase, BugStatus, Severity, compact_patch_attempts
from debug_mind.skills.registry import Skill
from debug_mind.tools.schemas import CODEBASE_TOOLS, MEMORY_TOOLS

_log = get_logger("skills")


# ── Memory skill ────────────────────────────────────────────────────────────


class MemorySkill(Skill):
    """Searching and writing the experiential bug-memory store."""

    name = "memory"
    description = "Search past bug cases and save new diagnoses to the memory store."

    def get_tools(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return list(MEMORY_TOOLS)

    def execute(
        self, tool_name: str, params: dict[str, Any], context: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        memory = context.get("memory")
        if memory is None:
            return {"error": "MemorySkill requires a memory store in context"}, None

        if tool_name == "search_memory":
            results = memory.search(query=params["query"], top_k=params.get("top_k", 5))
            return (
                {
                    "found": len(results),
                    "cases": [
                        {
                            "id": r.case.id,
                            "title": r.case.title,
                            "score": r.score,
                            "root_cause": r.case.root_cause,
                            "fix_suggestion": r.case.fix_suggestion,
                            "tags": r.case.tags,
                            "verified": r.case.verified,
                            "hit_count": r.case.hit_count,
                            "patch_attempts": compact_patch_attempts(r.case.patch_attempts),
                        }
                        for r in results
                    ],
                },
                "search",
            )

        if tool_name == "save_to_memory":
            from debug_mind.sanitize import sanitize_tags

            case = BugCase(
                title=params["title"],
                symptoms=params["symptoms"],
                error_log=params.get("error_log", ""),
                root_cause=params["root_cause"],
                fix_suggestion=params["fix_suggestion"],
                severity=Severity(params.get("severity", "medium")),
                status=BugStatus.ROOT_CAUSE_FOUND,
                tags=sanitize_tags(params.get("tags", [])),
                environment=params.get("environment", {}),
                diagnosis_steps=params.get("diagnosis_steps", []),
                similar_case_ids=params.get("similar_case_ids", []),
                patch_attempts=params.get("patch_attempts", []),
            )
            memory.save(case)
            return {"saved": True, "case_id": case.id}, "save"

        return {"error": f"MemorySkill does not handle tool: {tool_name}"}, None


# ── Codebase skill ──────────────────────────────────────────────────────────


class CodebaseSkill(Skill):
    """Real codebase access — grep, file read, structure listing, symbol parsing.

    Only exposes its tools when the runtime context has a project_path. This
    preserves the pre-Phase-6 behaviour: agents constructed without a project
    do not advertise codebase tools to the LLM.
    """

    name = "codebase"
    description = (
        "Search and read files in the user's project source code. Requires a project path."
    )

    def get_tools(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        if not context.get("project_path"):
            return []
        return list(CODEBASE_TOOLS)

    def execute(
        self, tool_name: str, params: dict[str, Any], context: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        project_path = context.get("project_path")
        if not project_path:
            return (
                {"error": "No project path configured. Use --project to connect a codebase."},
                None,
            )

        # Local imports to mirror the original lazy-loading shape from agent.py
        from debug_mind.skills.codebase import (
            list_project_structure,
            read_file,
            search_code,
        )

        if tool_name == "search_code":
            return (
                search_code(
                    pattern=params["pattern"],
                    project_path=project_path,
                    file_type=params.get("file_type", ""),
                ),
                None,
            )
        if tool_name == "read_file":
            return (
                read_file(
                    file_path=params["file_path"],
                    project_path=project_path,
                    start_line=params.get("start_line", 0),
                    end_line=params.get("end_line", 100),
                ),
                None,
            )
        if tool_name == "list_project_structure":
            return (
                list_project_structure(
                    project_path=project_path,
                    depth=params.get("depth", 3),
                ),
                None,
            )
        if tool_name == "parse_symbols":
            from debug_mind.skills.parser import parse_symbols

            return (
                parse_symbols(project_path=project_path, file_path=params["file_path"]),
                "code",
            )

        return {"error": f"CodebaseSkill does not handle tool: {tool_name}"}, None


# ── JVM skill (demo of a new domain pack) ───────────────────────────────────


_JVM_PATTERNS_PATH = Path(__file__).parent / "data" / "jvm_patterns.json"


def _load_jvm_patterns() -> list[dict[str, Any]]:
    try:
        data = json.loads(_JVM_PATTERNS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("jvm_patterns load failed", extra={"err": str(exc)})
        return []
    return list(data.get("patterns", []))


class JvmSkill(Skill):
    """Pattern-matches stack traces / error text against common JVM errors.

    This is a deliberately small, dependency-free demo to prove that a
    domain-specific skill pack can be authored independently and loaded with
    `--skills jvm`. It is NOT a full JVM analyser — for production use you
    would wire in an external service (Eclipse MAT, async-profiler, etc.).
    """

    name = "jvm"
    description = "Look up known JVM error patterns (NPE, OOM, deadlock, ...) and suggest fixes."

    def __init__(self) -> None:
        self._patterns = _load_jvm_patterns()

    def get_tools(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "name": "search_jvm_stack_pattern",
                "description": (
                    "Match a stack trace or error string against known JVM error patterns "
                    "(NPE, OutOfMemoryError, deadlock, ClassNotFoundException, ...). "
                    "Use ONLY when the bug involves Java / Kotlin / Scala / JVM bytecode. "
                    "Returns the top matching patterns with common causes and typical fixes."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Stack trace or error message text to match against.",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Max patterns to return (default 3).",
                            "default": 3,
                        },
                    },
                    "required": ["text"],
                },
            }
        ]

    def execute(
        self, tool_name: str, params: dict[str, Any], context: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        if tool_name != "search_jvm_stack_pattern":
            return {"error": f"JvmSkill does not handle tool: {tool_name}"}, None

        text = (params.get("text") or "").lower()
        top_k = max(1, int(params.get("top_k", 3)))

        scored: list[tuple[int, dict[str, Any]]] = []
        for pat in self._patterns:
            score = 0
            for kw in pat.get("keywords", []):
                if not kw:
                    continue
                # Substring match (case-insensitive) plus a weak regex try for short keywords
                kw_lc = kw.lower()
                if kw_lc in text:
                    score += 2
                elif re.search(re.escape(kw_lc), text):
                    score += 1
            if score > 0:
                scored.append((score, pat))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = [
            {
                "id": p["id"],
                "name": p["name"],
                "common_cause": p["common_cause"],
                "typical_fix": p["typical_fix"],
                "severity_hint": p.get("severity_hint", "medium"),
                "match_score": s,
            }
            for s, p in scored[:top_k]
        ]
        return {"found": len(top), "patterns": top}, None
