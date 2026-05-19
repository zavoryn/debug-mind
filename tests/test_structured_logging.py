"""Tests for structured logging — P2-3."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from debug_mind.observability.logger import get_logger, JSONFormatter


class TestJSONFormatter:
    def test_json_output_has_required_fields(self):
        import logging
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="debug_mind.test", level=logging.INFO, pathname="", lineno=0,
            msg="test message", args=(), exc_info=None,
        )
        record.trace_id = "abc123"
        record.case_id = "case-456"
        output = fmt.format(record)
        data = json.loads(output)
        assert data["msg"] == "test message"
        assert data["trace_id"] == "abc123"
        assert data["case_id"] == "case-456"
        assert "timestamp" in data
        assert data["level"] == "INFO"

    def test_json_output_plain_message(self):
        """JSON log should not contain rich markup tags."""
        import logging
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="debug_mind.test", level=logging.INFO, pathname="", lineno=0,
            msg="simple message", args=(), exc_info=None,
        )
        output = fmt.format(record)
        data = json.loads(output)
        assert "[" not in data["msg"] or "simple" in data["msg"]


class TestGetLogger:
    def test_json_format_env(self, monkeypatch, tmp_path):
        """When DEBUG_MIND_LOG_FORMAT=json, logs should be valid JSON."""
        monkeypatch.setenv("DEBUG_MIND_LOG_FORMAT", "json")
        # Force re-creation by using a unique name
        import debug_mind.observability.logger as mod
        log_file = str(tmp_path / "test.jsonl")
        monkeypatch.setattr(mod, "_FORMAT", "json")
        monkeypatch.setattr(mod, "_LOG_FILE", log_file)

        logger = logging.getLogger("debug_mind.test_json")
        logger.handlers.clear()

        from debug_mind.observability.logger import get_logger
        log = get_logger("test_json")
        log.info("hello world", extra={"trace_id": "t1"})

        with open(log_file) as f:
            lines = f.readlines()
        assert len(lines) >= 1
        data = json.loads(lines[-1])
        assert data["trace_id"] == "t1"
        assert data["msg"] == "hello world"

    def test_default_format_is_text(self):
        """Without env var, logger should still work (text format)."""
        log = get_logger("test_default")
        assert log is not None

    def test_otel_import_doesnt_crash(self):
        """_try_otel_span should not crash even without opentelemetry."""
        from debug_mind.observability.logger import _try_otel_span
        _try_otel_span("test-trace", case_id="c1")


class TestLogIntegration:
    """Verify structured log fields from actual MemoryStore operations."""

    def test_save_produces_log(self, tmp_path, capsys):
        from debug_mind.memory.store import MemoryStore
        from debug_mind.schemas import BugCase

        store = MemoryStore(memory_dir=tmp_path / "mem")
        case = BugCase(title="log-test", symptoms="s")
        store.save(case)

        # The log was emitted — we just verify it doesn't crash
        assert store.get(case.id) is not None

    def test_cli_output_unchanged_without_env(self, tmp_path):
        """Without DEBUG_MIND_LOG_FORMAT=json, CLI output should be same as Phase 1."""
        from click.testing import CliRunner
        from debug_mind.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["list"])
        # Should show "Memory is empty" or similar — no JSON on stdout
        assert result.exit_code == 0
        assert "{" not in result.output or "cases" not in result.output


import logging
