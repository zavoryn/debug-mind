"""Tests for memory-benefit experiments (ablation / pass^k / self-learning).

No LLM calls: run_trajectory and _seed_template are monkeypatched. The fake
trajectory derives its behaviour from the store directory contents, which is
exactly the variable the experiments are supposed to isolate.
"""

from __future__ import annotations

from pathlib import Path

from evaluation.dataset import BenchmarkCase
from evaluation.trajectory_eval import TrajectoryResult
import evaluation.experiments as experiments
from evaluation.experiments import (
    format_ablation,
    format_self_learning,
    is_repeat_class,
    run_ablation,
    run_self_learning,
)


def _make_case(case_id: str, repeat: bool) -> BenchmarkCase:
    return BenchmarkCase(
        id=case_id,
        bug_description=f"bug {case_id}",
        expected_root_cause_keywords=["redis"],
        expected_fix_keywords=["pool"],
        seed_case_ids=[f"seed-{case_id}"] if repeat else [],
        expected_top_hit_id=f"seed-{case_id}" if repeat else None,
    )


def _result(case_id: str, steps: int, correct: bool) -> TrajectoryResult:
    return TrajectoryResult(
        case_id=case_id,
        steps=steps,
        tokens_input=1000,
        tokens_output=100,
        estimated_cost_usd=0.001 * steps,
        time_seconds=float(steps),
        correct=correct,
        correctness_score=1.0 if correct else 0.0,
    )


def _fake_seed_template(template_dir: Path) -> int:
    """Stand-in for _seed_template: drop marker markdown files, no MemoryStore."""
    for i in range(3):
        (template_dir / f"seed-{i}.md").write_text("# seed", encoding="utf-8")
    return 3


def _memory_aware_trajectory(case, memory_dir, api_key=None, model=None):
    """Seeded store → fast and correct; empty store → slow, correct only for repeats.

    Mirrors the hypothesis under test so we can verify the experiment plumbing
    routes seeded/empty stores to the right arms and groups.
    """
    seeded = any(memory_dir.rglob("*.md"))
    if seeded:
        return _result(case.id, steps=3, correct=True)
    return _result(case.id, steps=9, correct=False)


# ── is_repeat_class ───────────────────────────────────────────────────────


class TestIsRepeatClass:
    def test_with_seed_ids(self):
        assert is_repeat_class(_make_case("a", repeat=True)) is True

    def test_without_seed_ids(self):
        assert is_repeat_class(_make_case("b", repeat=False)) is False


# ── Ablation A/B ─────────────────────────────────────────────────────────


class TestAblation:
    def test_arms_groups_and_deltas(self, monkeypatch):
        monkeypatch.setattr(experiments, "_seed_template", _fake_seed_template)
        monkeypatch.setattr(experiments, "run_trajectory", _memory_aware_trajectory)

        cases = [
            _make_case("r1", repeat=True),
            _make_case("r2", repeat=True),
            _make_case("n1", repeat=False),
        ]
        report, path = run_ablation(cases=cases, write_json=False)

        assert path is None
        w, n = report.with_memory, report.no_memory
        # Group split: 2 repeat + 1 novel in each arm.
        assert w.repeat_aggregate.total == 2
        assert w.novel_aggregate.total == 1
        assert n.aggregate.total == 3
        # Seeded arm saw the markers → fast/correct; empty arm did not.
        assert w.aggregate.correctness_rate == 1.0
        assert n.aggregate.correctness_rate == 0.0
        assert w.aggregate.mean_steps == 3.0
        assert n.aggregate.mean_steps == 9.0
        # Memory benefit shows up as negative step delta on repeat-class.
        assert report.deltas["repeat_delta_mean_steps"] == -6.0
        assert report.deltas["delta_correctness_rate"] == 1.0

    def test_each_run_gets_fresh_store(self, monkeypatch):
        monkeypatch.setattr(experiments, "_seed_template", _fake_seed_template)
        seen_dirs: list[Path] = []

        def tracking_trajectory(case, memory_dir, api_key=None, model=None):
            # A leftover from a previous run would prove store leakage.
            assert not (memory_dir / "leak-marker.md").exists()
            (memory_dir / "leak-marker.md").write_text("x", encoding="utf-8")
            seen_dirs.append(memory_dir)
            return _result(case.id, steps=1, correct=True)

        monkeypatch.setattr(experiments, "run_trajectory", tracking_trajectory)
        cases = [_make_case("a", repeat=True), _make_case("b", repeat=False)]
        run_ablation(cases=cases, runs=2, write_json=False)

        # 2 cases × 2 arms × 2 runs, all distinct directories.
        assert len(seen_dirs) == 8
        assert len(set(seen_dirs)) == 8

    def test_pass_hat_k_vs_pass_at_k(self, monkeypatch):
        monkeypatch.setattr(experiments, "_seed_template", _fake_seed_template)
        call_counts: dict[tuple[str, bool], int] = {}

        def flaky_trajectory(case, memory_dir, api_key=None, model=None):
            seeded = any(memory_dir.rglob("*.md"))
            key = (case.id, seeded)
            idx = call_counts.get(key, 0)
            call_counts[key] = idx + 1
            # Case "flaky" fails on its second run; everything else passes.
            correct = not (case.id == "flaky" and idx == 1)
            return _result(case.id, steps=2, correct=correct)

        monkeypatch.setattr(experiments, "run_trajectory", flaky_trajectory)
        cases = [_make_case("stable", repeat=True), _make_case("flaky", repeat=True)]
        report, _ = run_ablation(cases=cases, runs=2, write_json=False)

        w = report.with_memory
        assert w.pass_at_k == 1.0  # both cases pass at least once
        assert w.pass_hat_k == 0.5  # "flaky" doesn't pass every run
        assert report.no_memory.pass_hat_k == 0.5

    def test_sample_limits_cases(self, monkeypatch):
        monkeypatch.setattr(experiments, "_seed_template", _fake_seed_template)
        monkeypatch.setattr(experiments, "run_trajectory", _memory_aware_trajectory)
        cases = [_make_case(f"c{i}", repeat=False) for i in range(5)]
        report, _ = run_ablation(cases=cases, sample=2, write_json=False)
        assert report.with_memory.aggregate.total == 2

    def test_writes_json(self, monkeypatch, tmp_path):
        monkeypatch.setattr(experiments, "_seed_template", _fake_seed_template)
        monkeypatch.setattr(experiments, "run_trajectory", _memory_aware_trajectory)
        monkeypatch.setattr(experiments, "RESULTS_DIR", tmp_path)
        _, path = run_ablation(cases=[_make_case("a", repeat=True)], write_json=True)
        assert path is not None and path.exists()
        assert "deltas" in path.read_text(encoding="utf-8")


# ── Self-learning round-trip ──────────────────────────────────────────────


def _saving_trajectory(case, memory_dir, api_key=None, model=None):
    """Steps shrink as the store grows; each diagnosis saves one new file."""
    known = sum(1 for _ in memory_dir.rglob("*.md"))
    (memory_dir / f"saved-{case.id}-{known}.md").write_text("# case", encoding="utf-8")
    return _result(case.id, steps=max(10 - known, 1), correct=known > 0)


class TestSelfLearning:
    def test_round_two_is_cheaper(self, monkeypatch):
        monkeypatch.setattr(experiments, "run_trajectory", _saving_trajectory)
        cases = [_make_case(f"c{i}", repeat=False) for i in range(3)]
        report, _ = run_self_learning(cases=cases, rounds=2, write_json=False)

        assert len(report.rounds) == 2
        r1, r2 = report.rounds
        # Store grows monotonically across the whole experiment.
        sizes = [p.memory_files_before for p in r1.points + r2.points]
        assert sizes == sorted(sizes)
        assert r2.aggregate.mean_steps < r1.aggregate.mean_steps
        assert report.summary["delta_mean_steps"] < 0
        # Every case took fewer steps in the final round.
        assert all(p["delta_steps"] < 0 for p in report.paired_deltas)

    def test_single_round_has_no_pairing(self, monkeypatch):
        monkeypatch.setattr(experiments, "run_trajectory", _saving_trajectory)
        report, _ = run_self_learning(
            cases=[_make_case("a", repeat=False)], rounds=1, write_json=False
        )
        assert report.summary == {}
        assert report.paired_deltas == []


# ── Formatters ────────────────────────────────────────────────────────────


class TestDegradedRunDetection:
    """Graceful degradation must surface as an error, not a silent wrong answer."""

    def test_budget_exceeded_reasoning_becomes_error(self, monkeypatch, tmp_path):
        from types import SimpleNamespace

        import debug_mind.agent as agent_mod
        import evaluation.trajectory_eval as te
        from debug_mind.schemas import DiagnosisResult

        class FakeAgent:
            def __init__(self, **kwargs):
                pass

            def diagnose(self, **kwargs):
                return DiagnosisResult(
                    case_id="unknown",
                    root_cause="",
                    confidence=0.0,
                    diagnosis_steps=[],
                    fix_suggestion="",
                    similar_cases_found=0,
                    reasoning="[Budget exceeded: API error: 401 invalid key]",
                )

        monkeypatch.setattr(agent_mod, "DiagnosticAgent", FakeAgent)
        monkeypatch.setattr(
            te, "MemoryStore", lambda memory_dir: SimpleNamespace(close=lambda: None)
        )

        case = _make_case("degraded", repeat=False)
        res = te.run_trajectory(case, tmp_path)
        assert res.error is not None and "Budget exceeded" in res.error
        assert res.correct is False
        # API auth failure must classify as an API/budget problem, not reasoning.
        assert res.failure_mode in (
            te.FailureMode.API_ERROR,
            te.FailureMode.BUDGET_EXCEEDED,
        )


class TestFormatters:
    def test_format_ablation_smoke(self, monkeypatch):
        monkeypatch.setattr(experiments, "_seed_template", _fake_seed_template)
        monkeypatch.setattr(experiments, "run_trajectory", _memory_aware_trajectory)
        report, _ = run_ablation(
            cases=[_make_case("r", repeat=True), _make_case("n", repeat=False)],
            runs=2,
            write_json=False,
        )
        text = format_ablation(report)
        assert "Memory ablation A/B" in text
        assert "repeat-class" in text
        assert "pass^k" in text  # stability table renders when k > 1

    def test_format_self_learning_smoke(self, monkeypatch):
        monkeypatch.setattr(experiments, "run_trajectory", _saving_trajectory)
        report, _ = run_self_learning(
            cases=[_make_case("a", repeat=False)], rounds=2, write_json=False
        )
        text = format_self_learning(report)
        assert "Self-learning round-trip" in text
        assert "Flywheel effect" in text
