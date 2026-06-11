"""Chunked, resumable runner for the memory experiments.

Why: one agent run takes ~30s wall time and the full grid is
50 cases × 2 arms × k runs (+ 50 × rounds for the learning curve) — hours.
This driver checkpoints every completed run to a JSONL shard so it can be
invoked repeatedly in bounded time slices (each exits within --time-budget)
until the grid is complete, then `finalize` assembles the standard reports.

Usage:
  python scripts/run_experiments_chunked.py ablation --arm with_memory --runs 3
  python scripts/run_experiments_chunked.py ablation --arm no_memory  --runs 3
  python scripts/run_experiments_chunked.py curve --rounds 2
  python scripts/run_experiments_chunked.py finalize --runs 3

Exit markers on stdout: PROGRESS <done>/<total>, ARM_DONE / CURVE_DONE when
that stream's grid is complete.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.dataset import load_all_cases  # noqa: E402
from evaluation.experiments import (  # noqa: E402
    ARM_NO_MEMORY,
    ARM_WITH_MEMORY,
    AblationReport,
    CaseRuns,
    CurvePoint,
    RoundReport,
    SelfLearningReport,
    _build_arm_report,
    _clone_template,
    _compute_deltas,
    _count_memory_files,
    _pair_rounds,
    _seed_template,
    _summarize_rounds,
    _write_experiment_json,
    format_ablation,
    format_self_learning,
    is_repeat_class,
)
from evaluation.trajectory_eval import (  # noqa: E402
    RESULTS_DIR,
    FailureMode,
    ToolCallAnalysis,
    TrajectoryResult,
    aggregate,
    run_trajectory,
)

CURVE_STORE_DIR = RESULTS_DIR / "selflearn_store"


def _ckpt_path(arm: str) -> Path:
    return RESULTS_DIR / f"ablation_ckpt_{arm}.jsonl"


CURVE_CKPT = RESULTS_DIR / "curve_ckpt.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


def _result_from_json(d: dict) -> TrajectoryResult:
    ta = d.get("tool_analysis")
    fm = d.get("failure_mode")
    return TrajectoryResult(
        **{
            **d,
            "tool_analysis": ToolCallAnalysis(**ta) if ta else None,
            "failure_mode": FailureMode(fm) if fm else None,
        }
    )


# ── Ablation worker ───────────────────────────────────────────────────────


def run_ablation_chunk(arm: str, runs: int, time_budget: float) -> None:
    cases = load_all_cases()
    ckpt = _ckpt_path(arm)
    done = {(r["case_id"], r["run_idx"]) for r in _load_jsonl(ckpt)}
    total = len(cases) * runs
    tasks = [
        (case, idx) for case in cases for idx in range(runs) if (case.id, idx) not in done
    ]
    if not tasks:
        print(f"ARM_DONE {arm} {total}/{total}")
        return

    work_dir = Path(tempfile.mkdtemp(prefix=f"dm_abl_{arm}_"))
    try:
        template: Path | None = None
        if arm == ARM_WITH_MEMORY:
            template = work_dir / "template"
            template.mkdir(parents=True)
            _seed_template(template)

        t0 = time.monotonic()
        completed = len(done)
        for case, idx in tasks:
            if time.monotonic() - t0 > time_budget:
                break
            run_dir = work_dir / f"{case.id}_{idx}"
            if template is not None:
                _clone_template(template, run_dir)
            else:
                run_dir.mkdir(parents=True, exist_ok=True)
            try:
                res = run_trajectory(case, run_dir)
            finally:
                shutil.rmtree(run_dir, ignore_errors=True)
            _append_jsonl(
                ckpt, {"case_id": case.id, "run_idx": idx, "result": res.to_json()}
            )
            completed += 1
            print(f"PROGRESS {arm} {completed}/{total} {case.id}#{idx}", flush=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    if completed >= total:
        print(f"ARM_DONE {arm} {completed}/{total}")
    else:
        print(f"CHUNK_END {arm} {completed}/{total}")


# ── Learning-curve worker (strictly sequential, persistent store) ────────


def run_curve_chunk(rounds: int, time_budget: float) -> None:
    cases = load_all_cases()
    done = {(r["round_index"], r["case_index"]) for r in _load_jsonl(CURVE_CKPT)}
    total = len(cases) * rounds
    CURVE_STORE_DIR.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    completed = len(done)
    for round_idx in range(rounds):
        for case_idx, case in enumerate(cases):
            if (round_idx, case_idx) in done:
                continue
            if time.monotonic() - t0 > time_budget:
                print(f"CHUNK_END curve {completed}/{total}")
                return
            size_before = _count_memory_files(CURVE_STORE_DIR)
            res = run_trajectory(case, CURVE_STORE_DIR)
            _append_jsonl(
                CURVE_CKPT,
                {
                    "round_index": round_idx,
                    "case_index": case_idx,
                    "case_id": case.id,
                    "memory_files_before": size_before,
                    "result": res.to_json(),
                },
            )
            completed += 1
            print(f"PROGRESS curve {completed}/{total} r{round_idx} {case.id}", flush=True)

    print(f"CURVE_DONE {completed}/{total}")


# ── Finalize ─────────────────────────────────────────────────────────────


def finalize(runs: int) -> None:
    cases = {c.id: c for c in load_all_cases()}

    arm_reports = {}
    for arm in (ARM_WITH_MEMORY, ARM_NO_MEMORY):
        rows = _load_jsonl(_ckpt_path(arm))
        by_case: dict[str, list[tuple[int, TrajectoryResult]]] = {}
        for row in rows:
            by_case.setdefault(row["case_id"], []).append(
                (row["run_idx"], _result_from_json(row["result"]))
            )
        case_runs = [
            CaseRuns(
                case_id=cid,
                repeat_class=is_repeat_class(cases[cid]),
                results=[r for _, r in sorted(pairs)],
            )
            for cid, pairs in sorted(by_case.items())
        ]
        arm_reports[arm] = _build_arm_report(arm, runs, case_runs)

    report = AblationReport(
        with_memory=arm_reports[ARM_WITH_MEMORY], no_memory=arm_reports[ARM_NO_MEMORY]
    )
    report.deltas = _compute_deltas(report.with_memory, report.no_memory)
    path = _write_experiment_json("ablation", report.to_json())
    print(format_ablation(report))
    print(f"\nABLATION_JSON {path}")

    curve_rows = _load_jsonl(CURVE_CKPT)
    if curve_rows:
        by_round: dict[int, list[CurvePoint]] = {}
        for row in curve_rows:
            by_round.setdefault(row["round_index"], []).append(
                CurvePoint(
                    round_index=row["round_index"],
                    case_index=row["case_index"],
                    case_id=row["case_id"],
                    memory_files_before=row["memory_files_before"],
                    result=_result_from_json(row["result"]),
                )
            )
        round_reports = []
        for ridx in sorted(by_round):
            points = sorted(by_round[ridx], key=lambda p: p.case_index)
            round_reports.append(
                RoundReport(
                    round_index=ridx,
                    points=points,
                    aggregate=aggregate([p.result for p in points]),
                )
            )
        sl = SelfLearningReport(rounds=round_reports)
        if len(round_reports) >= 2:
            sl.paired_deltas = _pair_rounds(round_reports[0], round_reports[-1])
            sl.summary = _summarize_rounds(round_reports[0], round_reports[-1])
        sl_path = _write_experiment_json("self_learning", sl.to_json())
        print(format_self_learning(sl))
        print(f"\nSELF_LEARNING_JSON {sl_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_abl = sub.add_parser("ablation")
    p_abl.add_argument("--arm", choices=[ARM_WITH_MEMORY, ARM_NO_MEMORY], required=True)
    p_abl.add_argument("--runs", type=int, default=3)
    p_abl.add_argument("--time-budget", type=float, default=480.0)

    p_curve = sub.add_parser("curve")
    p_curve.add_argument("--rounds", type=int, default=2)
    p_curve.add_argument("--time-budget", type=float, default=480.0)

    p_fin = sub.add_parser("finalize")
    p_fin.add_argument("--runs", type=int, default=3)

    args = parser.parse_args()
    if args.cmd == "ablation":
        run_ablation_chunk(args.arm, args.runs, args.time_budget)
    elif args.cmd == "curve":
        run_curve_chunk(args.rounds, args.time_budget)
    else:
        finalize(args.runs)


if __name__ == "__main__":
    main()
