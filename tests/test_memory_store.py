"""Tests for the memory store — the core of the experiential memory."""

import tempfile
from pathlib import Path

import pytest

from debug_mind.schemas import BugCase, Severity, BugStatus
from debug_mind.memory.store import MemoryStore, _case_to_markdown, _markdown_to_case


@pytest.fixture
def store(tmp_path):
    return MemoryStore(memory_dir=tmp_path / "test_memory")


@pytest.fixture
def sample_case():
    return BugCase(
        id="test001",
        title="NPE in UserService.login",
        symptoms="Login returns 500, NullPointerException at line 42",
        error_log="java.lang.NullPointerException at UserService.java:42",
        root_cause="Redis connection pool exhausted, getLoginToken() returns null",
        fix_suggestion="Increase pool size + add null check",
        severity=Severity.HIGH,
        status=BugStatus.FIXED,
        tags=["npe", "redis", "spring-boot"],
        environment={"language": "Java", "framework": "Spring Boot 3.2"},
        diagnosis_steps=["Check NPE stack trace", "Trace Redis call", "Check pool config"],
    )


class TestMemoryStore:
    def test_save_and_retrieve(self, store, sample_case):
        store.save(sample_case)
        retrieved = store.get("test001")
        assert retrieved is not None
        assert retrieved.title == "NPE in UserService.login"
        assert retrieved.root_cause == "Redis connection pool exhausted, getLoginToken() returns null"
        assert retrieved.severity == Severity.HIGH

    def test_save_creates_markdown_file(self, store, sample_case):
        store.save(sample_case)
        md_path = store.cases_dir / "test001.md"
        assert md_path.exists()
        content = md_path.read_text(encoding="utf-8")
        assert "NPE in UserService.login" in content
        assert "spring-boot" in content

    def test_search_finds_similar(self, store, sample_case):
        store.save(sample_case)
        results = store.search(query="NullPointerException in login endpoint with Redis", top_k=5)
        assert len(results) >= 1
        assert results[0].case.id == "test001"
        assert results[0].score > 0.3

    def test_search_empty_store(self, store):
        results = store.search(query="anything")
        assert results == []

    def test_list_recent(self, store, sample_case):
        store.save(sample_case)
        cases = store.list_recent(limit=10)
        assert len(cases) == 1
        assert cases[0].id == "test001"

    def test_stats(self, store, sample_case):
        store.save(sample_case)
        stats = store.stats()
        assert stats.total_cases == 1
        assert stats.by_severity["high"] == 1
        assert ("npe", 1) in stats.top_tags or ("redis", 1) in stats.top_tags

    def test_rebuild_index(self, store, sample_case):
        store.save(sample_case)
        count = store.rebuild_index()
        assert count == 1
        # Should still be searchable after rebuild
        results = store.search(query="NPE Redis", top_k=5)
        assert len(results) >= 1


class TestMarkdownRoundtrip:
    def test_roundtrip(self, sample_case):
        md = _case_to_markdown(sample_case)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(md)
            path = Path(f.name)

        parsed = _markdown_to_case(path)
        assert parsed is not None
        assert parsed.id == sample_case.id
        assert parsed.title == sample_case.title
        assert parsed.severity == sample_case.severity
        assert parsed.tags == sample_case.tags
        assert parsed.root_cause == sample_case.root_cause
        assert len(parsed.diagnosis_steps) == len(sample_case.diagnosis_steps)

    def test_parse_example_file(self):
        example = Path(__file__).parent.parent / "memory" / "examples" / "example-redis-npe.md"
        if not example.exists():
            pytest.skip("Example file not found")
        case = _markdown_to_case(example)
        assert case is not None
        assert "NPE" in case.title or "UserService" in case.title
        assert case.severity == Severity.HIGH
