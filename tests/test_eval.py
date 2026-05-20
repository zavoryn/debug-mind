"""Tests for the evaluation framework — dataset loading, scoring, and CLI."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from debug_mind.cli import main
from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import BugCase, SearchResult

from evaluation.dataset import BenchmarkCase, load_all_cases, load_case
from evaluation.benchmark import (
    CaseResult,
    EvalResult,
    evaluate_case,
    run_eval,
    format_results,
    _keyword_recall,
    _find_rank,
    _compute_mrr,
    SEED_CASES_DIR,
)


# ── Dataset loading ───────────────────────────────────────────────────


class TestDatasetLoading:
    def test_load_all_cases_returns_expected_count(self):
        cases = load_all_cases()
        assert len(cases) >= 12

    def test_load_all_cases_ids_are_unique(self):
        cases = load_all_cases()
        ids = [c.id for c in cases]
        assert len(ids) == len(set(ids))

    def test_load_single_case(self):
        case = load_case("redis-pool-npe")
        assert case is not None
        assert case.id == "redis-pool-npe"
        assert len(case.expected_root_cause_keywords) > 0
        assert case.expected_top_hit_id is not None

    def test_load_nonexistent_case(self):
        case = load_case("does-not-exist")
        assert case is None

    def test_all_cases_have_required_fields(self):
        cases = load_all_cases()
        for c in cases:
            assert c.id, "Missing id"
            assert c.bug_description, f"Missing bug_description in {c.id}"
            assert len(c.expected_root_cause_keywords) >= 2, (
                f"Too few root_cause keywords in {c.id}"
            )
            assert len(c.expected_fix_keywords) >= 2, f"Too few fix keywords in {c.id}"
            assert len(c.seed_case_ids) >= 1, f"No seed_case_ids in {c.id}"

    def test_seed_cases_dir_exists_and_has_files(self):
        assert SEED_CASES_DIR.exists()
        md_files = list(SEED_CASES_DIR.glob("*.md"))
        assert len(md_files) >= 20

    def test_all_seed_cases_parseable(self):
        from debug_mind.memory.store import _markdown_to_case

        for md_file in sorted(SEED_CASES_DIR.glob("*.md")):
            case = _markdown_to_case(md_file)
            assert case is not None, f"Failed to parse {md_file.name}"
            assert case.title != "parsed", f"Title not extracted from {md_file.name}"
            assert case.symptoms, f"No symptoms in {md_file.name}"


# ── Scorer arithmetic ─────────────────────────────────────────────────


class TestScorerArithmetic:
    def test_keyword_recall_all_match(self):
        text = "Redis connection pool exhausted causing null pointer"
        keywords = ["redis", "connection pool", "null"]
        assert _keyword_recall(text, keywords) == 1.0

    def test_keyword_recall_partial(self):
        text = "Redis connection pool issue"
        keywords = ["redis", "null pointer", "deadlock"]
        assert _keyword_recall(text, keywords) == pytest.approx(1 / 3)

    def test_keyword_recall_empty_keywords(self):
        assert _keyword_recall("any text", []) == 1.0

    def test_keyword_recall_no_match(self):
        assert _keyword_recall("everything is fine", ["redis", "oom"]) == 0.0

    def test_find_rank_found(self):
        case = BugCase(title="test", symptoms="test", id="target123")
        results = [
            SearchResult(case=BugCase(title="a", symptoms="a", id="other1"), score=0.9),
            SearchResult(case=case, score=0.8),
        ]
        assert _find_rank(results, "target123") == 2

    def test_find_rank_not_found(self):
        results = [SearchResult(case=BugCase(title="a", symptoms="a", id="x"), score=0.9)]
        assert _find_rank(results, "missing") is None

    def test_compute_mrr(self):
        assert _compute_mrr(1) == 1.0
        assert _compute_mrr(2) == 0.5
        assert _compute_mrr(3) == pytest.approx(1 / 3)
        assert _compute_mrr(None) == 0.0


# ── Evaluation runner ─────────────────────────────────────────────────


class TestEvalRunner:
    def test_run_eval_returns_results(self):
        cases = load_all_cases()
        result = run_eval(cases=cases)
        assert result.total == len(cases)
        assert 0 <= result.hit_at_1 <= 1.0
        assert 0 <= result.mrr <= 1.0
        assert 0 <= result.keyword_recall <= 1.0

    def test_run_eval_empty_cases(self):
        result = run_eval(cases=[])
        assert result.total == 0
        assert result.hit_at_1 == 0.0

    def test_format_results(self):
        result = EvalResult(
            total=2,
            hit_at_1=0.5,
            hit_at_3=1.0,
            hit_at_5=1.0,
            mrr=0.75,
            keyword_recall=0.6,
            case_results=[
                CaseResult(
                    case_id="test-a",
                    hit_at_1=True,
                    hit_at_3=True,
                    hit_at_5=True,
                    mrr=1.0,
                    keyword_recall=0.8,
                ),
                CaseResult(
                    case_id="test-b",
                    hit_at_1=False,
                    hit_at_3=True,
                    hit_at_5=True,
                    mrr=0.5,
                    keyword_recall=0.4,
                ),
            ],
        )
        text = format_results(result)
        assert "test-a" in text
        assert "test-b" in text
        assert "Overall" in text

    def test_evaluate_case_with_mock_store(self):
        """Test evaluate_case with a store that has known content."""
        tmp_dir = tempfile.mkdtemp(prefix="test_eval_")
        try:
            store = MemoryStore(memory_dir=Path(tmp_dir))

            seed = BugCase(
                id="seed-test",
                title="Redis pool exhaustion NPE",
                symptoms="NPE when Redis connection pool runs out",
                root_cause="Redis connection pool max-active=8 too small, causing null returns",
                fix_suggestion="Increase max-active to 32 and add null checks",
                tags=["redis", "npe", "connection-pool"],
            )
            store.save(seed)

            bc = BenchmarkCase(
                id="test-case-1",
                bug_description="NPE on login when Redis pool exhausted",
                expected_root_cause_keywords=["redis", "connection pool", "null"],
                expected_fix_keywords=["max-active", "null check"],
                expected_top_hit_id="seed-test",
                seed_case_ids=["seed-test"],
            )

            result = evaluate_case(bc, store)
            assert result.case_id == "test-case-1"
            assert result.top_hit_id == "seed-test"
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ── CLI eval command ──────────────────────────────────────────────────


class TestEvalCLI:
    def test_eval_search_only_no_api_key(self):
        """eval --search-only should work without ANTHROPIC_API_KEY."""
        runner = CliRunner(env={"ANTHROPIC_API_KEY": ""})
        result = runner.invoke(main, ["eval", "--search-only"])
        assert result.exit_code == 0, result.output
        assert "Overall" in result.output

    def test_eval_single_case(self):
        runner = CliRunner(env={"ANTHROPIC_API_KEY": ""})
        result = runner.invoke(main, ["eval", "--search-only", "--case", "redis-pool-npe"])
        assert result.exit_code == 0, result.output
        assert "redis-pool-npe" in result.output

    def test_eval_json_output(self, tmp_path):
        json_path = tmp_path / "results.json"
        runner = CliRunner(env={"ANTHROPIC_API_KEY": ""})
        result = runner.invoke(main, ["eval", "--search-only", "--json", str(json_path)])
        assert result.exit_code == 0, result.output
        assert json_path.exists()
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert "total" in data
        assert "mrr" in data
        assert data["total"] >= 12

    def test_eval_nonexistent_case(self):
        runner = CliRunner()
        result = runner.invoke(main, ["eval", "--case", "nonexistent"])
        assert result.exit_code != 0
