"""Agent trajectory evaluation — measures end-to-end agent behaviour.

Why this exists:
The existing `evaluation/benchmark.py` measures *retrieval* quality (hit@k,
MRR) — i.e., would the memory store surface the right past case? That tells
us nothing about how the agent actually *behaves* when handed a fresh bug:
how many tool calls does it take? how many tokens does it burn? does it
ever arrive at a correct diagnosis? Those are the questions you get asked
when you ship an LLM agent into production — and most teams have no
reproducible answer. This module fills that gap.

Three evaluation dimensions — matching real production Agent monitoring:

  1. Outcome eval   — did the agent get the right answer?
                      (keyword-match today; LLM-as-judge is future work)

  2. Process eval   — did the agent reason correctly?
                      workflow compliance: search_memory first?
                      efficiency: redundant tool calls? save_to_memory called?

  3. Regression detection — is this run better/worse than the saved baseline?
                      compare --baseline prevents silent quality regressions.

Invoke manually (each run hits the LLM API):

    debug-mind eval --trajectory --sample 5
    debug-mind eval --trajectory --save-baseline
    debug-mind eval --trajectory --compare-baseline
"""

from __future__ import annotations

import json
import math
import re
import shutil
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import DiagnosisResult

from evaluation.dataset import BenchmarkCase, load_all_cases


RESULTS_DIR = Path(__file__).parent / "results"
BASELINE_FILE = RESULTS_DIR / "trajectory_baseline.json"

# Correctness threshold: fraction of expected keywords that must appear in
# the agent's final root_cause + fix_suggestion text. Tunable per use case.
DEFAULT_CORRECTNESS_THRESHOLD = 0.5

# If correctness_rate drops more than this vs baseline, flag as regression.
REGRESSION_THRESHOLD = 0.05


# ── Failure mode taxonomy ─────────────────────────────────────────────────


class FailureMode(str, Enum):
    """Why did the agent fail? Used to prioritise improvements."""

    BUDGET_EXCEEDED = "budget_exceeded"  # hit token/cost/time limit
    RETRIEVAL_MISS = "retrieval_miss"  # search_memory found nothing useful
    REASONING_ERROR = "reasoning_error"  # had context, reached wrong conclusion
    WORKFLOW_VIOLATION = "workflow_violation"  # didn't call search_memory first
    NO_SAVE = "no_save"  # diagnosed but never called save_to_memory
    API_ERROR = "api_error"  # provider returned error
    UNKNOWN = "unknown"


# ── Process (tool-call) analysis ─────────────────────────────────────────


@dataclass
class ToolCallAnalysis:
    """How did the agent behave during the trajectory?

    These metrics answer "did the agent reason correctly?" — orthogonal to
    outcome correctness, but critical for production reliability.
    """

    total_calls: int  # total tool invocations
    unique_tools: list[str]  # ordered unique tools called (deduped)
    search_memory_first: bool  # workflow compliance (System Prompt requires this)
    save_to_memory_called: bool  # did agent persist the diagnosis?
    redundant_calls: int  # same tool called back-to-back (wasted tokens)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def analyze_tool_calls(diagnosis_steps: list[str]) -> ToolCallAnalysis:
    """Parse diagnosis_steps strings → ToolCallAnalysis.

    diagnosis_steps entries have the format: "tool_name({...params...})"
    as written by agent._run_loop.
    """
    tool_names: list[str] = []
    for step in diagnosis_steps:
        # Extract tool name before the first '('
        m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", step.strip())
        if m:
            tool_names.append(m.group(1))

    unique: list[str] = list(dict.fromkeys(tool_names))  # order-preserving dedup
    search_memory_first = bool(tool_names) and tool_names[0] == "search_memory"
    save_called = "save_to_memory" in tool_names
    redundant = sum(1 for i in range(1, len(tool_names)) if tool_names[i] == tool_names[i - 1])

    return ToolCallAnalysis(
        total_calls=len(tool_names),
        unique_tools=unique,
        search_memory_first=search_memory_first,
        save_to_memory_called=save_called,
        redundant_calls=redundant,
    )


def classify_failure(
    result_correct: bool,
    tool_analysis: ToolCallAnalysis,
    error: str | None,
    diagnosis_steps: list[str],
) -> FailureMode | None:
    """Classify why the agent failed. Returns None if it succeeded."""
    if result_correct:
        return None
    if error:
        if "budget" in error.lower() or "exceeded" in error.lower() or "timeout" in error.lower():
            return FailureMode.BUDGET_EXCEEDED
        return FailureMode.API_ERROR
    if not tool_analysis.search_memory_first:
        return FailureMode.WORKFLOW_VIOLATION
    if not tool_analysis.save_to_memory_called:
        return FailureMode.NO_SAVE
    # search_memory was called but agent got the wrong answer — was it retrieval or reasoning?
    search_calls = [s for s in diagnosis_steps if s.startswith("search_memory")]
    if not search_calls:
        return FailureMode.RETRIEVAL_MISS
    # search happened but conclusion was wrong
    return FailureMode.REASONING_ERROR


@dataclass
class TrajectoryResult:
    """One end-to-end agent run on a single benchmark case."""

    case_id: str
    steps: int
    tokens_input: int
    tokens_output: int
    estimated_cost_usd: float
    time_seconds: float
    correct: bool
    correctness_score: float
    top_root_cause: str = ""
    error: str | None = None
    # Process eval (new)
    tool_analysis: ToolCallAnalysis | None = None
    failure_mode: FailureMode | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrajectoryAggregate:
    """Summary statistics over a batch of trajectory results."""

    total: int
    correct_count: int
    correctness_rate: float
    mean_steps: float
    p50_steps: float
    p95_steps: float
    mean_tokens_in: float
    mean_tokens_out: float
    mean_cost_usd: float
    total_cost_usd: float
    mean_time_seconds: float
    error_count: int = 0
    # Process eval aggregates (new)
    workflow_compliance_rate: float = 0.0  # fraction that called search_memory first
    save_rate: float = 0.0  # fraction that saved to memory
    mean_redundant_calls: float = 0.0  # avg wasted tool calls per run
    failure_mode_counts: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# ── Correctness judgement ──────────────────────────────────────────────────


def judge_correctness(
    diagnosis: DiagnosisResult,
    case: BenchmarkCase,
    threshold: float = DEFAULT_CORRECTNESS_THRESHOLD,
) -> tuple[bool, float]:
    """Keyword-based correctness judge.

    Returns (correct, score). `correct` is True iff `score >= threshold`.
    """
    text = " ".join(
        [
            diagnosis.root_cause or "",
            diagnosis.fix_suggestion or "",
            diagnosis.reasoning or "",
        ]
    ).lower()

    keywords = list(case.expected_root_cause_keywords) + list(case.expected_fix_keywords)
    if not keywords:
        # Nothing to judge against — call it correct so the eval doesn't lie about coverage.
        return True, 1.0

    hits = sum(1 for kw in keywords if kw.lower() in text)
    score = hits / len(keywords)
    return score >= threshold, score


# ── Single-case runner ────────────────────────────────────────────────────


def run_trajectory(
    case: BenchmarkCase,
    memory_dir: Path,
    api_key: str | None = None,
    model: str | None = None,
) -> TrajectoryResult:
    """Run a single case end-to-end through DiagnosticAgent.

    The caller is responsible for seeding the MemoryStore beforehand if
    they want pre-existing cases visible to the agent.
    """
    # Lazy import — DiagnosticAgent pulls in Anthropic SDK, which we don't
    # want to require for the lightweight aggregate-stats path.
    from debug_mind.agent import DiagnosticAgent
    from debug_mind.budget import TokenBudget

    memory = MemoryStore(memory_dir=memory_dir)
    budget = TokenBudget()
    t0 = time.monotonic()

    try:
        agent = DiagnosticAgent(
            memory=memory,
            api_key=api_key,
            model=model,
            budget=budget,
        )
        result = agent.diagnose(
            bug_description=case.bug_description,
            error_log=case.error_log,
            environment=case.environment,
        )
        elapsed = time.monotonic() - t0
        correct, score = judge_correctness(result, case)
        ta = analyze_tool_calls(result.diagnosis_steps)
        fm = classify_failure(correct, ta, None, result.diagnosis_steps)
        return TrajectoryResult(
            case_id=case.id,
            steps=len(result.diagnosis_steps),
            tokens_input=budget._total_input,
            tokens_output=budget._total_output,
            estimated_cost_usd=budget.accumulated_cost(),
            time_seconds=round(elapsed, 3),
            correct=correct,
            correctness_score=round(score, 3),
            top_root_cause=(result.root_cause or "")[:200],
            tool_analysis=ta,
            failure_mode=fm,
        )
    except Exception as e:
        elapsed = time.monotonic() - t0
        err_str = str(e)
        ta = ToolCallAnalysis(
            total_calls=0,
            unique_tools=[],
            search_memory_first=False,
            save_to_memory_called=False,
            redundant_calls=0,
        )
        fm = classify_failure(False, ta, err_str, [])
        return TrajectoryResult(
            case_id=case.id,
            steps=0,
            tokens_input=budget._total_input,
            tokens_output=budget._total_output,
            estimated_cost_usd=budget.accumulated_cost(),
            time_seconds=round(elapsed, 3),
            correct=False,
            correctness_score=0.0,
            top_root_cause="",
            error=err_str,
            tool_analysis=ta,
            failure_mode=fm,
        )
    finally:
        try:
            memory.close()
        except Exception:
            pass


# ── Batch runner & aggregator ─────────────────────────────────────────────


def _percentile(values: Sequence[float], p: float) -> float:
    """Linear-interpolation percentile. Returns 0.0 for empty input."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * p / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_vals[int(k)])
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def aggregate(results: list[TrajectoryResult]) -> TrajectoryAggregate:
    total = len(results)
    if total == 0:
        return TrajectoryAggregate(
            total=0,
            correct_count=0,
            correctness_rate=0.0,
            mean_steps=0.0,
            p50_steps=0.0,
            p95_steps=0.0,
            mean_tokens_in=0.0,
            mean_tokens_out=0.0,
            mean_cost_usd=0.0,
            total_cost_usd=0.0,
            mean_time_seconds=0.0,
            error_count=0,
        )

    successful = [r for r in results if r.error is None]
    n_succ = max(len(successful), 1)
    n_total = max(total, 1)
    steps = [r.steps for r in successful]
    correct_count = sum(1 for r in results if r.correct)
    error_count = sum(1 for r in results if r.error is not None)

    # Process eval aggregates
    has_ta = [r for r in results if r.tool_analysis is not None]
    n_ta = max(len(has_ta), 1)
    workflow_compliance_rate = round(
        sum(1 for r in has_ta if r.tool_analysis.search_memory_first) / n_ta,
        4,  # type: ignore[union-attr]
    )
    save_rate = round(
        sum(1 for r in has_ta if r.tool_analysis.save_to_memory_called) / n_ta,
        4,  # type: ignore[union-attr]
    )
    mean_redundant = round(
        sum(r.tool_analysis.redundant_calls for r in has_ta) / n_ta,
        2,  # type: ignore[union-attr]
    )

    # Failure mode distribution
    fm_counts: dict[str, int] = {}
    for r in results:
        if r.failure_mode is not None:
            key = r.failure_mode.value
            fm_counts[key] = fm_counts.get(key, 0) + 1

    return TrajectoryAggregate(
        total=total,
        correct_count=correct_count,
        correctness_rate=round(correct_count / n_total, 4),
        mean_steps=round(statistics.fmean(steps) if steps else 0.0, 2),
        p50_steps=round(_percentile(steps, 50), 2),
        p95_steps=round(_percentile(steps, 95), 2),
        mean_tokens_in=round(sum(r.tokens_input for r in successful) / n_succ, 1),
        mean_tokens_out=round(sum(r.tokens_output for r in successful) / n_succ, 1),
        mean_cost_usd=round(sum(r.estimated_cost_usd for r in successful) / n_succ, 6),
        total_cost_usd=round(sum(r.estimated_cost_usd for r in successful), 6),
        mean_time_seconds=round(sum(r.time_seconds for r in successful) / n_succ, 3),
        error_count=error_count,
        workflow_compliance_rate=workflow_compliance_rate,
        save_rate=save_rate,
        mean_redundant_calls=mean_redundant,
        failure_mode_counts=fm_counts,
    )


# ── Baseline comparison (regression detection) ────────────────────────────


def save_as_baseline(agg: TrajectoryAggregate) -> Path:
    """Persist aggregate metrics as the new baseline for regression detection."""
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(
        json.dumps(agg.to_json(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return BASELINE_FILE


def compare_to_baseline(agg: TrajectoryAggregate) -> dict[str, Any] | None:
    """Compare current run against saved baseline. Returns None if no baseline.

    A 'regression' is flagged when correctness_rate drops more than
    REGRESSION_THRESHOLD vs the baseline — same logic as CI gates in MLOps.
    """
    if not BASELINE_FILE.exists():
        return None
    try:
        raw = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
        baseline = TrajectoryAggregate(**raw)
    except Exception:
        return None

    delta_correct = round(agg.correctness_rate - baseline.correctness_rate, 4)
    delta_steps = round(agg.mean_steps - baseline.mean_steps, 2)
    delta_cost = round(agg.mean_cost_usd - baseline.mean_cost_usd, 6)
    delta_compliance = round(agg.workflow_compliance_rate - baseline.workflow_compliance_rate, 4)

    return {
        "baseline_correctness_rate": baseline.correctness_rate,
        "current_correctness_rate": agg.correctness_rate,
        "delta_correctness": delta_correct,
        "delta_mean_steps": delta_steps,
        "delta_mean_cost_usd": delta_cost,
        "delta_workflow_compliance": delta_compliance,
        "regression": delta_correct < -REGRESSION_THRESHOLD,
    }


def run_trajectory_eval(
    cases: list[BenchmarkCase] | None = None,
    sample: int | None = None,
    api_key: str | None = None,
    model: str | None = None,
    write_json: bool = True,
) -> tuple[list[TrajectoryResult], TrajectoryAggregate, Path | None]:
    """Run trajectory eval on (optionally sampled) cases.

    Returns (results, aggregate, json_path).
    `json_path` is None if write_json=False or writing failed.
    """
    if cases is None:
        cases = load_all_cases()

    if sample and sample > 0:
        cases = cases[:sample]

    # Seed an isolated memory store so the agent has prior cases to retrieve.
    tmp_dir = tempfile.mkdtemp(prefix="debug_mind_traj_")
    results: list[TrajectoryResult] = []
    try:
        # Pre-seed once; each per-case runner re-opens the same dir.
        seed_store = MemoryStore(memory_dir=Path(tmp_dir))
        try:
            from evaluation.benchmark import _seed_all

            _seed_all(seed_store)
        finally:
            seed_store.close()

        for case in cases:
            res = run_trajectory(case, Path(tmp_dir), api_key=api_key, model=model)
            results.append(res)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    agg = aggregate(results)
    json_path: Path | None = None
    if write_json:
        json_path = _write_results_json(results, agg)
    return results, agg, json_path


def _write_results_json(results: list[TrajectoryResult], agg: TrajectoryAggregate) -> Path | None:
    try:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"trajectory_{ts}.json"
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "aggregate": agg.to_json(),
        "results": [r.to_json() for r in results],
    }
    try:
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path
    except OSError:
        return None


# ── Formatting ────────────────────────────────────────────────────────────


def format_trajectory(
    results: list[TrajectoryResult],
    agg: TrajectoryAggregate,
    baseline_cmp: dict[str, Any] | None = None,
) -> str:
    """Render results as a Markdown report for the console."""
    lines = ["", "## Trajectory eval results", ""]

    # Per-case table
    lines.append(
        "| case | steps | tokens (in/out) | cost (USD) | time (s) | correct | failure mode |"
    )
    lines.append("|---|---:|---:|---:|---:|:---:|---|")
    for r in results:
        fm = r.failure_mode.value if r.failure_mode else "—"
        if r.error:
            lines.append(f"| {r.case_id[:24]} | err | — | — | {r.time_seconds:.1f} | ❌ | {fm} |")
            continue
        mark = "✅" if r.correct else "❌"
        ta_flag = ""
        if r.tool_analysis and not r.tool_analysis.search_memory_first:
            ta_flag = " ⚠️"  # workflow violation
        lines.append(
            f"| {r.case_id[:24]} | {r.steps}{ta_flag} | "
            f"{r.tokens_input}/{r.tokens_output} | "
            f"${r.estimated_cost_usd:.4f} | {r.time_seconds:.1f} | "
            f"{mark} ({r.correctness_score:.2f}) | {fm} |"
        )

    lines.append("")
    lines.append("### Outcome eval")
    lines.append(
        f"- total: **{agg.total}** | correct: **{agg.correct_count}** "
        f"({agg.correctness_rate:.0%}) | errors: {agg.error_count}"
    )

    lines.append("")
    lines.append("### Efficiency eval")
    lines.append(
        f"- steps: mean **{agg.mean_steps}**, p50 {agg.p50_steps}, **p95 {agg.p95_steps}**"
    )
    lines.append(
        f"- tokens (mean): in **{agg.mean_tokens_in:.0f}**, out **{agg.mean_tokens_out:.0f}**"
    )
    lines.append(f"- cost: mean **${agg.mean_cost_usd:.4f}** / total **${agg.total_cost_usd:.4f}**")
    lines.append(f"- time: mean **{agg.mean_time_seconds:.1f}s**")

    lines.append("")
    lines.append("### Process eval (workflow compliance)")
    lines.append(
        f"- search_memory first: **{agg.workflow_compliance_rate:.0%}** "
        f"({'✅ compliant' if agg.workflow_compliance_rate >= 0.9 else '⚠️ violations detected'})"
    )
    lines.append(f"- save_to_memory called: **{agg.save_rate:.0%}**")
    lines.append(f"- mean redundant tool calls: **{agg.mean_redundant_calls}**")

    if agg.failure_mode_counts:
        lines.append("")
        lines.append("### Failure mode breakdown")
        for mode, count in sorted(agg.failure_mode_counts.items(), key=lambda x: -x[1]):
            lines.append(f"- {mode}: {count}")

    if baseline_cmp:
        lines.append("")
        lines.append("### Regression check vs baseline")
        delta_sym = "▲" if baseline_cmp["delta_correctness"] >= 0 else "▼"
        lines.append(
            f"- correctness: {baseline_cmp['baseline_correctness_rate']:.0%} → "
            f"{baseline_cmp['current_correctness_rate']:.0%} "
            f"({delta_sym}{abs(baseline_cmp['delta_correctness']):.0%})"
        )
        lines.append(
            f"- steps delta: {baseline_cmp['delta_mean_steps']:+.1f} | "
            f"cost delta: ${baseline_cmp['delta_mean_cost_usd']:+.4f}"
        )
        if baseline_cmp["regression"]:
            lines.append(
                f"- **🔴 REGRESSION DETECTED** "
                f"(correctness dropped > {REGRESSION_THRESHOLD:.0%} vs baseline)"
            )
        else:
            lines.append("- ✅ No regression")

    return "\n".join(lines)
