"""Tests for Task B features — dedup, verified rerank, mark_used, verify CLI."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from debug_mind.cli import main
from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import BugCase


@pytest.fixture
def store():
    """Create an isolated MemoryStore for testing."""
    tmp_dir = tempfile.mkdtemp(prefix="test_taskb_")
    s = MemoryStore(memory_dir=Path(tmp_dir))
    yield s
    shutil.rmtree(tmp_dir, ignore_errors=True)


def _make_case(**overrides) -> BugCase:
    defaults = dict(
        title="Test NPE in login",
        symptoms="NullPointerException on UserService.login",
        root_cause="Missing null check on session token",
        fix_suggestion="Add null check before .equals() call",
        tags=["npe", "login"],
        verified=False,
    )
    defaults.update(overrides)
    return BugCase(**defaults)


class TestDedup:
    def test_dedup_verified_high_similarity_merges(self, store: MemoryStore):
        """Saving a case similar to a verified case returns the existing one."""
        existing = _make_case(id="case-a", verified=True)
        store.save(existing)

        new_case = _make_case(id="case-b", symptoms="NPE on UserService.login endpoint")
        result = store.save(new_case)

        assert result.id == "case-a"
        assert "Variant" in result.symptoms

    def test_dedup_unverified_does_not_merge(self, store: MemoryStore):
        """Similar but unverified cases are kept separate for diversity."""
        existing = _make_case(id="case-a", verified=False)
        store.save(existing)

        new_case = _make_case(id="case-b", symptoms="NPE on login endpoint")
        result = store.save(new_case)

        assert result.id == "case-b"

    def test_dedup_low_similarity_does_not_merge(self, store: MemoryStore):
        """Cases with low similarity are always kept separate."""
        existing = _make_case(id="case-a", verified=True, symptoms="Redis connection timeout")
        store.save(existing)

        new_case = _make_case(
            id="case-b",
            symptoms="OOM on large dataset export",
            root_cause="Loading entire table into memory",
        )
        result = store.save(new_case)

        assert result.id == "case-b"


class TestRerank:
    def test_verified_ranks_higher_than_unverified(self, store: MemoryStore):
        """With same raw score, verified case should appear before unverified."""
        # Use very different tags to avoid dedup collision on unverified case
        verified = _make_case(
            id="v-case",
            title="Redis pool NPE",
            symptoms="NPE when Redis pool exhausted during peak traffic",
            root_cause="Connection pool max-active too small for concurrent load",
            tags=["redis", "npe", "pool"],
            verified=True,
        )
        store.save(verified)

        # This is unverified but similar — make it different enough to avoid dedup
        unverified = _make_case(
            id="u-case",
            title="Jedis pool connection failure NPE",
            symptoms="NPE when Jedis pool runs out of connections in worker thread",
            root_cause="Jedis pool configuration insufficient for worker threads",
            tags=["jedis", "npe", "worker"],
            verified=False,
        )
        store.save(unverified)

        results = store.search("Redis pool NPE exhausted connection", top_k=5, min_score=0.0)
        assert len(results) >= 1

        # If both are returned, verified should come first
        v_pos = next((i for i, r in enumerate(results) if r.case.id == "v-case"), None)
        u_pos = next((i for i, r in enumerate(results) if r.case.id == "u-case"), None)

        if v_pos is not None and u_pos is not None:
            assert v_pos < u_pos, "Verified case should rank before unverified"

    def test_search_include_unverified_false(self, store: MemoryStore):
        """When include_unverified=False, only verified cases appear."""
        unverified = _make_case(
            id="u-case",
            verified=False,
            title="Unique unverified case about dolphins",
            symptoms="Dolphin migration pattern analysis failure",
            tags=["dolphin", "unique-u"],
        )
        store.save(unverified)

        verified = _make_case(
            id="v-case",
            verified=True,
            title="Unique verified case about dolphins",
            symptoms="Dolphin migration pattern analysis success",
            tags=["dolphin", "unique-v"],
        )
        store.save(verified)

        results = store.search(
            "dolphin migration analysis", top_k=5, include_unverified=False, min_score=0.0
        )
        ids = [r.case.id for r in results]
        assert "u-case" not in ids
        assert "v-case" in ids


class TestMarkUsed:
    def test_mark_used_increments_counters(self, store: MemoryStore):
        case = _make_case(id="mark-test")
        store.save(case)

        store.mark_used("mark-test")

        updated = store.get("mark-test")
        assert updated is not None
        assert updated.hit_count == 1
        assert updated.last_used_at is not None

    def test_mark_used_multiple(self, store: MemoryStore):
        case = _make_case(id="mark-multi")
        store.save(case)

        store.mark_used("mark-multi")
        store.mark_used("mark-multi")

        updated = store.get("mark-multi")
        assert updated is not None
        assert updated.hit_count == 2

    def test_mark_used_nonexistent(self, store: MemoryStore):
        # Should not raise
        store.mark_used("nonexistent-id")


class TestVerify:
    def test_verify_correct(self, store: MemoryStore):
        case = _make_case(id="verify-ok")
        store.save(case)

        ok = store.verify("verify-ok", correct=True, notes="Confirmed by author")
        assert ok

        updated = store.get("verify-ok")
        assert updated is not None
        assert updated.verified is True
        assert updated.verification_notes == "Confirmed by author"

        # Should still be searchable
        results = store.search(updated.to_search_text(), top_k=1)
        assert len(results) >= 1

    def test_verify_wrong_soft_deletes(self, store: MemoryStore):
        case = _make_case(id="verify-bad", tags=["unique-verify-wrong"])
        store.save(case)

        ok = store.verify("verify-bad", correct=False, notes="Wrong diagnosis")
        assert ok

        # Case should no longer be retrievable via get (renamed to .rejected)
        assert store.get("verify-bad") is None

        # Rejected file should exist
        rejected = store.cases_dir / "verify-bad.md.rejected"
        assert rejected.exists()

    def test_verify_nonexistent(self, store: MemoryStore):
        ok = store.verify("nonexistent", correct=True)
        assert ok is False


class TestVerifyCLI:
    def test_verify_correct_cli(self, tmp_path, monkeypatch):
        memory_dir = tmp_path / "verify_memory"
        memory_dir.mkdir()
        monkeypatch.setenv("DEBUG_MIND_MEMORY_DIR", str(memory_dir))
        store = MemoryStore(memory_dir=memory_dir)

        case = _make_case(id="cli-verify-test")
        store.save(case)

        runner = CliRunner()
        result = runner.invoke(main, ["verify", "cli-verify-test", "--correct"])
        assert result.exit_code == 0, result.output
        assert "verified" in result.output.lower()

    def test_verify_wrong_cli(self, tmp_path, monkeypatch):
        memory_dir = tmp_path / "wrong_memory"
        memory_dir.mkdir()
        monkeypatch.setenv("DEBUG_MIND_MEMORY_DIR", str(memory_dir))
        store = MemoryStore(memory_dir=memory_dir)

        case = _make_case(id="cli-wrong-test")
        store.save(case)

        runner = CliRunner()
        result = runner.invoke(main, ["verify", "cli-wrong-test", "--wrong"])
        assert result.exit_code == 0, result.output
        assert "rejected" in result.output.lower()

    def test_verify_no_flag(self):
        runner = CliRunner()
        result = runner.invoke(main, ["verify", "some-id"])
        assert result.exit_code != 0


class TestOldMarkdownCompat:
    def test_old_markdown_parses_with_defaults(self, store: MemoryStore):
        """Old markdown without new fields should parse with default values."""
        old_md = """# Old Bug Case

> case_id: `old-case-1` | severity: **high** | status: **fixed**

## Environment
- language: Java

## Symptoms
Something broke

## Error Log
```
NullPointerException
```

## Root Cause
Missing null check

## Diagnosis Steps
1. Found the issue

## Fix Suggestion
Add null check

## Tags
npe, old

---
- created: 2025-05-18T10:30:00+00:00
- updated: 2025-05-18T11:15:00+00:00
- similar_cases: []
"""
        md_path = store.cases_dir / "old-case-1.md"
        md_path.write_text(old_md, encoding="utf-8")

        from debug_mind.memory.store import _markdown_to_case

        case = _markdown_to_case(md_path)

        assert case is not None
        assert case.id == "old-case-1"
        assert case.verified is False
        assert case.verification_notes == ""
        assert case.hit_count == 0
        assert case.last_used_at is None
        assert case.superseded_by is None
