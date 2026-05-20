"""Tests for token/cost budget — P2-2."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch


from debug_mind.budget import TokenBudget


class TestTokenBudget:
    def test_budget_not_exceeded_initially(self):
        b = TokenBudget(max_cost_usd=0.50, max_input_tokens=200_000)
        exceeded, reason = b.is_exceeded()
        assert not exceeded
        assert reason is None

    def test_budget_exceeded_on_cost(self):
        b = TokenBudget(max_cost_usd=0.001)  # very small budget
        # 1000 input tokens at $3/1M = $0.003
        usage = SimpleNamespace(input_tokens=1000, output_tokens=0,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0)
        b.record(usage)
        exceeded, reason = b.is_exceeded()
        assert exceeded
        assert "cost" in reason.lower() or "Cost" in reason

    def test_budget_exceeded_on_tokens(self):
        b = TokenBudget(max_input_tokens=100, max_output_tokens=50)
        usage = SimpleNamespace(input_tokens=200, output_tokens=0,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0)
        b.record(usage)
        exceeded, reason = b.is_exceeded()
        assert exceeded
        assert "token" in reason.lower()

    def test_remaining_tokens(self):
        b = TokenBudget(max_input_tokens=1000, max_output_tokens=500)
        usage = SimpleNamespace(input_tokens=300, output_tokens=100,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0)
        b.record(usage)
        rem_in, rem_out = b.remaining_tokens()
        assert rem_in == 700
        assert rem_out == 400

    def test_remaining_cost(self):
        b = TokenBudget(max_cost_usd=1.0)
        usage = SimpleNamespace(input_tokens=100_000, output_tokens=0,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0)
        b.record(usage)
        # 100k tokens at $3/1M = $0.30
        rem = b.remaining_cost()
        assert rem is not None
        assert abs(rem - 0.70) < 0.01

    def test_unlimited_budget(self):
        b = TokenBudget(max_input_tokens=None, max_output_tokens=None, max_cost_usd=None)
        usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0)
        b.record(usage)
        exceeded, reason = b.is_exceeded()
        assert not exceeded

    def test_pricing_from_env(self, monkeypatch):
        monkeypatch.setenv("DEBUG_MIND_PRICING_JSON", '{"test-model": {"input": 0.0, "output": 0.0}}')
        b = TokenBudget(max_cost_usd=0.001, max_input_tokens=None, max_output_tokens=None, model="test-model")
        # With 0 pricing, no cost accumulates
        usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0)
        b.record(usage)
        exceeded, _ = b.is_exceeded()
        assert not exceeded

    def test_accumulated_cost_calculation(self):
        b = TokenBudget()
        usage = SimpleNamespace(input_tokens=100_000, output_tokens=10_000,
                                cache_read_input_tokens=50_000, cache_creation_input_tokens=20_000)
        b.record(usage)
        cost = b.accumulated_cost()
        # 100k*3 + 10k*15 + 50k*0.30 + 20k*3.75 = 300+150+15+75 = 540 / 1M = $0.54
        assert abs(cost - 0.54) < 0.001


class TestBudgetIntegration:
    """Test budget integration with DiagnosticAgent."""

    def test_budget_exceeded_breaks_loop_with_partial_diagnosis(self, tmp_path):
        """When budget is exceeded, the agent breaks and saves a partial diagnosis."""
        from debug_mind.memory.store import MemoryStore
        from debug_mind.agent import DiagnosticAgent

        store = MemoryStore(memory_dir=tmp_path / "mem")
        budget = TokenBudget(max_input_tokens=100, max_cost_usd=10.0)

        # Mock Anthropic client to simulate budget exhaustion
        mock_usage = SimpleNamespace(input_tokens=200, output_tokens=10,
                                     cache_read_input_tokens=0, cache_creation_input_tokens=0)
        mock_content = [
            SimpleNamespace(type="text", text="I found the issue."),
            SimpleNamespace(type="tool_use", id="tu1", name="search_memory",
                            input={"query": "test"}),
        ]
        # Second response: no tool_use (agent done)
        mock_content_2 = [SimpleNamespace(type="text", text="Final answer")]

        response_1 = SimpleNamespace(content=mock_content, usage=mock_usage, stop_reason="tool_use")
        response_2 = SimpleNamespace(content=mock_content_2, usage=mock_usage, stop_reason="end_turn")

        with patch.object(store, "search", return_value=[]):
            agent = DiagnosticAgent(memory=store, budget=budget, api_key="fake-key")

            with patch.object(agent.client.messages, "create", side_effect=[response_1, response_2]):
                with patch.object(agent, "_execute_tool", return_value=({"found": 0}, "search")):
                    result = agent.diagnose("test bug")

        # Budget should have been exceeded after first call
        assert result.reasoning is not None
        assert result.confidence == 0.0

    def test_no_budget_means_no_limit(self, tmp_path):
        """Without a budget, the agent runs normally."""
        from debug_mind.memory.store import MemoryStore
        from debug_mind.agent import DiagnosticAgent

        store = MemoryStore(memory_dir=tmp_path / "mem")
        agent = DiagnosticAgent(memory=store, budget=None, api_key="fake-key")
        assert agent.budget is None

    def test_zero_cost_exits_immediately(self, tmp_path):
        """--max-cost 0 should cause immediate exit with budget exceeded."""
        budget = TokenBudget(max_cost_usd=0.0, max_input_tokens=1_000_000)
        usage = SimpleNamespace(input_tokens=1, output_tokens=0,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0)
        budget.record(usage)
        exceeded, reason = budget.is_exceeded()
        assert exceeded
