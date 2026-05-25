"""Tests for the multi-namespace MemoryStore (P6-2).

Covers:
  - namespace isolation (two namespaces don't bleed)
  - fallback_namespaces topping up when local ns is sparse
  - auto-migration from pre-Phase-6 layout (memory/cases/) to memory/default/
  - default-namespace behaviour is identical to Phase 5 callers that don't
    pass a namespace argument
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import BugCase, Severity


def _make_case(title: str, root_cause: str, tags=None) -> BugCase:
    return BugCase(
        title=title,
        symptoms=f"{title} symptoms",
        root_cause=root_cause,
        fix_suggestion=f"fix for {title}",
        severity=Severity.HIGH,
        tags=tags or [],
    )


class TestNamespaceIsolation:
    def test_two_namespaces_do_not_bleed(self, tmp_path):
        base = tmp_path / "mem"
        store_a = MemoryStore(memory_dir=base, namespace="team-a")
        store_b = MemoryStore(memory_dir=base, namespace="team-b")

        store_a.save(_make_case("A redis bug", "Redis connection pool exhausted"))
        store_b.save(_make_case("B database bug", "Postgres deadlock"))

        a_results = store_a.search("redis", top_k=5)
        b_results = store_b.search("redis", top_k=5)

        a_ids = {r.case.id for r in a_results}
        b_ids = {r.case.id for r in b_results}

        assert any("redis" in r.case.root_cause.lower() for r in a_results)
        assert a_ids.isdisjoint(b_ids), "namespaces leaked cases"

        # Directory layout reflects isolation
        assert (base / "team-a" / "cases").exists()
        assert (base / "team-b" / "cases").exists()
        store_a.close()
        store_b.close()

    def test_stats_per_namespace(self, tmp_path):
        base = tmp_path / "mem"
        store_a = MemoryStore(memory_dir=base, namespace="team-a")
        store_b = MemoryStore(memory_dir=base, namespace="team-b")
        store_a.save(_make_case("A1", "rc1"))
        store_a.save(_make_case("A2", "rc2"))
        store_b.save(_make_case("B1", "rc1b"))

        assert store_a.stats().total_cases == 2
        assert store_b.stats().total_cases == 1
        store_a.close()
        store_b.close()

    def test_invalid_namespace_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid namespace"):
            MemoryStore(memory_dir=tmp_path / "m", namespace="foo/bar")
        with pytest.raises(ValueError, match="Invalid namespace"):
            MemoryStore(memory_dir=tmp_path / "m", namespace="")
        with pytest.raises(ValueError, match="Invalid namespace"):
            MemoryStore(memory_dir=tmp_path / "m", namespace=".hidden")


class TestFallbackNamespaces:
    def test_fallback_used_when_local_empty(self, tmp_path):
        base = tmp_path / "mem"
        # Pre-populate "shared" with cases
        shared = MemoryStore(memory_dir=base, namespace="shared")
        shared.save(_make_case("kafka broker timeout", "kafka client misconfigured"))
        shared.save(_make_case("kafka rebalance", "consumer group churn"))
        shared.close()

        # team-x is empty — search with fallback to shared should hit
        team_x = MemoryStore(memory_dir=base, namespace="team-x")
        results = team_x.search(
            "kafka timeout", top_k=3, fallback_namespaces=["shared"]
        )
        assert len(results) >= 1
        # All hits should be tagged with origin namespace
        assert all(r.from_namespace == "shared" for r in results)
        team_x.close()

    def test_fallback_skipped_when_local_has_enough(self, tmp_path):
        base = tmp_path / "mem"
        shared = MemoryStore(memory_dir=base, namespace="shared")
        shared.save(_make_case("shared kafka", "fix in shared"))
        shared.close()

        local = MemoryStore(memory_dir=base, namespace="local")
        for i in range(4):
            local.save(_make_case(f"local kafka {i}", "local fix"))

        results = local.search(
            "kafka", top_k=3, fallback_namespaces=["shared"]
        )
        # All results should be local — fallback only kicks in when len < 3
        assert len(results) == 3
        assert all(r.from_namespace is None for r in results)
        local.close()

    def test_fallback_ignores_unknown_namespace(self, tmp_path):
        base = tmp_path / "mem"
        store = MemoryStore(memory_dir=base, namespace="solo")
        # Should not raise — unknown fallback ns is silently skipped
        results = store.search("anything", top_k=3, fallback_namespaces=["does-not-exist"])
        assert results == []
        store.close()


class TestLegacyMigration:
    def test_legacy_layout_is_migrated(self, tmp_path):
        # Simulate a pre-Phase-6 install: memory/cases/<x>.md + memory/sqlite/cases.db
        base = tmp_path / "mem"
        legacy_cases = base / "cases"
        legacy_cases.mkdir(parents=True)
        legacy_md = legacy_cases / "abc123.md"
        legacy_md.write_text(
            "# Old case\n\n"
            "> case_id: `abc123` | severity: **high** | status: **reported**\n\n"
            "## Symptoms\nOld\n\n## Root Cause\nOld root\n\n"
            "## Fix Suggestion\nOld fix\n\n## Tags\nlegacy\n\n---\n"
            "- created: 2025-01-01T00:00:00+00:00\n"
            "- updated: 2025-01-01T00:00:00+00:00\n"
            "- similar_cases: []\n- verified: true\n"
            "- verification_notes: \n- hit_count: 0\n- last_used_at: \n"
            "- superseded_by: \n- last_verified_at: \n- verify_count: 0\n"
            "- version: 1\n- links: []\n",
            encoding="utf-8",
        )

        # First init with default namespace triggers migration
        store = MemoryStore(memory_dir=base, namespace="default")

        # Legacy dir is gone; default/cases exists with the file
        assert not (base / "cases").exists()
        assert (base / "default" / "cases" / "abc123.md").exists()
        # Case is loadable through the new store
        got = store.get("abc123")
        assert got is not None
        assert got.title == "Old case"
        store.close()

    def test_migration_skipped_when_default_already_exists(self, tmp_path):
        base = tmp_path / "mem"
        legacy_cases = base / "cases"
        legacy_cases.mkdir(parents=True)
        (legacy_cases / "old.md").write_text("# old", encoding="utf-8")
        # User already has a default namespace dir
        (base / "default").mkdir(parents=True)

        MemoryStore(memory_dir=base, namespace="default")

        # Legacy dir is NOT touched
        assert (base / "cases" / "old.md").exists()

    def test_migration_skipped_for_non_default_namespace(self, tmp_path):
        base = tmp_path / "mem"
        legacy_cases = base / "cases"
        legacy_cases.mkdir(parents=True)
        (legacy_cases / "old.md").write_text("# old", encoding="utf-8")

        MemoryStore(memory_dir=base, namespace="team-a")

        # Legacy dir untouched; team-a created beside it
        assert (base / "cases" / "old.md").exists()
        assert (base / "team-a" / "cases").exists()


class TestBackwardsCompat:
    def test_no_namespace_arg_defaults_to_default(self, tmp_path):
        base = tmp_path / "mem"
        store = MemoryStore(memory_dir=base)
        assert store.namespace == "default"
        # Effective layout
        assert store.memory_dir == base / "default"
        assert store.cases_dir == base / "default" / "cases"
        store.close()
