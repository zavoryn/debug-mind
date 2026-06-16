"""Tests for CLI commands — uses Click's CliRunner, no API calls."""

import pytest
from click.testing import CliRunner

from debug_mind.cli import main
from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import BugCase, DiagnosisResult, Severity


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def memory_dir(tmp_path, monkeypatch):
    d = tmp_path / "test_memory"
    monkeypatch.setenv("DEBUG_MIND_MEMORY_DIR", str(d))
    return d


class TestMainGroup:
    def test_help(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "DebugMind" in result.output

    def test_version(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output


class TestSearchCommand:
    def test_search_empty_memory(self, runner, memory_dir):
        MemoryStore(memory_dir=memory_dir)
        result = runner.invoke(main, ["search", "anything"])
        assert result.exit_code == 0
        assert "No similar bugs found" in result.output

    def test_search_with_results(self, runner, memory_dir):
        store = MemoryStore(memory_dir=memory_dir)
        case = BugCase(
            title="Redis connection timeout",
            symptoms="Service unavailable",
            root_cause="Pool exhausted",
            fix_suggestion="Increase pool",
            tags=["redis"],
        )
        store.save(case)

        result = runner.invoke(main, ["search", "redis timeout"])
        assert result.exit_code == 0
        assert "Redis connection timeout" in result.output


class TestListCommand:
    def test_list_empty(self, runner, memory_dir):
        MemoryStore(memory_dir=memory_dir)
        result = runner.invoke(main, ["list"])
        assert result.exit_code == 0
        assert "Memory is empty" in result.output

    def test_list_with_cases(self, runner, memory_dir):
        store = MemoryStore(memory_dir=memory_dir)
        store.save(BugCase(title="Bug A", symptoms="crash", tags=["test"]))
        store.save(BugCase(title="Bug B", symptoms="error", tags=["test"]))

        result = runner.invoke(main, ["list"])
        assert result.exit_code == 0
        assert "Bug A" in result.output
        assert "Bug B" in result.output


class TestStatsCommand:
    def test_stats_empty(self, runner, memory_dir):
        MemoryStore(memory_dir=memory_dir)
        result = runner.invoke(main, ["stats"])
        assert result.exit_code == 0
        assert "Total Cases: 0" in result.output

    def test_stats_with_data(self, runner, memory_dir):
        store = MemoryStore(memory_dir=memory_dir)
        store.save(
            BugCase(
                title="Critical Bug",
                symptoms="down",
                severity=Severity.CRITICAL,
                tags=["prod"],
            )
        )
        store.save(
            BugCase(
                title="Low Bug",
                symptoms="cosmetic",
                severity=Severity.LOW,
                tags=["ui"],
            )
        )

        result = runner.invoke(main, ["stats"])
        assert result.exit_code == 0
        assert "Total Cases: 2" in result.output


class TestRebuildCommand:
    def test_rebuild_empty(self, runner, memory_dir):
        MemoryStore(memory_dir=memory_dir)
        result = runner.invoke(main, ["rebuild"])
        assert result.exit_code == 0
        assert "Rebuilt index" in result.output

    def test_rebuild_with_cases(self, runner, memory_dir):
        store = MemoryStore(memory_dir=memory_dir)
        store.save(BugCase(title="Test", symptoms="x", tags=["t"]))

        # CLI creates its own MemoryStore, so it picks up the same dir
        result = runner.invoke(main, ["rebuild"])
        assert result.exit_code == 0
        assert "Rebuilt index with 1 cases" in result.output


class TestDiagnoseCommand:
    def test_diagnose_no_api_key(self, runner, memory_dir, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = runner.invoke(main, ["diagnose", "some bug"])
        assert result.exit_code != 0
        assert "ANTHROPIC_API_KEY" in result.output or "Error" in result.output

    def test_diagnose_uses_provider_specific_api_key(self, runner, memory_dir, monkeypatch):
        monkeypatch.setenv("DEBUG_MIND_PROVIDER", "deepseek")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        seen: dict[str, str | None] = {}

        class FakeAgent:
            def __init__(self, memory, project_path=None, api_key=None, **_kwargs):
                seen["api_key"] = api_key

            def diagnose(self, bug_description, error_log="", environment=None):
                return DiagnosisResult(
                    case_id="fake-case",
                    root_cause="provider-specific key was accepted",
                    confidence=1.0,
                    diagnosis_steps=[],
                    fix_suggestion="continue diagnosis",
                )

        monkeypatch.setattr("debug_mind.agent.DiagnosticAgent", FakeAgent)

        result = runner.invoke(main, ["diagnose", "--no-stream", "some bug"])

        assert result.exit_code == 0
        assert seen["api_key"] == "deepseek-key"
        assert "provider-specific key was accepted" in result.output

    def test_diagnose_missing_provider_specific_api_key(self, runner, memory_dir, monkeypatch):
        monkeypatch.setenv("DEBUG_MIND_PROVIDER", "deepseek")
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        result = runner.invoke(main, ["diagnose", "some bug"])

        assert result.exit_code != 0
        assert "DEEPSEEK_API_KEY" in result.output
        assert "ANTHROPIC_API_KEY" not in result.output

    def test_deepseek_provider_env_is_recognised(self, runner, memory_dir, monkeypatch):
        monkeypatch.setenv("DEBUG_MIND_PROVIDER", "deepseek")

        result = runner.invoke(main, ["search", "anything"])

        assert result.exit_code == 0
        assert "DEBUG_MIND_PROVIDER='deepseek' is not recognised" not in result.output

    def test_diagnose_invalid_project(self, runner, memory_dir):
        result = runner.invoke(
            main,
            [
                "diagnose",
                "--project",
                "/nonexistent/path",
                "bug",
            ],
        )
        assert result.exit_code != 0


class TestShowCommand:
    def test_show_not_found(self, runner, memory_dir):
        MemoryStore(memory_dir=memory_dir)
        result = runner.invoke(main, ["show", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_show_existing_case(self, runner, memory_dir):
        store = MemoryStore(memory_dir=memory_dir)
        case = BugCase(
            title="Test Case Show",
            symptoms="crash",
            root_cause="null",
            fix_suggestion="fix",
            tags=["show-test"],
        )
        store.save(case)

        result = runner.invoke(main, ["show", case.id])
        assert result.exit_code == 0
        assert "Test Case Show" in result.output
        assert "show-test" in result.output


class TestDeleteCommand:
    def test_delete_not_found(self, runner, memory_dir):
        MemoryStore(memory_dir=memory_dir)
        result = runner.invoke(main, ["delete", "nonexistent", "--yes"])
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_delete_existing_case(self, runner, memory_dir):
        store = MemoryStore(memory_dir=memory_dir)
        case = BugCase(title="To Delete", symptoms="x", tags=["del"])
        store.save(case)
        case_id = case.id

        result = runner.invoke(main, ["delete", case_id, "--yes"])
        assert result.exit_code == 0
        assert "deleted" in result.output

        # Verify it's gone
        assert store.get(case_id) is None
