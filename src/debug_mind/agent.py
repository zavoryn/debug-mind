"""Diagnostic Agent — the brain of DebugMind.

Uses Claude with tool use (function calling) to diagnose bugs.
The agent is injected with two categories of tools:
  1. Memory tools: search & save bug cases from the experiential memory.
  2. Diagnostic skills: code search, log analysis, config inspection, etc.

The core loop:
  User describes bug → Agent searches memory → If similar case found, fast-path
  → Otherwise, full diagnostic reasoning → Save case to memory → Return result.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import anthropic

from debug_mind.schemas import BugCase, DiagnosisResult, Severity, BugStatus
from debug_mind.memory.store import MemoryStore

SYSTEM_PROMPT = """You are DebugMind, an expert bug diagnosis agent with access to a memory of past bug cases.

Your workflow:
1. First, ALWAYS call `search_memory` to check if similar bugs have been diagnosed before.
2. If similar cases are found, reference them in your diagnosis and build on past solutions.
3. If no similar cases exist, perform a thorough systematic diagnosis.
4. After reaching a conclusion, call `save_to_memory` to persist this case for future reference.

When diagnosing:
- Start from symptoms (error messages, stack traces, observed behavior)
- Follow a systematic root cause analysis approach
- Consider environment context (language, framework, versions)
- Provide clear, actionable fix suggestions
- Tag cases with searchable keywords

Be concise but thorough. Think step by step."""

MEMORY_SEARCH_TOOL = {
    "name": "search_memory",
    "description": "Search past bug cases in the experiential memory. Use this FIRST for every new bug to check if similar issues were solved before.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language description of the bug symptoms, error, or keywords to search for.",
            },
            "top_k": {
                "type": "integer",
                "description": "Max number of similar cases to return (default 5).",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}

MEMORY_SAVE_TOOL = {
    "name": "save_to_memory",
    "description": "Save a diagnosed bug case to the experiential memory for future reference. Call this AFTER completing diagnosis.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short searchable title"},
            "symptoms": {"type": "string", "description": "What was observed"},
            "error_log": {"type": "string", "description": "Raw error log or stack trace"},
            "root_cause": {"type": "string", "description": "The identified root cause"},
            "fix_suggestion": {"type": "string", "description": "How to fix it"},
            "severity": {
                "type": "string",
                "enum": ["critical", "high", "medium", "low"],
                "description": "Bug severity",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Searchable tags",
            },
            "environment": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Environment context (language, framework, version, etc.)",
            },
            "diagnosis_steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Steps taken during diagnosis",
            },
            "similar_case_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "IDs of similar past cases",
            },
        },
        "required": ["title", "symptoms", "root_cause", "fix_suggestion"],
    },
}

# Skill tools — injected based on context
CODE_SEARCH_TOOL = {
    "name": "search_code",
    "description": "Search the codebase for patterns, function definitions, or error-related code. Use when you need to locate relevant source code.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Search pattern (regex supported)"},
            "file_type": {"type": "string", "description": "File extension filter (e.g. 'java', 'py', 'ts')"},
        },
        "required": ["pattern"],
    },
}

LOG_ANALYSIS_TOOL = {
    "name": "analyze_log",
    "description": "Analyze an error log or stack trace to extract key information: exception types, source locations, and error patterns.",
    "input_schema": {
        "type": "object",
        "properties": {
            "log_content": {"type": "string", "description": "The log content to analyze"},
        },
        "required": ["log_content"],
    },
}


class DiagnosticAgent:
    """The core agent that diagnoses bugs using memory + LLM reasoning."""

    def __init__(
        self,
        memory: MemoryStore,
        model: str = "claude-sonnet-4-20250514",
        api_key: str | None = None,
    ):
        self.memory = memory
        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

        self.tools = [MEMORY_SEARCH_TOOL, MEMORY_SAVE_TOOL, CODE_SEARCH_TOOL, LOG_ANALYSIS_TOOL]

    def diagnose(
        self,
        bug_description: str,
        error_log: str = "",
        environment: dict[str, str] | None = None,
    ) -> DiagnosisResult:
        """Run the full diagnostic loop on a bug report.

        This is the main entry point. It:
        1. Sends the bug to Claude with all tools available
        2. Claude searches memory first, then reasons through the diagnosis
        3. Claude saves the result back to memory
        4. Returns the structured diagnosis
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

Please diagnose this bug. Remember to:
1. Search memory first for similar cases
2. Analyze systematically
3. Save the result to memory when done"""

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        diagnosis_steps: list[str] = []
        saved_case_id: str | None = None
        similar_case_ids: list[str] = []

        # Agent loop — Claude calls tools, we execute and feed back
        max_turns = 15
        for _ in range(max_turns):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=self._build_tool_definitions(),
                messages=messages,
            )

            # Collect assistant response
            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            # Check if we're done (no more tool calls)
            tool_use_blocks = [b for b in assistant_content if b.type == "tool_use"]
            if not tool_use_blocks:
                break

            # Execute tool calls
            tool_results = []
            for block in tool_use_blocks:
                result, side_effect = self._execute_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
                diagnosis_steps.append(f"[{block.name}] {json.dumps(block.input, ensure_ascii=False)[:200]}")

                if side_effect == "search":
                    similar_case_ids = [c["id"] for c in result.get("cases", [])]
                elif side_effect == "save":
                    saved_case_id = result.get("case_id")

            messages.append({"role": "user", "content": tool_results})

        # Extract final text response
        final_text = "\n".join(
            b.text for b in assistant_content if hasattr(b, "text") and b.type == "text"
        )

        # Fetch saved case from memory
        saved_case = self.memory.get(saved_case_id) if saved_case_id else None

        return DiagnosisResult(
            case_id=saved_case.id if saved_case else "unknown",
            root_cause=saved_case.root_cause if saved_case else final_text,
            confidence=0.85,
            diagnosis_steps=diagnosis_steps,
            fix_suggestion=saved_case.fix_suggestion if saved_case else "",
            similar_cases_found=len(similar_case_ids),
            reasoning=final_text,
        )

    def _build_tool_definitions(self) -> list[dict]:
        return self.tools

    def _execute_tool(self, name: str, params: dict) -> tuple[dict, str | None]:
        """Execute a tool call from the agent.

        Returns (result_dict, side_effect_type) where side_effect_type is
        'search', 'save', or None — used to track state across the agent loop.
        """
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

        elif name == "search_code":
            return {
                "note": "Code search not connected to a live codebase in this mode. "
                "Provide a project path via CLI to enable.",
                "pattern": params["pattern"],
            }, None

        elif name == "analyze_log":
            log = params["log_content"]
            lines = log.strip().split("\n")
            exceptions = [l for l in lines if "Exception" in l or "Error" in l or "error" in l]
            return {
                "total_lines": len(lines),
                "error_lines": len(exceptions),
                "exceptions": exceptions[:10],
                "analysis": "Extracted key error lines from the log.",
            }, None

        return {"error": f"Unknown tool: {name}"}, None
