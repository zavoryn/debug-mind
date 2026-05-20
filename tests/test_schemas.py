"""Tests for Pydantic schemas — validation, defaults, serialization."""

from datetime import datetime

import pytest

from debug_mind.schemas import (
    BugCase,
    DiagnosisResult,
    SearchResult,
    MemoryStats,
    Severity,
    BugStatus,
)


class TestBugCase:
    def test_defaults(self):
        case = BugCase(title="Test", symptoms="broken")
        assert case.id
        assert len(case.id) == 12
        assert case.severity == Severity.MEDIUM
        assert case.status == BugStatus.REPORTED
        assert case.tags == []
        assert case.environment == {}
        assert case.diagnosis_steps == []
        assert case.similar_case_ids == []
        assert isinstance(case.created_at, datetime)
        assert isinstance(case.updated_at, datetime)

    def test_auto_id_is_unique(self):
        a = BugCase(title="A", symptoms="a")
        b = BugCase(title="B", symptoms="b")
        assert a.id != b.id

    def test_all_severities(self):
        for sev in Severity:
            case = BugCase(title="T", symptoms="s", severity=sev)
            assert case.severity == sev

    def test_all_statuses(self):
        for status in BugStatus:
            case = BugCase(title="T", symptoms="s", status=status)
            assert case.status == status

    def test_to_search_text(self):
        case = BugCase(
            title="NPE in login",
            symptoms="500 error",
            error_log="NullPointerException",
            root_cause="null pointer",
            tags=["npe", "java"],
            environment={"language": "Java"},
        )
        text = case.to_search_text()
        assert "NPE in login" in text
        assert "500 error" in text
        assert "NullPointerException" in text
        assert "npe, java" in text
        assert "language=Java" in text

    def test_to_search_text_empty_fields(self):
        case = BugCase(title="Test", symptoms="s")
        text = case.to_search_text()
        assert "Test" in text
        assert "s" in text
        # Should not include empty trailing colons
        assert text.rstrip().endswith("s")

    def test_full_construction(self):
        case = BugCase(
            id="manual001",
            title="Full Bug",
            symptoms="crash",
            error_log="Stack trace here",
            root_cause="bad code",
            fix_suggestion="fix it",
            severity=Severity.CRITICAL,
            status=BugStatus.FIXED,
            tags=["critical", "prod"],
            environment={"lang": "Python", "version": "3.12"},
            diagnosis_steps=["step1", "step2"],
            similar_case_ids=["case002"],
        )
        assert case.id == "manual001"
        assert case.severity == Severity.CRITICAL
        assert case.status == BugStatus.FIXED
        assert len(case.diagnosis_steps) == 2
        assert case.similar_case_ids == ["case002"]


class TestDiagnosisResult:
    def test_defaults(self):
        result = DiagnosisResult(
            case_id="test",
            root_cause="unknown",
            diagnosis_steps=[],
            fix_suggestion="",
            confidence=0.5,
        )
        assert result.confidence == 0.5
        assert result.similar_cases_found == 0
        assert result.reasoning == ""

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            DiagnosisResult(
                case_id="x",
                root_cause="r",
                diagnosis_steps=[],
                fix_suggestion="",
                confidence=1.5,
            )
        with pytest.raises(Exception):
            DiagnosisResult(
                case_id="x",
                root_cause="r",
                diagnosis_steps=[],
                fix_suggestion="",
                confidence=-0.1,
            )


class TestSearchResult:
    def test_creation(self):
        case = BugCase(title="T", symptoms="s")
        sr = SearchResult(case=case, score=0.85)
        assert sr.score == 0.85
        assert sr.case.title == "T"


class TestMemoryStats:
    def test_creation(self):
        stats = MemoryStats(
            total_cases=5,
            by_severity={"high": 2, "medium": 3},
            by_status={"fixed": 4, "open": 1},
            top_tags=[("npe", 3), ("redis", 2)],
        )
        assert stats.total_cases == 5
        assert len(stats.top_tags) == 2
