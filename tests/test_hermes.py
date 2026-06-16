"""Tests for Hermes tool governance."""

from __future__ import annotations

import json

from debug_mind.hermes import HermesGovernance, RiskLevel


def test_strict_schema_validation_rejects_enum_and_bad_array_items(tmp_path):
    hermes = HermesGovernance(
        tool_schemas={
            "save_to_memory": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "environment": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["title"],
            }
        },
        trace_path=tmp_path / "trace.jsonl",
    )

    bad_enum = hermes.inspect_tool_call(
        "save_to_memory",
        {"title": "case", "severity": "urgent"},
        turn=1,
    )
    assert bad_enum.allowed is False
    assert bad_enum.reason == "validation_error"
    assert "severity" in bad_enum.message
    assert "one of" in bad_enum.message

    bad_array = hermes.inspect_tool_call(
        "save_to_memory",
        {"title": "case", "severity": "medium", "tags": ["redis", 123]},
        turn=1,
    )
    assert bad_array.allowed is False
    assert "tags[1]" in bad_array.message


def test_duplicate_tool_call_is_blocked_with_stable_fingerprint(tmp_path):
    hermes = HermesGovernance(
        tool_schemas={
            "search_memory": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
                "required": ["query"],
            }
        },
        trace_path=tmp_path / "trace.jsonl",
    )

    first = hermes.inspect_tool_call("search_memory", {"query": "redis", "top_k": 3}, turn=1)
    second = hermes.inspect_tool_call("search_memory", {"top_k": 3, "query": "redis"}, turn=2)
    third = hermes.inspect_tool_call(
        "search_memory", {"query": "redis timeout", "top_k": 3}, turn=3
    )

    assert first.allowed is True
    assert first.risk_level == RiskLevel.READ
    assert second.allowed is False
    assert second.reason == "duplicate_tool_call"
    assert second.duplicate_of == 1
    assert third.allowed is True


def test_dangerous_tool_trace_omits_raw_patch_diff(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    hermes = HermesGovernance(tool_schemas={}, trace_path=trace_path, trace_id="trace-1")
    patch_diff = "--- a/app.py\n+++ b/app.py\n@@\n-print('old')\n+print('new')\n"

    decision = hermes.inspect_tool_call(
        "apply_and_test",
        {
            "file_path": "app.py",
            "patch_diff": patch_diff,
            "test_command": "pytest tests/test_app.py",
        },
        turn=1,
    )
    hermes.record_tool_result(
        decision,
        result={"passed": False, "output": "FAILED tests/test_app.py"},
        side_effect="patch_test",
        latency_ms=42,
    )

    entries = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["trace_id"] == "trace-1"
    assert entry["tool"] == "apply_and_test"
    assert entry["risk_level"] == RiskLevel.DANGEROUS
    assert entry["decision"] == "allowed"
    assert entry["latency_ms"] == 42
    assert entry["side_effect"] == "patch_test"
    assert entry["input_summary"]["patch_diff"]["chars"] == len(patch_diff)
    assert "print('old')" not in json.dumps(entry, ensure_ascii=False)


def test_blocked_result_is_structured_for_llm_self_correction(tmp_path):
    hermes = HermesGovernance(
        tool_schemas={
            "search_memory": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            }
        },
        trace_path=tmp_path / "trace.jsonl",
    )

    decision = hermes.inspect_tool_call("search_memory", {}, turn=1)
    result = hermes.blocked_result(decision)

    assert result["blocked"] is True
    assert result["reason"] == "validation_error"
    assert "query" in result["error"]
    assert "hint" in result
