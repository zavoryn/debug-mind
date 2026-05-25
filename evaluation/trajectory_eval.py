"""Agent trajectory evaluation — measures end-to-end agent behaviour.

Why this exists:
The existing `evaluation/benchmark.py` measures *retrieval* quality (hit@k,
MRR) — i.e., would the memory store surface the right past case? That tells
us nothing about how the agent actually *behaves* when handed a fresh bug:
how many tool calls does it take? how many tokens does it burn? does it
ever arrive at a correct diagnosis? Those are the questions you get asked
when you ship an LLM agent into production — and most teams have no
reproducible answer. This module fills that gap.

This is intentionally not part of the CI pipeline (each run hits a paid
LLM API) and is meant to be invoked manually:

    debug-mind eval --trajectory --sample 3

Correctness is judged today by keyword matching against the benchmark
case's expected_root_cause_keywords + expected_fix_keywords. A more
expensive LLM-as-judge implementation is reserved for Phase 7.
"""

from __future__ import annotations

import json
import math
import shutil
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import DiagnosisResult

from evaluation.dataset import BenchmarkCase, load_all_cases


RESULTS_DIR = Path(__file__).parent / "results"

# Correctness threshold: fraction of expected keywords that must appear in
# the agent's final root_cause + fix_suggestion text. Tunable per use case.
DEFAULT_CORRECTNESS_THRESHOLD = 0.5


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
        )
    except Exception as e:
        elapsed = time.monotonic() - t0
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
            error=str(e),
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
            total=0, correct_count=0, correctness_rate=0.0,
            mean_steps=0.0, p50_steps=0.0, p95_steps=0.0,
            mean_tokens_in=0.0, mean_tokens_out=0.0,
            mean_cost_usd=0.0, total_cost_usd=0.0,
            mean_time_seconds=0.0, error_count=0,
        )

    successful = [r for r in results if r.error is None]
    n_succ = max(len(successful), 1)
    steps = [r.steps for r in successful]
    correct_count = sum(1 for r in results if r.correct)
    error_count = sum(1 for r in results if r.error is not None)

    return TrajectoryAggregate(
        total=total,
        correct_count=correct_count,
        correctness_rate=round(correct_count / total, 4),
        mean_steps=round(statistics.fmean(steps) if steps else 0.0, 2),
        p50_steps=round(_percentile(steps, 50), 2),
        p95_steps=round(_percentile(steps, 95), 2),
        mean_tokens_in=round(sum(r.tokens_input for r in successful) / n_succ, 1),
        mean_tokens_out=round(sum(r.tokens_output for r in successful) / n_succ, 1),
        mean_cost_usd=round(sum(r.estimated_cost_usd for r in successful) / n_succ, 6),
        total_cost_usd=round(sum(r.estimated_cost_usd for r in successful), 6),
        mean_time_seconds=round(sum(r.time_seconds for r in successful) / n_succ, 3),
        error_count=error_count,
    )


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


def _write_results_json(
    results: list[TrajectoryResult], agg: TrajectoryAggregate
) -> Path | None:
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
    results: list[TrajectoryResult], agg: TrajectoryAggregate
) -> str:
    """Render results as a Markdown table for the console."""
    lines = ["", "## Trajectory eval results", ""]
    lines.append("| case | steps | tokens (in/out) | cost (USD) | time (s) | correct |")
    lines.append("|---|---:|---:|---:|---:|:---:|")
    for r in results:
        if r.error:
            lines.append(
                f"| {r.case_id[:24]} | err | — | — | {r.time_seconds:.1f} | ❌ ({r.error[:30]}) |"
            )
            continue
        mark = "✅" if r.correct else "❌"
        lines.append(
            f"| {r.case_id[:24]} | {r.steps} | {r.tokens_input}/{r.tokens_output} | "
            f"${r.estimated_cost_usd:.4f} | {r.time_seconds:.1f} | {mark} ({r.correctness_score:.2f}) |"
        )
    lines.append("")
    lines.append("### Summary")
    lines.append(
        f"- total: **{agg.total}** | correct: **{agg.correct_count}** "
        f"({agg.correctness_rate:.0%}) | errors: {agg.error_count}"
    )
    lines.append(
        f"- steps: mean **{agg.mean_steps}**, p50 {agg.p50_steps}, p95 {agg.p95_steps}"
    )
    lines.append(
        f"- tokens (mean): in **{agg.mean_tokens_in:.0f}**, out **{agg.mean_tokens_out:.0f}**"
    )
    lines.append(
        f"- cost: mean **${agg.mean_cost_usd:.4f}** / total **${agg.total_cost_usd:.4f}**"
    )
    lines.append(f"- time: mean **{agg.mean_time_seconds:.1f}s**")
    return "\n".join(lines)
