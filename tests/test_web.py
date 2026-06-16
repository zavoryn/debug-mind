"""Tests for the Gradio web entrypoint helpers."""

from __future__ import annotations

import sys
import types

from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import DiagnosisResult
from debug_mind import web


def test_web_resolves_provider_specific_env_key(monkeypatch):
    monkeypatch.setenv("DEBUG_MIND_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    key, key_env, provider = web._resolve_provider_api_key("")

    assert key == "deepseek-key"
    assert key_env == "DEEPSEEK_API_KEY"
    assert provider == "deepseek"


def test_web_diagnose_stream_passes_provider_key_to_agent(tmp_path, monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "gradio",
        types.SimpleNamespace(update=lambda **kwargs: {"update": kwargs}),
    )
    monkeypatch.setenv("DEBUG_MIND_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    seen: dict[str, str | None] = {}

    class FakeAgent:
        def __init__(self, memory, api_key=None):
            seen["api_key"] = api_key

        def diagnose_stream(self, bug_description, error_log="", environment=None):
            yield (
                "done",
                DiagnosisResult(
                    case_id="web-case",
                    root_cause="provider key accepted",
                    confidence=1.0,
                    diagnosis_steps=[],
                    fix_suggestion="continue",
                ),
            )

    monkeypatch.setattr("debug_mind.agent.DiagnosticAgent", FakeAgent)

    memory = MemoryStore(memory_dir=tmp_path / "mem")
    events = list(web._do_diagnose_stream("bug", "", "", "", memory))

    assert seen["api_key"] == "deepseek-key"
    assert events[-1][1] == "web-case"
