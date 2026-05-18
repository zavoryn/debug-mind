"""Diagnostic Agent — the brain of DebugMind.

Uses Claude with tool use to diagnose bugs. Two modes:
  1. Full mode: connected to a real codebase, can search code, read files.
  2. Offline mode: only uses memory search — no codebase access.

The agent loop follows the ReAct pattern:
  Observe (bug report) → Think → Act (call tool) → Observe (tool result) → ...
  → Final diagnosis → Save to memory.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable

import anthropic

from debug_mind.schemas import BugCase, DiagnosisResult, Severity, BugStatus
from debug_mind.memory.store import MemoryStore
from debug_mind.skills.codebase import search_code, read_file, list_project_structure

SYSTEM_PROMPT = """You are DebugMind, an expert bug diagnosis agent with access to:
1. **Experiential Memory** — a knowledge base of past bug diagnoses.
2. **Codebase Access** — you can search and read source code in the project.
3. **Log Analysis** — you can analyze error logs and stack traces.

## Your Workflow
1. **Search Memory** — ALWAYS call `search_memory` first to check for similar past bugs.
2. **Understand the Codebase** — If a project path is available, call `list_project_structure` and `search_code` to locate relevant code.
3. **Read Relevant Code** — Use `read_file` to inspect suspicious code referenced in stack traces.
4. **Analyze** — Correlate symptoms, error logs, source code, and any similar past cases.
5. **Save** — Call `save_to_memory` to persist the diagnosis for future reference.

## Diagnosis Guidelines
- Start from the stack trace — find the exact file and line.
- Check for null/empty returns, missing error handling, resource leaks.
- Consider framework-specific pitfalls (Spring autowiring, async context, etc.).
- Reference similar past cases when available — they shortcut the diagnosis.
- Be specific: cite file names, line numbers, method names.
- Provide copy-paste-ready fix suggestions.

Respond in the user's language. Be concise but thorough."""

# ── Tool Definitions ────────────────────────────────────────────────

MEMORY_TOOLS = [
    {
        "name": "search_memory",
        "description": "Search past bug cases in the experiential memory. ALWAYS call this first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Bug symptoms, error, or keywords"},
                "top_k": {"type": "integer", "description": "Max results (default 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "save_to_memory",
        "description": "Save a diagnosed bug case to memory. Call AFTER completing diagnosis.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short searchable title"},
                "symptoms": {"type": "string", "description": "What was observed"},
                "error_log": {"type": "string", "description": "Raw error log or stack trace"},
                "root_cause": {"type": "string", "description": "The identified root cause"},
                "fix_suggestion": {"type": "string", "description": "How to fix it"},
                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Searchable tags"},
                "environment": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Environment context",
                },
                "diagnosis_steps": {"type": "array", "items": {"type": "string"}},
                "similar_case_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "symptoms", "root_cause", "fix_suggestion"],
        },
    },
]

CODEBASE_TOOLS = [
    {
        "name": "search_code",
        "description": "Search the project source code for a pattern. Returns file:line:content matches.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Search pattern (supports regex)"},
                "file_type": {"type": "string", "description": "File extension filter (e.g. 'java', 'py')"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a source file from the project. Use after search_code to inspect specific code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Relative path from project root"},
                "start_line": {"type": "integer", "description": "Start line (0-based, default 0)", "default": 0},
                "end_line": {"type": "integer", "description": "End line (default 100)", "default": 100},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "list_project_structure",
        "description": "Get the directory tree of the project. Use to understand project layout.",
        "input_schema": {
            "type": "object",
            "properties": {
                "depth": {"type": "integer", "description": "Directory depth (default 3)", "default": 3},
            },
        },
    },
]


class DiagnosticAgent:
    """The core agent that diagnoses bugs using memory + codebase access + LLM."""

    def __init__(
        self,
        memory: MemoryStore,
        project_path: str | None = None,
        model: str = "claude-sonnet-4-20250514",
        api_key: str | None = None,
        on_tool_call: Callable[[str, dict], None] | None = None,
    ):
        self.memory = memory
        self.project_path = project_path
        self.model = model
        self.on_tool_call = on_tool_call
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

        self.tools = MEMORY_TOOLS + (CODEBASE_TOOLS if project_path else [])

    def diagnose(
        self,
        bug_description: str,
        error_log: str = "",
        environment: dict[str, str] | None = None,
    ) -> DiagnosisResult:
        """Run the full diagnostic loop on a bug report."""
        env_str = "\n".join(f"- {k}: {v}" for k, v in (environment or {}).items())

        user_message = f"""## New Bug Report

**Description:** {bug_description}

**Error Log:**
```
{error_log or "(no log provided)"}
```

**Environment:**
{env_str or "- not specified"}
{"**Project Path:** " + self.project_path if self.project_path else ""}

Diagnose this bug. Remember: search memory first, then inspect code if available, then save the result."""

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        diagnosis_steps: list[str] = []
        saved_case_id: str | None = None
        similar_case_ids: list[str] = []

        max_turns = 20
        for turn in range(max_turns):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=self.tools,
                messages=messages,
            )

            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            # Emit any text the agent produced this turn
            for block in assistant_content:
                if block.type == "text" and self.on_tool_call:
                    pass  # text is streamed via the final output

            tool_use_blocks = [b for b in assistant_content if b.type == "tool_use"]
            if not tool_use_blocks:
                break

            tool_results = []
            for block in tool_use_blocks:
                if self.on_tool_call:
                    self.on_tool_call(block.name, block.input)

                result, side_effect = self._execute_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

                step_desc = f"{block.name}({json.dumps(block.input, ensure_ascii=False)[:120]})"
                diagnosis_steps.append(step_desc)

                if side_effect == "search":
                    similar_case_ids = [c["id"] for c in result.get("cases", [])]
                elif side_effect == "save":
                    saved_case_id = result.get("case_id")

            messages.append({"role": "user", "content": tool_results})

        # Extract final text
        final_text = "\n".join(
            b.text for b in assistant_content if hasattr(b, "text") and b.type == "text"
        )

        saved_case = self.memory.get(saved_case_id) if saved_case_id else None

        return DiagnosisResult(
            case_id=saved_case.id if saved_case else "unknown",
            root_cause=saved_case.root_cause if saved_case else final_text,
            confidence=saved_case is not None,
            diagnosis_steps=diagnosis_steps,
            fix_suggestion=saved_case.fix_suggestion if saved_case else "",
            similar_cases_found=len(similar_case_ids),
            reasoning=final_text,
        )

    def diagnose_stream(
        self,
        bug_description: str,
        error_log: str = "",
        environment: dict[str, str] | None = None,
    ):
        """Streaming variant — yields (event_type, data) tuples as the agent works.

        Yields:
            ("thinking", text) — agent's text output
            ("tool_call", {"name": ..., "input": ...}) — tool being called
            ("tool_result", {"name": ..., "result": ...}) — tool execution result
            ("done", DiagnosisResult) — final result
        """
        env_str = "\n".join(f"- {k}: {v}" for k, v in (environment or {}).items())

        user_message = f"""## New Bug Report

**Description:** {bug_description}

**Error Log:**
```
{error_log or "(no log provided)"}
```

**Environment:**
{env_str or "- not specified"}
{"**Project Path:** " + self.project_path if self.project_path else ""}

Diagnose this bug. Remember: search memory first, then inspect code if available, then save the result."""

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        diagnosis_steps: list[str] = []
        saved_case_id: str | None = None
        similar_case_ids: list[str] = []

        max_turns = 20
        for turn in range(max_turns):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=self.tools,
                messages=messages,
            )

            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            for block in assistant_content:
                if block.type == "text":
                    yield ("thinking", block.text)

            tool_use_blocks = [b for b in assistant_content if b.type == "tool_use"]
            if not tool_use_blocks:
                break

            tool_results = []
            for block in tool_use_blocks:
                yield ("tool_call", {"name": block.name, "input": block.input})

                result, side_effect = self._execute_tool(block.name, block.input)
                yield ("tool_result", {"name": block.name, "result": result})

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

                step_desc = f"{block.name}({json.dumps(block.input, ensure_ascii=False)[:120]})"
                diagnosis_steps.append(step_desc)

                if side_effect == "search":
                    similar_case_ids = [c["id"] for c in result.get("cases", [])]
                elif side_effect == "save":
                    saved_case_id = result.get("case_id")

            messages.append({"role": "user", "content": tool_results})

        final_text = "\n".join(
            b.text for b in assistant_content if hasattr(b, "text") and b.type == "text"
        )

        saved_case = self.memory.get(saved_case_id) if saved_case_id else None

        result = DiagnosisResult(
            case_id=saved_case.id if saved_case else "unknown",
            root_cause=saved_case.root_cause if saved_case else final_text,
            confidence=saved_case is not None,
            diagnosis_steps=diagnosis_steps,
            fix_suggestion=saved_case.fix_suggestion if saved_case else "",
            similar_cases_found=len(similar_case_ids),
            reasoning=final_text,
        )

        yield ("done", result)

    def _execute_tool(self, name: str, params: dict) -> tuple[dict, str | None]:
        if name == "search_memory":
            results = self.memory.search(query=params["query"], top_k=params.get("top_k", 5))
            return {
                "found": len(results),
                "cases": [
                    {
                        "id": r.case.id,
                        "title": r.case.title,
                        "score": r.score,
                        "root_cause": r.case.root_cause,
                        "fix_suggestion": r.case.fix_suggestion,
                        "tags": r.case.tags,
                    }
                    for r in results
                ],
            }, "search"

        elif name == "save_to_memory":
            case = BugCase(
                title=params["title"],
                symptoms=params["symptoms"],
                error_log=params.get("error_log", ""),
                root_cause=params["root_cause"],
                fix_suggestion=params["fix_suggestion"],
                severity=Severity(params.get("severity", "medium")),
                status=BugStatus.ROOT_CAUSE_FOUND,
                tags=params.get("tags", []),
                environment=params.get("environment", {}),
                diagnosis_steps=params.get("diagnosis_steps", []),
                similar_case_ids=params.get("similar_case_ids", []),
            )
            self.memory.save(case)
            return {"saved": True, "case_id": case.id}, "save"

        elif name == "search_code" and self.project_path:
            return search_code(
                pattern=params["pattern"],
                project_path=self.project_path,
                file_type=params.get("file_type", ""),
            ), None

        elif name == "read_file" and self.project_path:
            return read_file(
                file_path=params["file_path"],
                project_path=self.project_path,
                start_line=params.get("start_line", 0),
                end_line=params.get("end_line", 100),
            ), None

        elif name == "list_project_structure" and self.project_path:
            return list_project_structure(
                project_path=self.project_path,
                depth=params.get("depth", 3),
            ), None

        elif name in ("search_code", "read_file", "list_project_structure"):
            return {"error": "No project path configured. Use --project to connect a codebase."}, None

        return {"error": f"Unknown tool: {name}"}, None
