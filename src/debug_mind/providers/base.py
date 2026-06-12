"""Base protocol for LLM providers."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# HTTP-level guard. The loop-level wall-clock budget only checks *between*
# rounds and cannot preempt a request that hangs at the network layer — a
# 38-minute hang in the 2026-06 ablation run (SDK-internal retries stacking
# on top of tenacity backoff) motivated this. SDK-internal retries are
# disabled at client construction: the agent already retries via tenacity
# using each provider's is_retryable().
HTTP_TIMEOUT_SECONDS = float(os.environ.get("DEBUG_MIND_HTTP_TIMEOUT", "120"))


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class LLMContentBlock:
    type: str  # "text" | "tool_use"
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    content: list[LLMContentBlock]
    usage: LLMUsage
    stop_reason: str = "end_turn"


class LLMProvider(ABC):
    """Protocol for LLM backends."""

    @abstractmethod
    def create_message(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: list[dict[str, Any]],
        max_tokens: int,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a message and return a standardized response."""

    @abstractmethod
    def is_retryable(self, error: Exception) -> bool:
        """Return True if this error should trigger a retry."""

    @property
    @abstractmethod
    def default_model(self) -> str:
        """Return the default model for this provider."""


def anthropic_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Anthropic-format tool schemas to OpenAI function calling format."""
    openai_tools = []
    for tool in tools:
        schema = tool.get("input_schema", {})
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": {
                        "type": "object",
                        "properties": schema.get("properties", {}),
                        "required": schema.get("required", []),
                    },
                },
            }
        )
    return openai_tools
