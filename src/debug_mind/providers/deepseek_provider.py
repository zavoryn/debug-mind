"""DeepSeek provider — OpenAI-compatible API via DEEPSEEK_API_KEY.

Usage:
    export DEBUG_MIND_PROVIDER=deepseek
    export DEEPSEEK_API_KEY=sk-...
    debug-mind diagnose "..."

DeepSeek's API is fully OpenAI-compatible, so we reuse the OpenAI SDK with
a different base_url. Tool calling is supported on deepseek-chat.
"""

from __future__ import annotations

import os
from typing import Any

from debug_mind.providers.openai_provider import OpenAIProvider


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek backend — drop-in replacement for OpenAI provider."""

    _BASE_URL = "https://api.deepseek.com"

    def __init__(self, api_key: str | None = None):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "openai package is required for DeepSeek provider. Install with: pip install openai"
            )
        key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise ValueError(
                "DeepSeek API key not found. "
                "Set DEEPSEEK_API_KEY environment variable or pass api_key."
            )
        # Bypass OpenAIProvider.__init__ and construct the client directly
        self.client = OpenAI(api_key=key, base_url=self._BASE_URL)

    @property
    def default_model(self) -> str:
        # deepseek-v4-flash: fast, cheap, tool-calling supported — default for agentic loops
        # Override via DEBUG_MIND_MODEL=deepseek-v4-pro for higher quality
        return "deepseek-v4-flash"

    def is_retryable(self, error: Exception) -> bool:
        try:
            from openai import RateLimitError, APIConnectionError, InternalServerError

            return isinstance(error, (RateLimitError, APIConnectionError, InternalServerError))
        except ImportError:
            return False

    # create_message is inherited from OpenAIProvider — no changes needed.
    # DeepSeek's response format is identical to OpenAI's.

    # ---------- provider-specific notes for the agent ----------
    # deepseek-chat  : fast, cheap, supports function calling (tool use)
    # deepseek-reasoner : chain-of-thought, slower, no tool calling
    # Stick with deepseek-chat for agentic loops.


class DeepSeekR1Provider(DeepSeekProvider):
    """DeepSeek-R1 (reasoning model) — for offline analysis, NOT agentic loops.

    deepseek-reasoner does not support tool calling, so it cannot be used
    in the ReAct loop. Use it only for one-shot diagnosis without tools.
    """

    @property
    def default_model(self) -> str:
        return "deepseek-reasoner"

    def create_message(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: list[dict[str, Any]],
        max_tokens: int,
        **kwargs: Any,
    ):
        # Strip tools — reasoner doesn't support function calling
        return super().create_message(
            model=model,
            messages=messages,
            tools=[],  # no tools
            system=system,
            max_tokens=max_tokens,
            **kwargs,
        )
