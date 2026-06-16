"""Diagnostic Agent — the brain of DebugMind.

Uses LLM with tool use to diagnose bugs via pluggable providers (Anthropic, OpenAI, etc.).
Two modes:
  1. Full mode: connected to a real codebase, can search code, read files.
  2. Offline mode: only uses memory search — no codebase access.

The agent loop follows the ReAct pattern:
  Observe (bug report) → Think → Act (call tool) → Observe (tool result) → ...
  → Final diagnosis → Save to memory.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Generator

from debug_mind.schemas import BugCase, DiagnosisResult, BugStatus
from debug_mind.memory.store import MemoryStore
from debug_mind.tools.schemas import (
    MEMORY_TOOLS as _MEMORY_TOOLS,
    CODEBASE_TOOLS as _CODEBASE_TOOLS,
)
from debug_mind.skills.registry import (
    Skill,
    SkillRegistry,
    get_default_registry,
)
from debug_mind.budget import TokenBudget
from debug_mind.observability.logger import get_logger, _try_otel_span
from debug_mind.sanitize import sanitize_bug_input
from debug_mind.providers.base import LLMProvider
from debug_mind.providers.anthropic_provider import AnthropicProvider

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

_log = get_logger("agent")


SYSTEM_PROMPT = """You are DebugMind, an expert bug diagnosis agent with access to:
1. **Experiential Memory** — a knowledge base of past bug diagnoses.
2. **Codebase Access** — you can search and read source code in the project.
3. **Log Analysis** — you can analyze error logs and stack traces.

## Your Workflow
1. **Search Memory** — ALWAYS call `search_memory` first to check for similar past bugs.
2. **Understand the Codebase** — If a project path is available, call `list_project_structure` and `search_code` to locate relevant code.
3. **Read Relevant Code** — Use `read_file` to inspect suspicious code referenced in stack traces.
4. **Analyze** — Correlate symptoms, error logs, source code, and any similar past cases.
5. **Fix (if project available)** — Use `propose_patch` to generate a diff, then `apply_and_test` to validate in a sandbox.
   - If tests **pass**: the fix is confirmed. Save with status FIXED.
   - If tests **fail**: record this patch as a dead-end in `patch_attempts` (include the diff and test output), then try a different approach. Never repeat a proven-broken fix.
6. **Save** — Call `save_to_memory` to persist the diagnosis (and any patch_attempts) for future reference.

## Diagnosis Guidelines
- Start from the stack trace — find the exact file and line.
- Check for null/empty returns, missing error handling, resource leaks.
- Consider framework-specific pitfalls (Spring autowiring, async context, etc.).
- Reference similar past cases when available — they shortcut the diagnosis.
- Be specific: cite file names, line numbers, method names.
- Provide copy-paste-ready fix suggestions.
- Prefer verified=True cases; if only unverified cases match, explain and lower confidence.

Respond in the user's language. Be concise but thorough."""

# ── Tool Definitions (imported from shared schema module) ─────────────

MEMORY_TOOLS = _MEMORY_TOOLS
CODEBASE_TOOLS = _CODEBASE_TOOLS


@dataclass
class AgentRunState:
    """Mutable state for one diagnosis run.

    Keeping these fields together makes tool side effects explicit instead of
    relying on scattered loop variables or the model remembering prior results.
    """

    diagnosis_steps: list[str] = field(default_factory=list)
    saved_case_id: str | None = None
    similar_case_ids: list[str] = field(default_factory=list)
    failed_patch_attempts: list[dict[str, str]] = field(default_factory=list)

    def input_for_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Return params augmented with buffered state when needed."""
        if tool_name != "save_to_memory" or not self.failed_patch_attempts:
            return params

        merged = dict(params)
        existing = merged.get("patch_attempts") or []
        if not isinstance(existing, list):
            existing = []
        merged["patch_attempts"] = _merge_patch_attempts(
            existing,
            self.failed_patch_attempts,
        )
        return merged

    def record_tool_result(
        self,
        tool_name: str,
        params: dict[str, Any],
        result: dict[str, Any],
        side_effect: str | None,
    ) -> None:
        """Update run state after a tool finishes."""
        if side_effect == "search":
            self.similar_case_ids = [c["id"] for c in result.get("cases", [])]
        elif side_effect == "save":
            self.saved_case_id = result.get("case_id")

        if tool_name == "apply_and_test" or side_effect == "patch_test":
            attempt = _patch_attempt_from_result(params, result)
            if attempt:
                self.failed_patch_attempts = _merge_patch_attempts(
                    self.failed_patch_attempts,
                    [attempt],
                )


def _merge_patch_attempts(
    existing: list[dict[str, Any]],
    buffered: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Append buffered patch attempts without duplicating the same failed diff."""
    merged: list[dict[str, str]] = [
        {str(k): str(v) for k, v in item.items() if v is not None}
        for item in existing
        if isinstance(item, dict)
    ]
    seen = {
        (item.get("diff", ""), item.get("test_output", ""), item.get("reason", ""))
        for item in merged
    }

    for item in buffered:
        key = (item.get("diff", ""), item.get("test_output", ""), item.get("reason", ""))
        if key in seen:
            continue
        merged.append(dict(item))
        seen.add(key)
    return merged


def _patch_attempt_from_result(
    params: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, str] | None:
    """Build a durable failed patch attempt from apply_and_test output."""
    if result.get("passed") is not False:
        return None

    diff = str(result.get("patch_diff") or params.get("patch_diff") or "")
    test_output = str(result.get("output") or result.get("error") or "")
    if not diff and not test_output:
        return None

    reason = str(result.get("error") or "tests failed")
    attempt = {
        "diff": diff,
        "test_output": test_output,
        "reason": reason,
    }
    return {k: v for k, v in attempt.items() if v}


def _confidence_for_case(case: BugCase | None) -> float:
    """Map persisted case status to user-facing diagnosis confidence."""
    if case is None:
        return 0.0
    if case.status == BugStatus.UNRESOLVED:
        return 0.0
    if case.status in {BugStatus.REPORTED, BugStatus.DIAGNOSING}:
        return 0.3
    return 1.0


class DiagnosticAgent:
    """The core agent that diagnoses bugs using memory + codebase access + LLM."""

    def __init__(
        self,
        memory: MemoryStore,
        project_path: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        provider: LLMProvider | None = None,
        budget: TokenBudget | None = None,
        no_retry: bool = False,
        skills: list[str] | None = None,
        skill_registry: SkillRegistry | None = None,
    ):
        self.memory = memory
        self.project_path = project_path
        self.budget = budget
        self.no_retry = no_retry
        # Provider: explicit > env choice > default Anthropic
        self.provider = provider or self._create_provider(api_key)
        self.model = model or self.provider.default_model
        # Cost estimation must price against the model actually in use, not
        # the TokenBudget default — otherwise non-Claude runs report ~20x off.
        if self.budget is not None:
            self.budget.model = self.model

        # Skill loading — default behaviour mirrors pre-Phase-6:
        #   no skills arg → memory + (codebase iff project_path)
        # Explicit list bypasses the auto-codebase rule.
        self._skill_registry = skill_registry or get_default_registry()
        if skills is None:
            skill_names = ["memory"]
            if project_path:
                skill_names.append("codebase")
                skill_names.append("patch")
        else:
            skill_names = list(skills)
        self._skills: list[Skill] = self._skill_registry.load_by_names(skill_names)
        self._skill_names = skill_names

        context = {"memory": memory, "project_path": project_path}
        self.tools = []
        self._tool_skill_map: dict[str, Skill] = {}
        self._tool_schemas: dict[str, dict] = {}  # name → input_schema for validation
        for skill in self._skills:
            for tool in skill.get_tools(context):
                if tool["name"] in self._tool_skill_map:
                    raise ValueError(
                        f"Tool name conflict: {tool['name']!r} provided by both "
                        f"{self._tool_skill_map[tool['name']].name!r} and {skill.name!r}"
                    )
                self.tools.append(tool)
                self._tool_skill_map[tool["name"]] = skill
                self._tool_schemas[tool["name"]] = tool.get("input_schema", {})

        self._retry_decorator = self._create_retry_decorator()

        _log.info(
            "agent initialised",
            extra={
                "skills": skill_names,
                "tool_count": len(self.tools),
                "project": bool(project_path),
            },
        )

    def _create_provider(self, api_key: str | None) -> LLMProvider:
        """Create provider based on DEBUG_MIND_PROVIDER env or default to Anthropic.

        Supported values for DEBUG_MIND_PROVIDER:
            anthropic  (default) — Claude via ANTHROPIC_API_KEY
            openai               — GPT via OPENAI_API_KEY
            deepseek             — DeepSeek via DEEPSEEK_API_KEY
            glm / zhipu          — Zhipu GLM via ZHIPU_API_KEY
        """
        choice = os.environ.get("DEBUG_MIND_PROVIDER", "anthropic").lower()
        if choice == "openai":
            from debug_mind.providers.openai_provider import OpenAIProvider

            return OpenAIProvider(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
        if choice == "deepseek":
            from debug_mind.providers.deepseek_provider import DeepSeekProvider

            return DeepSeekProvider(api_key=api_key or os.environ.get("DEEPSEEK_API_KEY"))
        if choice in ("glm", "zhipu"):
            from debug_mind.providers.glm_provider import GLMProvider

            return GLMProvider(
                api_key=api_key or os.environ.get("ZHIPU_API_KEY") or os.environ.get("GLM_API_KEY")
            )
        return AnthropicProvider(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def _create_retry_decorator(self):
        """Build a retry decorator using the provider's is_retryable method."""
        _provider = self.provider

        def _is_retryable(exc: BaseException) -> bool:
            return _provider.is_retryable(exc)

        return retry(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            reraise=True,
        )

    def _call_provider(self, **kwargs):
        """Call the LLM provider with retry on transient errors."""

        @self._retry_decorator
        def _do_call():
            return self.provider.create_message(**kwargs)

        return _do_call()

    def diagnose(
        self,
        bug_description: str,
        error_log: str = "",
        environment: dict[str, str] | None = None,
    ) -> DiagnosisResult:
        """Run the full diagnostic loop. Returns the final DiagnosisResult."""
        result = None
        for event_type, data in self._run_loop(bug_description, error_log, environment):
            if event_type == "done":
                result = data
        return result  # type: ignore[return-value]

    def diagnose_stream(
        self,
        bug_description: str,
        error_log: str = "",
        environment: dict[str, str] | None = None,
    ) -> Generator[tuple[str, Any], None, None]:
        """Streaming variant — yields (event_type, data) tuples.

        Yields:
            ("thinking", str)             — agent's text output
            ("tool_call", dict)           — {"name": ..., "input": ...}
            ("tool_result", dict)         — {"name": ..., "result": ...}
            ("done", DiagnosisResult)     — final result
        """
        yield from self._run_loop(bug_description, error_log, environment, stream=True)

    def _build_user_message(
        self, bug_description: str, error_log: str, environment: dict[str, str] | None
    ) -> str:
        env_str = "\n".join(f"- {k}: {v}" for k, v in (environment or {}).items())
        project_line = f"\n**Project Path:** {self.project_path}" if self.project_path else ""
        return f"""## New Bug Report

**Description:** {bug_description}

**Error Log:**
```
{error_log or "(no log provided)"}
```

**Environment:**
{env_str or "- not specified"}{project_line}

Diagnose this bug. Remember: search memory first, then inspect code if available, then save the result."""

    def _run_loop(
        self,
        bug_description: str,
        error_log: str,
        environment: dict[str, str] | None,
        stream: bool = False,
    ) -> Generator[tuple[str, Any], None, None]:
        """Core ReAct loop shared by diagnose() and diagnose_stream()."""
        trace_id = uuid.uuid4().hex[:16]
        _log.info("diagnosis started", extra={"trace_id": trace_id})
        _try_otel_span(trace_id)

        # Sanitize inputs before building prompt
        bug_description, error_log, environment, _ = sanitize_bug_input(
            description=bug_description,
            error_log=error_log,
            environment=environment,
        )

        user_message = self._build_user_message(bug_description, error_log, environment)
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        state = AgentRunState()

        max_turns = 20
        max_wall_secs = float(os.environ.get("DEBUG_MIND_MAX_WALL_SECS", "300"))
        _deadline = time.monotonic() + max_wall_secs
        for _turn in range(max_turns):
            if stream:
                yield ("turn", {"turn": _turn + 1, "max_turns": max_turns})
            # Context compression: truncate old tool results before they fill the window
            messages = _compress_messages(messages)
            if time.monotonic() > _deadline:
                _log.warning(
                    "wall-clock timeout reached",
                    extra={"trace_id": trace_id, "max_wall_secs": max_wall_secs},
                )
                if stream:
                    yield ("thinking", f"\n[Timeout: diagnosis exceeded {max_wall_secs:.0f}s]")
                partial = self._build_partial_result(
                    state.diagnosis_steps,
                    state.saved_case_id,
                    state.similar_case_ids,
                    assistant_content=None,
                    budget_reason=f"wall-clock timeout ({max_wall_secs:.0f}s)",
                    patch_attempts=state.failed_patch_attempts,
                )
                yield ("done", partial)
                return
            try:
                # D4: Prompt caching — cache system prompt and last tool to reduce cost
                cache_control = {"type": "ephemeral"}
                cached_tools = []
                for i, tool in enumerate(self.tools):
                    if i == len(self.tools) - 1:
                        cached_tools.append({**tool, "cache_control": cache_control})
                    else:
                        cached_tools.append(tool)

                call_fn = self._call_provider if not self.no_retry else self.provider.create_message
                response = call_fn(
                    model=self.model,
                    max_tokens=4096,
                    system=[
                        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": cache_control}
                    ],
                    tools=cached_tools,
                    messages=messages,
                )
            except Exception as e:
                _log.warning(
                    "API error after retries", extra={"trace_id": trace_id, "error": str(e)}
                )
                if stream:
                    yield ("thinking", f"\n[API Error: {e}]")
                # Save partial diagnosis with what we have
                partial = self._build_partial_result(
                    state.diagnosis_steps,
                    state.saved_case_id,
                    state.similar_case_ids,
                    assistant_content=None,
                    budget_reason=f"API error: {e}",
                    patch_attempts=state.failed_patch_attempts,
                )
                yield ("done", partial)
                return

            # Budget tracking
            if self.budget and response.usage:
                self.budget.record(response.usage)
                _log.info(
                    "LLM response",
                    extra={
                        "trace_id": trace_id,
                        "model": self.model,
                        "tokens_in": getattr(response.usage, "input_tokens", 0),
                        "tokens_out": getattr(response.usage, "output_tokens", 0),
                    },
                )
                exceeded, reason = self.budget.is_exceeded()
                if exceeded:
                    if stream:
                        yield ("thinking", f"\n[Budget exceeded: {reason}]")
                    # Save partial diagnosis before breaking
                    partial = self._build_partial_result(
                        state.diagnosis_steps,
                        state.saved_case_id,
                        state.similar_case_ids,
                        assistant_content=None,
                        budget_reason=reason,
                        patch_attempts=state.failed_patch_attempts,
                    )
                    yield ("done", partial)
                    return

            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            # Emit text blocks
            for block in assistant_content:
                if block.type == "text" and stream:
                    yield ("thinking", block.text)

            tool_use_blocks = [b for b in assistant_content if b.type == "tool_use"]
            if not tool_use_blocks:
                break

            # Execute tools
            tool_results = []
            for block in tool_use_blocks:
                tool_input = state.input_for_tool(block.name, block.input)
                if stream:
                    yield ("tool_call", {"name": block.name, "input": tool_input})

                t0 = time.monotonic()
                result, side_effect = self._execute_tool(block.name, tool_input)
                latency_ms = int((time.monotonic() - t0) * 1000)

                _log.info(
                    "tool call",
                    extra={
                        "trace_id": trace_id,
                        "tool": block.name,
                        "latency_ms": latency_ms,
                        "found": result.get("found") if isinstance(result, dict) else None,
                        "saved": result.get("saved") if isinstance(result, dict) else None,
                    },
                )

                if stream:
                    yield ("tool_result", {"name": block.name, "result": result})

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

                step_desc = f"{block.name}({json.dumps(tool_input, ensure_ascii=False)[:120]})"
                state.diagnosis_steps.append(step_desc)
                state.record_tool_result(block.name, tool_input, result, side_effect)

            messages.append({"role": "user", "content": tool_results})

        # Build final result
        final_text = "\n".join(
            b.text for b in assistant_content if hasattr(b, "text") and b.type == "text"
        )
        saved_case = self.memory.get(state.saved_case_id) if state.saved_case_id else None

        # D5: If final_text is empty (e.g. max_turns exhausted with all tool_use),
        # fall back to saved_case fields
        if not final_text and saved_case:
            final_text = f"Root cause: {saved_case.root_cause}\nFix: {saved_case.fix_suggestion}"

        if not saved_case and state.failed_patch_attempts:
            saved_case = self._save_unresolved_patch_attempts(
                bug_description=bug_description,
                error_log=error_log,
                final_text=final_text,
                state=state,
            )

        # Mark adopted similar cases as used
        for cid in state.similar_case_ids:
            self.memory.mark_used(cid)

        diag = DiagnosisResult(
            case_id=saved_case.id if saved_case else "unknown",
            root_cause=saved_case.root_cause if saved_case else final_text,
            confidence=_confidence_for_case(saved_case),
            diagnosis_steps=state.diagnosis_steps,
            fix_suggestion=saved_case.fix_suggestion if saved_case else "",
            similar_cases_found=len(state.similar_case_ids),
            reasoning=final_text,
        )

        yield ("done", diag)

    def _build_partial_result(
        self,
        diagnosis_steps: list[str],
        saved_case_id: str | None,
        similar_case_ids: list[str],
        assistant_content: list | None,
        budget_reason: str,
        patch_attempts: list[dict[str, str]] | None = None,
    ) -> DiagnosisResult:
        """Build a partial DiagnosisResult when budget is exceeded.

        Saves what we have as UNRESOLVED so the user's money wasn't wasted.
        """
        final_text = ""
        if assistant_content:
            final_text = "\n".join(
                b.text for b in assistant_content if hasattr(b, "text") and b.type == "text"
            )

        saved_case = self.memory.get(saved_case_id) if saved_case_id else None
        reasoning = (
            f"[Budget exceeded: {budget_reason}]\n{final_text}"
            if final_text
            else f"[Budget exceeded: {budget_reason}]"
        )

        if not saved_case and diagnosis_steps:
            case = BugCase(
                title="Partial diagnosis (budget exceeded)",
                symptoms="Diagnosis interrupted by budget limit",
                root_cause=final_text
                or "Incomplete — budget exceeded before root cause identified",
                fix_suggestion="",
                status=BugStatus.UNRESOLVED,
                diagnosis_steps=diagnosis_steps,
                similar_case_ids=similar_case_ids,
                patch_attempts=patch_attempts or [],
            )
            self.memory.save(case)
            saved_case = case

        return DiagnosisResult(
            case_id=saved_case.id if saved_case else "unknown",
            root_cause=saved_case.root_cause if saved_case else final_text,
            confidence=0.0,
            diagnosis_steps=diagnosis_steps,
            fix_suggestion=saved_case.fix_suggestion if saved_case else "",
            similar_cases_found=len(similar_case_ids),
            reasoning=reasoning,
        )

    def _save_unresolved_patch_attempts(
        self,
        bug_description: str,
        error_log: str,
        final_text: str,
        state: AgentRunState,
    ) -> BugCase:
        """Persist buffered failed patches when the model never saves them.

        This is a safety net for the dead-end memory loop. The model still gets
        the first chance to save a high-quality case; this method only writes a
        minimal unresolved case so failed patch evidence is not lost.
        """
        case = BugCase(
            title="Unresolved failed patch attempt",
            symptoms=bug_description,
            error_log=error_log,
            root_cause=final_text or "Patch validation failed before a final diagnosis was saved",
            fix_suggestion="Avoid repeating the recorded failed patch attempts.",
            status=BugStatus.UNRESOLVED,
            diagnosis_steps=state.diagnosis_steps,
            similar_case_ids=state.similar_case_ids,
            patch_attempts=state.failed_patch_attempts,
        )
        self.memory.save(case)
        state.saved_case_id = case.id
        return case

    def _execute_tool(self, name: str, params: dict) -> tuple[dict, str | None]:
        """Execute a single tool call by dispatching to the owning skill.

        Validates LLM-generated parameters against the tool's input_schema BEFORE
        execution. On failure, returns a structured error dict (not raises) so the
        model can read it as a tool_result and self-correct its parameters — this is
        the agentic tool-error self-healing pattern (inspired by MiniCode's tool
        validation layer).
        """
        skill = self._tool_skill_map.get(name)
        if skill is None:
            return {"error": f"Unknown tool: {name}"}, None

        # ── Input validation ───────────────────────────────────────────────
        schema = self._tool_schemas.get(name, {})
        validation_error = _validate_tool_params(name, params, schema)
        if validation_error:
            _log.warning(
                "tool param validation failed",
                extra={"tool": name, "error": validation_error},
            )
            # Return structured error — NOT an exception — so the LLM sees it
            # as a tool_result and can self-correct rather than the loop crashing.
            return {
                "error": validation_error,
                "hint": "Fix the parameters and retry this tool call.",
            }, None

        context = {"memory": self.memory, "project_path": self.project_path}
        return skill.execute(name, params, context)


# ── Context compression ───────────────────────────────────────────────────────
#
# Design: inspired by MiniCode's snip-compact pattern (which itself reflects
# Claude Code's context management approach): deterministic middle-history
# trimming that *protects* critical turns from removal.
#
# Two tiers of protection (never truncated):
#   1. First message  — the original bug report; always needed for grounding.
#   2. Last 2 messages — most recent reasoning + tool results; always needed.
#   3. "Anchor" turns  — any turn containing save_to_memory or an error marker;
#      these record diagnosis conclusions and failure signals that must survive.
#
# Non-anchor middle turns have their tool_result content truncated first
# (keep first _TOOL_RESULT_KEEP_CHARS chars + marker). This mirrors MiniCode's
# tool-result-storage: large outputs are replaced with a preview, but the turn
# itself stays so the model can still see the tool was called and what it found.

# Soft limit: total chars before we start trimming.
# ~60K chars ≈ ~15K tokens, leaving headroom in a 200K-token context window.
_CONTEXT_CHAR_LIMIT = 60_000
# Chars to keep from each old tool result when truncating.
_TOOL_RESULT_KEEP_CHARS = 300
# Keywords that mark a turn as an "anchor" — protected from truncation.
_ANCHOR_TOOLS = {"save_to_memory", "apply_and_test"}
_ERROR_MARKERS = {"error", "failed", "exception", "traceback", "budget exceeded"}


def _is_anchor_message(msg: dict[str, Any]) -> bool:
    """Return True if this message must not be truncated.

    Anchors: turns that contain save_to_memory / apply_and_test tool calls,
    or tool results that carry error/failure signals. Mirrors MiniCode's
    protection of edit-file and error turns in snip compact.
    """
    content = msg.get("content")
    if not isinstance(content, list):
        return False
    for part in content:
        # Protect tool_use blocks for anchor tools
        if part.get("type") == "tool_use" and part.get("name") in _ANCHOR_TOOLS:
            return True
        # Protect tool_result blocks that contain error markers
        if part.get("type") == "tool_result":
            raw = str(part.get("content", "")).lower()
            if any(marker in raw for marker in _ERROR_MARKERS):
                return True
    return False


# ── Tool parameter validation ─────────────────────────────────────────────────
#
# LLMs are probabilistic — they can generate tool calls with missing required
# fields, wrong types, or malformed values. Without validation, the first
# KeyError / TypeError propagates as an exception, killing the entire loop.
#
# Instead: validate params against the tool's input_schema, return a structured
# error string. The loop then feeds it back as a tool_result, and the model
# reads "Missing required field 'query'" and self-corrects its next call.
#
# This is the same pattern MiniCode uses (zod schema validation before execution)
# adapted for Python with a zero-dependency implementation.

_SCHEMA_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": (int, float),  # type: ignore[assignment]
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _validate_tool_params(
    tool_name: str,
    params: dict[str, Any],
    schema: dict[str, Any],
) -> str | None:
    """Validate LLM-generated tool params against the tool's input_schema.

    Returns an error message string if invalid, None if valid.
    Intentionally lenient: only checks required fields and basic types.
    Extra fields are allowed (LLMs sometimes add helpful extras).
    """
    if not schema:
        return None  # no schema to validate against, pass through

    required: list[str] = schema.get("required", [])
    properties: dict[str, Any] = schema.get("properties", {})

    # 1. Check required fields are present and non-None
    missing = [f for f in required if f not in params or params[f] is None]
    if missing:
        return (
            f"Tool '{tool_name}' is missing required field(s): {missing}. "
            f"Required: {required}. Got keys: {list(params.keys())}."
        )

    # 2. Check basic types for fields that are present
    type_errors: list[str] = []
    for param_name, value in params.items():
        if param_name not in properties or value is None:
            continue
        expected_type_str = properties[param_name].get("type")
        if not expected_type_str:
            continue
        expected_type = _SCHEMA_TYPE_MAP.get(expected_type_str)
        if expected_type is None:
            continue
        if not isinstance(value, expected_type):
            type_errors.append(
                f"'{param_name}' should be {expected_type_str}, got {type(value).__name__}"
            )

    if type_errors:
        return f"Tool '{tool_name}' parameter type error(s): {'; '.join(type_errors)}."

    return None


def _compress_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trim old tool_result content when the conversation grows too large.

    Protection rules (never modified):
    - messages[0]       : original bug report
    - messages[-2:]     : most recent assistant turn + tool results
    - anchor turns      : save_to_memory, apply_and_test, error turns

    Non-anchor middle turns have their tool_result content truncated to
    _TOOL_RESULT_KEEP_CHARS, keeping a [truncated] marker so the model
    still knows the tool was called and what it partially found.
    """
    total_chars = sum(len(str(m.get("content", ""))) for m in messages)
    if total_chars <= _CONTEXT_CHAR_LIMIT:
        return messages

    result = list(messages)
    protected_tail = max(0, len(result) - 2)

    for i in range(1, protected_tail):
        if total_chars <= _CONTEXT_CHAR_LIMIT:
            break
        if _is_anchor_message(result[i]):
            continue  # never touch anchor turns

        msg = result[i]
        content = msg.get("content")
        if not isinstance(content, list):
            continue

        new_parts = []
        changed = False
        for part in content:
            if part.get("type") == "tool_result":
                raw = part.get("content", "")
                if isinstance(raw, str) and len(raw) > _TOOL_RESULT_KEEP_CHARS:
                    trimmed = raw[:_TOOL_RESULT_KEEP_CHARS] + " …[truncated for context]"
                    new_parts.append({**part, "content": trimmed})
                    total_chars -= len(raw) - len(trimmed)
                    changed = True
                    continue
            new_parts.append(part)

        if changed:
            result[i] = {**msg, "content": new_parts}

    if total_chars > _CONTEXT_CHAR_LIMIT:
        _log.warning(
            "context still large after compression",
            extra={"total_chars": total_chars, "limit": _CONTEXT_CHAR_LIMIT},
        )
    return result
