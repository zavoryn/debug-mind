"""Benchmark runner and scorer — evaluates memory retrieval quality.

Metrics:
  - hit@k: whether the expected case appears in top-k results
  - MRR: Mean Reciprocal Rank
  - keyword_recall: fraction of expected keywords found in recalled case's fields

Used by `debug-mind eval` CLI command. Runs against an isolated MemoryStore
to avoid polluting the user's real memory.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import BugCase, SearchResult

from evaluation.dataset import BenchmarkCase, load_all_cases

SEED_CASES_DIR = Path(__file__).parent / "seed_cases"


@dataclass
class CaseResult:
    """Result of evaluating a single benchmark case."""

    case_id: str
    hit_at_1: bool = False
    hit_at_3: bool = False
    hit_at_5: bool = False
    mrr: float = 0.0
    keyword_recall: float = 0.0
    top_hit_id: str | None = None
    error: str | None = None


@dataclass
class EvalResult:
    """Aggregate evaluation result across all benchmark cases."""

    total: int = 0
    hit_at_1: float = 0.0
    hit_at_3: float = 0.0
    hit_at_5: float = 0.0
    mrr: float = 0.0
    keyword_recall: float = 0.0
    case_results: list[CaseResult] = field(default_factory=list)


def _seed_store(store: MemoryStore, seed_ids: list[str]) -> dict[str, BugCase]:
    """Load seed case markdown files into the store. Returns id→BugCase map."""
    loaded = {}
    for seed_id in seed_ids:
        md_path = SEED_CASES_DIR / f"{seed_id}.md"
        if not md_path.exists():
            continue
        # Parse the markdown to a BugCase using the store's parser
        from debug_mind.memory.store import _markdown_to_case

        case = _markdown_to_case(md_path)
        if case:
            store.save(case)
            loaded[case.id] = case
    return loaded


def _seed_all(store: MemoryStore) -> dict[str, BugCase]:
    """Load all seed case markdown files into the store."""
    loaded = {}
    for md_path in sorted(SEED_CASES_DIR.glob("*.md")):
        from debug_mind.memory.store import _markdown_to_case

        case = _markdown_to_case(md_path)
        if case:
            store.save(case)
            loaded[case.id] = case
    return loaded


def _compute_mrr(rank: int | None) -> float:
    """Compute reciprocal rank. rank is 1-based; None means not found → 0."""
    return 1.0 / rank if rank else 0.0


def _find_rank(results: list[SearchResult], target_id: str) -> int | None:
    """Find 1-based rank of target_id in results. None if not found."""
    for i, r in enumerate(results):
        if r.case.id == target_id:
            return i + 1
    return None


def _keyword_recall(text: str, expected_keywords: list[str]) -> float:
    """Fraction of expected keywords found (case-insensitive substring match)."""
    if not expected_keywords:
        return 1.0
    text_lower = text.lower()
    hits = sum(1 for kw in expected_keywords if kw.lower() in text_lower)
    return hits / len(expected_keywords)


def evaluate_case(
    case: BenchmarkCase,
    store: MemoryStore,
) -> CaseResult:
    """Evaluate a single benchmark case against the memory store."""
    result = CaseResult(case_id=case.id)

    # Build search query from the case
    query_parts = [case.bug_description]
    if case.error_log:
        query_parts.append(case.error_log)
    query = "\n".join(query_parts)

    # Search
    search_results = store.search(query=query, top_k=5, min_score=0.0)

    if not search_results:
        return result

    # hit@k and MRR based on expected_top_hit_id
    if case.expected_top_hit_id:
        rank = _find_rank(search_results, case.expected_top_hit_id)
        result.mrr = _compute_mrr(rank)
        result.hit_at_1 = rank == 1 if rank else False
        result.hit_at_3 = rank is not None and rank <= 3
        result.hit_at_5 = rank is not None and rank <= 5

    # Top hit for keyword recall
    top_result = search_results[0]
    result.top_hit_id = top_result.case.id

    combined_text = f"{top_result.case.root_cause} {top_result.case.fix_suggestion}"
    all_keywords = case.expected_root_cause_keywords + case.expected_fix_keywords
    result.keyword_recall = _keyword_recall(combined_text, all_keywords)

    return result


def run_eval(
    cases: list[BenchmarkCase] | None = None,
    search_only: bool = True,
) -> EvalResult:
    """Run the full evaluation benchmark.

    Creates an isolated MemoryStore in a temp directory, seeds it with all
    seed cases, then evaluates each benchmark case.
    """
    if cases is None:
        cases = load_all_cases()

    # Create isolated store
    tmp_dir = tempfile.mkdtemp(prefix="debug_mind_eval_")
    store = None
    try:
        store = MemoryStore(memory_dir=Path(tmp_dir))

        # Seed all cases
        _seed_all(store)

        case_results: list[CaseResult] = []
        for bc in cases:
            cr = evaluate_case(bc, store)
            case_results.append(cr)

    finally:
        if store is not None:
            store.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Aggregate
    total = len(case_results)
    if total == 0:
        return EvalResult(total=0)

    hit1 = sum(1 for cr in case_results if cr.hit_at_1) / total
    hit3 = sum(1 for cr in case_results if cr.hit_at_3) / total
    hit5 = sum(1 for cr in case_results if cr.hit_at_5) / total
    mrr = sum(cr.mrr for cr in case_results) / total
    kw = sum(cr.keyword_recall for cr in case_results) / total

    return EvalResult(
        total=total,
        hit_at_1=round(hit1, 4),
        hit_at_3=round(hit3, 4),
        hit_at_5=round(hit5, 4),
        mrr=round(mrr, 4),
        keyword_recall=round(kw, 4),
        case_results=case_results,
    )


def format_results(result: EvalResult) -> str:
    """Format evaluation results as a human-readable table."""
    lines = []
    lines.append("+---------------------+------+------+-------+-------+------+")
    lines.append("| Case                | hit@1| hit@3| hit@5 | MRR   |  KW  |")
    lines.append("+---------------------+------+------+-------+-------+------+")

    for cr in result.case_results:
        h1 = "  Y  " if cr.hit_at_1 else "  -  "
        h3 = "  Y  " if cr.hit_at_3 else "  -  "
        h5 = "  Y  " if cr.hit_at_5 else "  -  "
        mrr = f"{cr.mrr:.3f}" if cr.mrr > 0 else "0.000"
        kw = f"{cr.keyword_recall:.0%}"

        name = cr.case_id[:19].ljust(19)
        lines.append(f"| {name} |{h1}|{h3}|{h5} | {mrr} | {kw:>4} |")

    lines.append("+---------------------+------+------+-------+-------+------+")
    n = f"({result.total})"
    overall_label = f"Overall {n}".ljust(19)
    lines.append(
        f"| {overall_label} |{result.hit_at_1:.2f} | {result.hit_at_3:.2f} | {result.hit_at_5:.2f}  | {result.mrr:.2f}  | {result.keyword_recall:.2f} |"
    )
    lines.append("+---------------------+------+------+-------+-------+------+")

    return "\n".join(lines)
