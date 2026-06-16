"""Memory-benefit experiments — ablation A/B, pass^k stability, self-learning curve.

Why this exists:
DebugMind's core thesis is "memory makes the Nth diagnosis cheaper than the
first". `benchmark.py` proves retrieval *can* surface the right past case;
`trajectory_eval.py` measures how the agent behaves on one fixed, pre-seeded
store. Neither measures the *causal benefit of memory itself*. This module
adds the three experiment designs the memory-systems literature uses:

1. Ablation A/B (`run_ablation`) — same cases, same agent, same tools; the
   only variable is memory CONTENT (seeded vs empty). The no-memory arm does
   NOT remove the memory tools: removing them would change the agent's
   action space and prompt, contaminating the comparison with a second
   variable. Cases split into repeat-class (a same-class seed exists in
   memory) and novel-class (no matching seed) — memory should help the
   former and must not hurt the latter.

2. pass^k (`runs=k`) — τ-bench-style stability metric: a case passes pass^k
   only if ALL k runs are correct (contrast pass@k = at least one run
   correct). The gap between pass@k and pass^k is flakiness.

3. Self-learning round-trip (`run_self_learning`) — round 1 starts from an
   EMPTY store and the agent's own saves accumulate case by case; round 2
   re-runs the same cases against whatever round 1 left behind. This
   measures the full flywheel (diagnose → save → retrieve → speed-up) with
   zero curated knowledge.

Isolation rule: in the ablation, every single run gets a fresh clone of the
store template, so one run's saves can never leak into another (the shared
accumulating store in `trajectory_eval.py` is intentional there, but would
invalidate an A/B). In the self-learning experiment, accumulation within a
round is the point — that is the variable under measurement.

Cost warning: total LLM calls = cases × 2 arms × runs (ablation) or
cases × rounds (self-learning). Use --sample for smoke tests.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from debug_mind.memory.store import MemoryStore

from evaluation.dataset import BenchmarkCase, load_all_cases
from evaluation.trajectory_eval import (
    RESULTS_DIR,
    TrajectoryAggregate,
    TrajectoryResult,
    aggregate,
    run_trajectory,
)

ARM_WITH_MEMORY = "with_memory"
ARM_NO_MEMORY = "no_memory"


# ── Case classification ───────────────────────────────────────────────────


def is_repeat_class(case: BenchmarkCase) -> bool:
    """A case is repeat-class iff the seeded store contains a same-class case."""
    return bool(case.seed_case_ids or case.expected_top_hit_id)


# ── Store preparation ─────────────────────────────────────────────────────


def _seed_template(template_dir: Path) -> int:
    """Build the seeded store template once. Returns number of seeded cases."""
    from evaluation.benchmark import _seed_all

    store = MemoryStore(memory_dir=template_dir)
    try:
        loaded = _seed_all(store)
    finally:
        store.close()
    return len(loaded)


def _clone_template(template_dir: Path, dest_dir: Path) -> None:
    """Copy the (closed) template store so a run starts from a known state."""
    shutil.copytree(template_dir, dest_dir, dirs_exist_ok=True)


def _count_memory_files(memory_dir: Path) -> int:
    """Cheap store-size probe: markdown files are the source of truth."""
    if not memory_dir.exists():
        return 0
    return sum(1 for _ in memory_dir.rglob("*.md"))


# ── Ablation A/B + pass^k ─────────────────────────────────────────────────


@dataclass
class CaseRuns:
    """All k runs of one case within one arm."""

    case_id: str
    repeat_class: bool
    results: list[TrajectoryResult]

    @property
    def any_correct(self) -> bool:
        return any(r.correct for r in self.results)

    @property
    def all_correct(self) -> bool:
        return bool(self.results) and all(r.correct for r in self.results)

    def to_json(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "repeat_class": self.repeat_class,
            "any_correct": self.any_correct,
            "all_correct": self.all_correct,
            "results": [r.to_json() for r in self.results],
        }


@dataclass
class ArmReport:
    """One arm of the ablation: aggregate + repeat/novel split + pass^k."""

    arm: str
    k: int
    case_runs: list[CaseRuns]
    aggregate: TrajectoryAggregate
    repeat_aggregate: TrajectoryAggregate
    novel_aggregate: TrajectoryAggregate
    pass_at_k: float
    pass_hat_k: float

    def to_json(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "k": self.k,
            "aggregate": self.aggregate.to_json(),
            "repeat_aggregate": self.repeat_aggregate.to_json(),
            "novel_aggregate": self.novel_aggregate.to_json(),
            "pass_at_k": self.pass_at_k,
            "pass_hat_k": self.pass_hat_k,
            "case_runs": [cr.to_json() for cr in self.case_runs],
        }


@dataclass
class AblationReport:
    with_memory: ArmReport
    no_memory: ArmReport
    deltas: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "deltas": self.deltas,
            "with_memory": self.with_memory.to_json(),
            "no_memory": self.no_memory.to_json(),
        }


def _build_arm_report(arm: str, k: int, case_runs: list[CaseRuns]) -> ArmReport:
    all_results = [r for cr in case_runs for r in cr.results]
    repeat_results = [r for cr in case_runs if cr.repeat_class for r in cr.results]
    novel_results = [r for cr in case_runs if not cr.repeat_class for r in cr.results]
    n_cases = max(len(case_runs), 1)
    return ArmReport(
        arm=arm,
        k=k,
        case_runs=case_runs,
        aggregate=aggregate(all_results),
        repeat_aggregate=aggregate(repeat_results),
        novel_aggregate=aggregate(novel_results),
        pass_at_k=round(sum(1 for cr in case_runs if cr.any_correct) / n_cases, 4),
        pass_hat_k=round(sum(1 for cr in case_runs if cr.all_correct) / n_cases, 4),
    )


def _compute_deltas(with_mem: ArmReport, no_mem: ArmReport) -> dict[str, float]:
    """with_memory minus no_memory. Negative steps/cost deltas = memory helps."""
    w, n = with_mem.aggregate, no_mem.aggregate
    wr, nr = with_mem.repeat_aggregate, no_mem.repeat_aggregate
    wn, nn = with_mem.novel_aggregate, no_mem.novel_aggregate
    return {
        "delta_correctness_rate": round(w.correctness_rate - n.correctness_rate, 4),
        "delta_mean_steps": round(w.mean_steps - n.mean_steps, 2),
        "delta_mean_cost_usd": round(w.mean_cost_usd - n.mean_cost_usd, 6),
        "delta_mean_time_seconds": round(w.mean_time_seconds - n.mean_time_seconds, 3),
        "delta_pass_hat_k": round(with_mem.pass_hat_k - no_mem.pass_hat_k, 4),
        # Headline: repeat-class is where memory is supposed to pay off.
        "repeat_delta_correctness_rate": round(wr.correctness_rate - nr.correctness_rate, 4),
        "repeat_delta_mean_steps": round(wr.mean_steps - nr.mean_steps, 2),
        "repeat_delta_mean_cost_usd": round(wr.mean_cost_usd - nr.mean_cost_usd, 6),
        # Guard: novel-class must not regress (memory must not mislead).
        "novel_delta_correctness_rate": round(wn.correctness_rate - nn.correctness_rate, 4),
        "novel_delta_mean_steps": round(wn.mean_steps - nn.mean_steps, 2),
    }


def run_ablation(
    cases: list[BenchmarkCase] | None = None,
    sample: int | None = None,
    runs: int = 1,
    api_key: str | None = None,
    model: str | None = None,
    write_json: bool = True,
) -> tuple[AblationReport, Path | None]:
    """Run the memory ablation A/B. Returns (report, json_path).

    Every run starts from a fresh clone of the arm's template store, so runs
    are independent and pass^k is measured on i.i.d. trials.
    """
    if cases is None:
        cases = load_all_cases()
    if sample and sample > 0:
        cases = cases[:sample]
    runs = max(runs, 1)

    work_dir = Path(tempfile.mkdtemp(prefix="debug_mind_ablation_"))
    arm_reports: dict[str, ArmReport] = {}
    try:
        seeded_template = work_dir / "template_seeded"
        seeded_template.mkdir(parents=True, exist_ok=True)
        _seed_template(seeded_template)

        for arm in (ARM_WITH_MEMORY, ARM_NO_MEMORY):
            case_runs: list[CaseRuns] = []
            for case in cases:
                results: list[TrajectoryResult] = []
                for run_idx in range(runs):
                    run_dir = work_dir / f"{arm}_{case.id}_{run_idx}"
                    if arm == ARM_WITH_MEMORY:
                        _clone_template(seeded_template, run_dir)
                    else:
                        run_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        results.append(run_trajectory(case, run_dir, api_key=api_key, model=model))
                    finally:
                        shutil.rmtree(run_dir, ignore_errors=True)
                case_runs.append(
                    CaseRuns(
                        case_id=case.id,
                        repeat_class=is_repeat_class(case),
                        results=results,
                    )
                )
            arm_reports[arm] = _build_arm_report(arm, runs, case_runs)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    report = AblationReport(
        with_memory=arm_reports[ARM_WITH_MEMORY],
        no_memory=arm_reports[ARM_NO_MEMORY],
    )
    report.deltas = _compute_deltas(report.with_memory, report.no_memory)

    json_path = _write_experiment_json("ablation", report.to_json()) if write_json else None
    return report, json_path


# ── Self-learning round-trip (learning curve) ─────────────────────────────


@dataclass
class CurvePoint:
    """One case run within a round, with the store size it started from."""

    round_index: int
    case_index: int
    case_id: str
    memory_files_before: int
    result: TrajectoryResult

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["result"] = self.result.to_json()
        return d


@dataclass
class RoundReport:
    round_index: int
    points: list[CurvePoint]
    aggregate: TrajectoryAggregate

    def to_json(self) -> dict[str, Any]:
        return {
            "round_index": self.round_index,
            "aggregate": self.aggregate.to_json(),
            "points": [p.to_json() for p in self.points],
        }


@dataclass
class SelfLearningReport:
    rounds: list[RoundReport]
    paired_deltas: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "paired_deltas": self.paired_deltas,
            "rounds": [r.to_json() for r in self.rounds],
        }


def _pair_rounds(first: RoundReport, last: RoundReport) -> list[dict[str, Any]]:
    """Per-case first-round vs last-round comparison (matched by case_id)."""
    last_by_id = {p.case_id: p for p in last.points}
    paired = []
    for p1 in first.points:
        p2 = last_by_id.get(p1.case_id)
        if p2 is None:
            continue
        paired.append(
            {
                "case_id": p1.case_id,
                "steps_first": p1.result.steps,
                "steps_last": p2.result.steps,
                "delta_steps": p2.result.steps - p1.result.steps,
                "cost_first": p1.result.estimated_cost_usd,
                "cost_last": p2.result.estimated_cost_usd,
                "delta_cost_usd": round(
                    p2.result.estimated_cost_usd - p1.result.estimated_cost_usd, 6
                ),
                "correct_first": p1.result.correct,
                "correct_last": p2.result.correct,
            }
        )
    return paired


def _summarize_rounds(first: RoundReport, last: RoundReport) -> dict[str, float]:
    f, last_agg = first.aggregate, last.aggregate
    return {
        "rounds": float(last.round_index + 1),
        "first_round_correctness": f.correctness_rate,
        "last_round_correctness": last_agg.correctness_rate,
        "delta_correctness_rate": round(last_agg.correctness_rate - f.correctness_rate, 4),
        "first_round_mean_steps": f.mean_steps,
        "last_round_mean_steps": last_agg.mean_steps,
        "delta_mean_steps": round(last_agg.mean_steps - f.mean_steps, 2),
        "first_round_mean_cost_usd": f.mean_cost_usd,
        "last_round_mean_cost_usd": last_agg.mean_cost_usd,
        "delta_mean_cost_usd": round(last_agg.mean_cost_usd - f.mean_cost_usd, 6),
        "delta_mean_time_seconds": round(last_agg.mean_time_seconds - f.mean_time_seconds, 3),
    }


def run_self_learning(
    cases: list[BenchmarkCase] | None = None,
    sample: int | None = None,
    rounds: int = 2,
    api_key: str | None = None,
    model: str | None = None,
    write_json: bool = True,
) -> tuple[SelfLearningReport, Path | None]:
    """Run the self-learning round-trip. Returns (report, json_path).

    One shared store, starting EMPTY. The agent's own `save_to_memory` calls
    are the only writes — round N+1 benefits exactly from what the agent
    chose to persist, which is the flywheel DebugMind claims.
    """
    if cases is None:
        cases = load_all_cases()
    if sample and sample > 0:
        cases = cases[:sample]
    rounds = max(rounds, 1)

    store_dir = Path(tempfile.mkdtemp(prefix="debug_mind_selflearn_"))
    round_reports: list[RoundReport] = []
    try:
        for round_idx in range(rounds):
            points: list[CurvePoint] = []
            for case_idx, case in enumerate(cases):
                size_before = _count_memory_files(store_dir)
                res = run_trajectory(case, store_dir, api_key=api_key, model=model)
                points.append(
                    CurvePoint(
                        round_index=round_idx,
                        case_index=case_idx,
                        case_id=case.id,
                        memory_files_before=size_before,
                        result=res,
                    )
                )
            round_reports.append(
                RoundReport(
                    round_index=round_idx,
                    points=points,
                    aggregate=aggregate([p.result for p in points]),
                )
            )
    finally:
        shutil.rmtree(store_dir, ignore_errors=True)

    report = SelfLearningReport(rounds=round_reports)
    if len(round_reports) >= 2:
        report.paired_deltas = _pair_rounds(round_reports[0], round_reports[-1])
        report.summary = _summarize_rounds(round_reports[0], round_reports[-1])

    json_path = _write_experiment_json("self_learning", report.to_json()) if write_json else None
    return report, json_path


# ── Persistence ───────────────────────────────────────────────────────────


def _write_experiment_json(name: str, payload: dict[str, Any]) -> Path | None:
    try:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{name}_{ts}.json"
    wrapped = {"ts": datetime.now(timezone.utc).isoformat(), **payload}
    try:
        path.write_text(json.dumps(wrapped, indent=2, ensure_ascii=False), encoding="utf-8")
        return path
    except OSError:
        return None


# ── Formatting ────────────────────────────────────────────────────────────


def _fmt_agg_row(label: str, agg: TrajectoryAggregate) -> str:
    return (
        f"| {label} | {agg.total} | {agg.correctness_rate:.0%} | "
        f"{agg.mean_steps} | {agg.p50_steps} | "
        f"${agg.mean_cost_usd:.4f} | {agg.mean_time_seconds:.1f}s |"
    )


def format_ablation(report: AblationReport) -> str:
    """Render the ablation A/B as a Markdown report."""
    w, n = report.with_memory, report.no_memory
    d = report.deltas
    lines = ["", "## Memory ablation A/B", ""]
    lines.append("| arm / group | runs | correct | mean steps | p50 | mean cost | mean time |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    lines.append(_fmt_agg_row("with memory — all", w.aggregate))
    lines.append(_fmt_agg_row("with memory — repeat", w.repeat_aggregate))
    lines.append(_fmt_agg_row("with memory — novel", w.novel_aggregate))
    lines.append(_fmt_agg_row("no memory — all", n.aggregate))
    lines.append(_fmt_agg_row("no memory — repeat", n.repeat_aggregate))
    lines.append(_fmt_agg_row("no memory — novel", n.novel_aggregate))

    lines.append("")
    lines.append("### Memory benefit (with_memory - no_memory)")
    lines.append(
        f"- **repeat-class**: steps {d['repeat_delta_mean_steps']:+.1f}, "
        f"cost ${d['repeat_delta_mean_cost_usd']:+.4f}, "
        f"correctness {d['repeat_delta_correctness_rate']:+.0%}"
    )
    lines.append(
        f"- novel-class guard: steps {d['novel_delta_mean_steps']:+.1f}, "
        f"correctness {d['novel_delta_correctness_rate']:+.0%} "
        f"(should be ≈ 0 — memory must not mislead on unseen bug classes)"
    )
    lines.append(
        f"- overall: steps {d['delta_mean_steps']:+.1f}, "
        f"cost ${d['delta_mean_cost_usd']:+.4f}, "
        f"correctness {d['delta_correctness_rate']:+.0%}"
    )

    if w.k > 1:
        lines.append("")
        lines.append(f"### Stability (k={w.k})")
        lines.append("| arm | pass@k | pass^k | flakiness gap |")
        lines.append("|---|---:|---:|---:|")
        for arm in (w, n):
            gap = round(arm.pass_at_k - arm.pass_hat_k, 4)
            lines.append(f"| {arm.arm} | {arm.pass_at_k:.0%} | {arm.pass_hat_k:.0%} | {gap:.0%} |")
    return "\n".join(lines)


def format_self_learning(report: SelfLearningReport) -> str:
    """Render the self-learning round-trip as a Markdown report."""
    lines = ["", "## Self-learning round-trip", ""]
    lines.append("| round | correct | mean steps | p50 | mean cost | mean time |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for r in report.rounds:
        a = r.aggregate
        lines.append(
            f"| {r.round_index + 1} | {a.correctness_rate:.0%} | {a.mean_steps} | "
            f"{a.p50_steps} | ${a.mean_cost_usd:.4f} | {a.mean_time_seconds:.1f}s |"
        )

    if report.summary:
        s = report.summary
        lines.append("")
        lines.append("### Flywheel effect (round 1 → final round)")
        lines.append(
            f"- steps: {s['first_round_mean_steps']} → {s['last_round_mean_steps']} "
            f"({s['delta_mean_steps']:+.1f})"
        )
        lines.append(
            f"- cost: ${s['first_round_mean_cost_usd']:.4f} → "
            f"${s['last_round_mean_cost_usd']:.4f} (${s['delta_mean_cost_usd']:+.4f})"
        )
        lines.append(
            f"- correctness: {s['first_round_correctness']:.0%} → "
            f"{s['last_round_correctness']:.0%} ({s['delta_correctness_rate']:+.0%})"
        )

    if report.paired_deltas:
        improved = sum(1 for p in report.paired_deltas if p["delta_steps"] < 0)
        lines.append(
            f"- cases with fewer steps in final round: **{improved}/{len(report.paired_deltas)}**"
        )
    return "\n".join(lines)
