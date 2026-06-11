"""Token and cost budget for the diagnostic agent — prevents runaway LLM spending.

Tracks cumulative token usage and estimated cost across all turns. The agent
loop checks is_exceeded() before each LLM call; when exceeded it breaks
gracefully and saves a partial diagnosis.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

# Default pricing: USD per 1M tokens
_DEFAULT_PRICING = {
    "claude-sonnet-4-20250514": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
    "claude-sonnet-4-6": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
    # DeepSeek official pricing (2026-04, api-docs.deepseek.com)
    "deepseek-v4-flash": {
        "input": 0.14,
        "output": 0.28,
        "cache_read": 0.0028,
        "cache_write": 0.0,
    },
}


def _load_pricing() -> dict[str, dict[str, float]]:
    """Load pricing table, allowing env override via DEBUG_MIND_PRICING_JSON."""
    pricing = dict(_DEFAULT_PRICING)
    env_json = os.environ.get("DEBUG_MIND_PRICING_JSON")
    if env_json:
        try:
            override = json.loads(env_json)
            pricing.update(override)
        except (json.JSONDecodeError, TypeError):
            pass
    return pricing


@dataclass
class TokenBudget:
    """Tracks token usage and cost against configurable limits.

    Attributes:
        max_input_tokens: Cumulative input token limit (None = unlimited).
        max_output_tokens: Cumulative output token limit (None = unlimited).
        max_cost_usd: Cumulative cost limit in USD (None = unlimited).
        model: Model identifier used for cost estimation.
    """

    max_input_tokens: int | None = 200_000
    max_output_tokens: int | None = 20_000
    max_cost_usd: float | None = 0.50
    model: str = "claude-sonnet-4-6"

    _total_input: int = field(default=0, repr=False)
    _total_output: int = field(default=0, repr=False)
    _total_cache_read: int = field(default=0, repr=False)
    _total_cache_write: int = field(default=0, repr=False)
    _pricing: dict = field(default_factory=_load_pricing, repr=False, init=False)

    def record(self, usage) -> None:
        """Record token usage from an Anthropic response.usage object.

        The usage object should have: input_tokens, output_tokens,
        cache_read_input_tokens, cache_creation_input_tokens.
        """
        self._total_input += getattr(usage, "input_tokens", 0)
        self._total_output += getattr(usage, "output_tokens", 0)
        self._total_cache_read += getattr(usage, "cache_read_input_tokens", 0)
        self._total_cache_write += getattr(usage, "cache_creation_input_tokens", 0)

    def remaining_tokens(self) -> tuple[int | None, int | None]:
        """Return (remaining_input, remaining_output). None means unlimited."""
        rem_in = (self.max_input_tokens - self._total_input) if self.max_input_tokens else None
        rem_out = (self.max_output_tokens - self._total_output) if self.max_output_tokens else None
        return rem_in, rem_out

    def remaining_cost(self) -> float | None:
        """Return remaining cost in USD. None means unlimited."""
        if self.max_cost_usd is None:
            return None
        return self.max_cost_usd - self.accumulated_cost()

    def accumulated_cost(self) -> float:
        """Return total accumulated cost in USD."""
        rates = self._pricing.get(self.model, self._pricing.get("claude-sonnet-4-6", {}))
        cost = 0.0
        cost += self._total_input * rates.get("input", 3.0) / 1_000_000
        cost += self._total_output * rates.get("output", 15.0) / 1_000_000
        cost += self._total_cache_read * rates.get("cache_read", 0.30) / 1_000_000
        cost += self._total_cache_write * rates.get("cache_write", 3.75) / 1_000_000
        return cost

    def is_exceeded(self) -> tuple[bool, str | None]:
        """Check if any budget limit has been exceeded.

        Returns (exceeded, reason). If exceeded is True, reason describes why.
        """
        if self.max_input_tokens and self._total_input >= self.max_input_tokens:
            return True, f"Input token budget exceeded: {self._total_input}/{self.max_input_tokens}"
        if self.max_output_tokens and self._total_output >= self.max_output_tokens:
            return (
                True,
                f"Output token budget exceeded: {self._total_output}/{self.max_output_tokens}",
            )
        if self.max_cost_usd is not None and self.accumulated_cost() >= self.max_cost_usd:
            return (
                True,
                f"Cost budget exceeded: ${self.accumulated_cost():.4f}/${self.max_cost_usd:.2f}",
            )
        return False, None
