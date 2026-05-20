"""Tests for API retry and partial diagnosis fallback — P2-4."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import httpx
import anthropic


from debug_mind.memory.store import MemoryStore
from debug_mind.agent import DiagnosticAgent


def _make_response(text="done", tool_uses=None, input_tokens=100, output_tokens=50):
    """Build a mock Anthropic response."""
    content = [SimpleNamespace(type="text", text=text)]
    if tool_uses:
        for tu in tool_uses:
            content.append(
                SimpleNamespace(type="tool_use", id=tu["id"], name=tu["name"], input=tu["input"])
            )
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    return SimpleNamespace(content=content, usage=usage, stop_reason="end_turn")


def _mock_response(status_code: int) -> httpx.Response:
    """Build a mock httpx response with the given status code."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = {}
    return resp


class TestRetryOnTransientErrors:
    def test_retry_rate_limit_then_succeed(self, tmp_path):
        """RateLimitError on first 2 calls, success on 3rd."""
        store = MemoryStore(memory_dir=tmp_path / "mem")
        agent = DiagnosticAgent(memory=store, api_key="fake", no_retry=False)

        rlimit1 = anthropic.RateLimitError(
            message="rate limited", response=_mock_response(429), body=None
        )
        rlimit2 = anthropic.RateLimitError(
            message="rate limited", response=_mock_response(429), body=None
        )
        success = _make_response("Final answer")

        with patch.object(
            agent.provider, "create_message", side_effect=[rlimit1, rlimit2, success]
        ):
            result = agent.diagnose("test bug")

        assert result.root_cause == "Final answer"

    def test_retry_5xx_three_times_then_give_up(self, tmp_path):
        """Three 5xx errors → agent saves UNRESOLVED partial diagnosis."""
        store = MemoryStore(memory_dir=tmp_path / "mem")
        agent = DiagnosticAgent(memory=store, api_key="fake", no_retry=False)

        err = anthropic.APIStatusError(
            message="internal error", response=_mock_response(500), body=None
        )

        with patch.object(agent.provider, "create_message", side_effect=[err, err, err]):
            result = agent.diagnose("test bug")

        assert result.confidence == 0.0

    def test_401_not_retried(self, tmp_path):
        """401 AuthenticationError should not be retried."""
        store = MemoryStore(memory_dir=tmp_path / "mem")
        agent = DiagnosticAgent(memory=store, api_key="fake", no_retry=False)

        err = anthropic.AuthenticationError(
            message="invalid key", response=_mock_response(401), body=None
        )
        mock_create = MagicMock(side_effect=err)
        with patch.object(agent.provider, "create_message", mock_create):
            _ = agent.diagnose("test bug")

        assert mock_create.call_count == 1

    def test_no_retry_flag_skips_retry(self, tmp_path):
        """With --no-retry, even 429 should not be retried."""
        store = MemoryStore(memory_dir=tmp_path / "mem")
        agent = DiagnosticAgent(memory=store, api_key="fake", no_retry=True)

        err = anthropic.RateLimitError(
            message="rate limited", response=_mock_response(429), body=None
        )
        mock_create = MagicMock(side_effect=err)

        with patch.object(agent.provider, "create_message", mock_create):
            _ = agent.diagnose("test bug")

        assert mock_create.call_count == 1

    def test_partial_diagnosis_saved_on_api_failure(self, tmp_path):
        """After API failure, a partial diagnosis should be saved to memory."""
        store = MemoryStore(memory_dir=tmp_path / "mem")
        agent = DiagnosticAgent(memory=store, api_key="fake", no_retry=True)

        search_response = _make_response(
            text="", tool_uses=[{"id": "tu1", "name": "search_memory", "input": {"query": "test"}}]
        )
        err = anthropic.APIStatusError(
            message="server error", response=_mock_response(500), body=None
        )

        with patch.object(agent.provider, "create_message", side_effect=[search_response, err]):
            with patch.object(
                agent, "_execute_tool", return_value=({"found": 0, "cases": []}, "search")
            ):
                result = agent.diagnose("test bug")

        assert result.confidence == 0.0
