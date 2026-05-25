"""Tests for agent trajectory eval (P6-4) — does NOT call the LLM."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from debug_mind.schemas import DiagnosisResult

from evaluation.dataset import BenchmarkCase
from evaluation.trajectory_eval import (
    TrajectoryResult,
    aggregate,
    _percentile,
    judge_correctness,
    run_trajectory,
)


def _make_case(**overrides) -> BenchmarkCase:
    base = dict(
        id="case-test",
        bug_description="some bug",
        error_log="",
        expected_root_cause_keywords=["redis", "pool"],
        expected_fix_keywords=["max-active", "increase"],
    )
    base.update(overrides)
    return BenchmarkCase(**base)


def _make_diagnosis(root_cause: str = "", fix: str = "", reasoning: str = "") -> DiagnosisResult:
    return DiagnosisResult(
        case_id="test",
        root_cause=root_cause,
        confidence=1.0,
        diagnosis_steps=[],
        fix_suggestion=fix,
        similar_cases_found=0,
        reasoning=reasoning,
    )


# ── judge_correctness ─────────────────────────────────────────────────────


class TestJudgeCorrectness:
    def test_all_keywords_present_is_correct(self):
        case = _make_case()
        diag = _make_diagnosis(
            root_cause="Redis connection pool exhausted",
            fix="Increase max-active to 50",
        )
        ok, score = judge_correctness(diag, case)
        assert ok is True
        assert score == 1.0

    def test_half_keywords_present_at_threshold_is_correct(self):
        case = _make_case()  # 4 keywords total
        diag = _make_diagnosis(
            root_cause="Redis pool issue",  # hits 'redis', 'pool' = 2/4 = 0.5
            fix="see runbook",
        )
        ok, score = judge_correctness(diag, case, threshold=0.5)
        assert ok is True
        assert score == 0.5

    def test_below_threshold_is_incorrect(self):
        case = _make_case()
        diag = _make_diagnosis(root_cause="random text", fix="")
        ok, score = judge_correctness(diag, case)
        assert ok is False
        assert score == 0.0

    def test_no_keywords_treated_as_correct(self):
        case = _make_case(expected_root_cause_keywords=[], expected_fix_keywords=[])
        diag = _make_diagnosis(root_cause="x", fix="y")
        ok, score = judge_correctness(diag, case)
        assert ok is True
        assert score == 1.0

    def test_case_insensitive_match(self):
        case = _make_case(
            expected_root_cause_keywords=["REDIS", "Pool"],
            expected_fix_keywords=[],
        )
        diag = _make_diagnosis(root_cause="redis pool exhausted")
        ok, score = judge_correctness(diag, case)
        assert ok is True
        assert score == 1.0


# ── aggregate ────────────────────────────────────────────────────────────


def _result(case_id, steps, correct, error=None):
    return TrajectoryResult(
        case_id=case_id,
        steps=steps,
        tokens_input=1000,
        tokens_output=200,
        estimated_cost_usd=0.01,
        time_seconds=2.5,
        correct=correct,
        correctness_score=1.0 if correct else 0.0,
        top_root_cause="rc",
        error=error,
    )


class TestAggregate:
    def test_empty_input(self):
        agg = aggregate([])
        assert agg.total == 0
        assert agg.correctness_rate == 0.0
        assert agg.mean_steps == 0.0

    def test_basic_aggregation(self):
        results = [
            _result("a", 3, True),
            _result("b", 5, True),
            _result("c", 9, False),
        ]
        agg = aggregate(results)
        assert agg.total == 3
        assert agg.correct_count == 2
        assert agg.correctness_rate == pytest.approx(2 / 3, rel=1e-3)
        assert agg.mean_steps == pytest.approx((3 + 5 + 9) / 3, rel=1e-3)
        assert agg.p50_steps == 5.0
        assert agg.total_cost_usd == pytest.approx(0.03, rel=1e-3)

    def test_errors_excluded_from_means_but_counted_in_total(self):
        results = [
            _result("ok", 4, True),
            _result("fail", 0, False, error="API exploded"),
        ]
        agg = aggregate(results)
        assert agg.total == 2
        assert agg.error_count == 1
        # Mean steps computed only over successful run (one with steps=4)
        assert agg.mean_steps == 4.0


# ── _percentile helper ──────────────────────────────────────────────────


class TestPercentile:
    def test_empty_returns_zero(self):
        assert _percentile([], 50) == 0.0

    def test_single_element(self):
        assert _percentile([7], 50) == 7.0

    def test_p50_of_odd_length(self):
        assert _percentile([1, 2, 3, 4, 5], 50) == 3.0

    def test_p95_picks_top_end(self):
        values = list(range(1, 21))  # 1..20
        # linear interp p95 of 20 values: k = 19 * 0.95 = 18.05 → 19 + 0.05*(20-19) = 19.05
        assert _percentile(values, 95) == pytest.approx(19.05, abs=0.01)


# ── run_trajectory (mocked LLM) ─────────────────────────────────────────


class TestRunTrajectorySmoke:
    def test_runs_with_mocked_agent(self, tmp_path, monkeypatch):
        """Verify the pipeline assembles without calling the real LLM."""
        case = _make_case(bug_description="redis pool exhausted")

        from evaluation import trajectory_eval as te

        # Patch DiagnosticAgent: the test only cares that run_trajectory
        # threads through, captures budget state, and returns a result.
        class _StubAgent:
            def __init__(self, memory, api_key=None, model=None, budget=None):
                self.memory = memory
                self.budget = budget

            def diagnose(self, bug_description, error_log, environment):
                # Pretend we used some tokens, returned a partial-but-on-topic diagnosis
                from debug_mind.schemas import DiagnosisResult

                if self.budget is not None:
                    fake_usage = MagicMock()
                    fake_usage.input_tokens = 1500
                    fake_usage.output_tokens = 250
                    fake_usage.cache_read_input_tokens = 0
                    fake_usage.cache_creation_input_tokens = 0
                    self.budget.record(fake_usage)
                return DiagnosisResult(
                    case_id="stub",
                    root_cause="Redis pool exhausted under load",
                    confidence=1.0,
                    diagnosis_steps=["search_memory(q)", "save_to_memory()"],
                    fix_suggestion="Increase max-active to 50",
                    similar_cases_found=1,
                    reasoning="search hit",
                )

        # The agent module is imported lazily inside run_trajectory.
        monkeypatch.setattr(
            "debug_mind.agent.DiagnosticAgent", _StubAgent, raising=True
        )

        result = te.run_trajectory(case, tmp_path / "mem", api_key="fake")
        assert result.error is None
        assert result.steps == 2
        assert result.tokens_input == 1500
        assert result.tokens_output == 250
        assert result.correct is True
        assert result.correctness_score == 1.0
