"""Tests for input sanitization — P2-6."""

from __future__ import annotations

import os

import pytest

from debug_mind.sanitize import (
    sanitize_description,
    sanitize_error_log,
    sanitize_environment,
    sanitize_tags,
    sanitize_bug_input,
)


class TestSanitizeDescription:
    def test_short_passes_through(self):
        assert sanitize_description("short") == "short"

    def test_long_truncated(self):
        text = "x" * 10000
        result = sanitize_description(text)
        assert len(result) <= 4200  # 4096 + truncation marker
        assert result.endswith("[truncated]")

    def test_control_chars_stripped(self):
        assert sanitize_description("hello\x00\x01\x1fworld") == "helloworld"

    def test_newline_preserved(self):
        assert sanitize_description("line1\nline2\ttab") == "line1\nline2\ttab"


class TestSanitizeErrorLog:
    def test_short_passes_through(self):
        assert sanitize_error_log("error") == "error"

    def test_100kb_truncated_to_16kb(self):
        log = "A" * 100_000
        result = sanitize_error_log(log)
        assert len(result) <= 16500  # 16384 + truncation markers
        # Should contain original tail
        assert result.endswith("A" * 8192)

    def test_head_and_tail_preserved(self):
        log = "H" * 9000 + "M" * 2000 + "T" * 9000
        result = sanitize_error_log(log)
        assert result.startswith("H")
        assert result.endswith("T" * 8192)

    def test_truncation_marker_present(self):
        log = "x" * 100_000
        result = sanitize_error_log(log)
        assert "[truncated" in result


class TestSanitizeEnvironment:
    def test_normal_passes_through(self):
        env = {"java": "17", "os": "linux"}
        assert sanitize_environment(env) == env

    def test_values_truncated(self):
        env = {"key": "v" * 1000}
        result = sanitize_environment(env)
        assert len(result["key"]) == 256

    def test_too_many_keys_truncated(self):
        env = {f"key{i}": f"val{i}" for i in range(50)}
        result = sanitize_environment(env)
        assert len(result) == 20


class TestSanitizeTags:
    def test_short_list_passes(self):
        tags = ["npe", "redis", "spring"]
        assert sanitize_tags(tags) == tags

    def test_30_tags_truncated_to_20(self):
        tags = [f"tag{i}" for i in range(30)]
        result = sanitize_tags(tags)
        assert len(result) == 20
        assert result[0] == "tag0"


class TestSanitizeBugInput:
    def test_combined_sanitization(self):
        desc, log, env, tags = sanitize_bug_input(
            description="d" * 10000,
            error_log="e" * 100_000,
            environment={f"k{i}": "v" * 500 for i in range(30)},
            tags=[f"t{i}" for i in range(30)],
        )
        assert len(desc) <= 4200
        assert len(log) <= 17000
        assert len(env) == 20
        assert len(tags) == 20

    def test_ctrl_chars_in_log(self):
        _, log, _, _ = sanitize_bug_input(error_log="hello\x00\x01\x1bworld")
        assert "\x00" not in log
        assert "\x01" not in log
        assert "hello" in log
        assert "world" in log
