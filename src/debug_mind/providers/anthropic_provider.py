"""Anthropic Claude provider — the default backend."""

from __future__ import annotations

import os
from typing import Any

import anthropic

from debug_mind.providers.base import LLMProvider, LLMResponse, LLMUsage, LLMContentBlock


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str | None = None):
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    @property
    def default_model(self) -> str:
        return "claude-sonnet-4-6"

    def create_message(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: list[dict[str, Any]],
        max_tokens: int,
        **kwargs: Any,
    ) -> LLMResponse:
        resp = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
            **kwargs,
        )
        content = []
        for block in resp.content:
            if block.type == "text":
                content.append(LLMContentBlock(type="text", text=block.text))
            elif block.type == "tool_use":
                content.append(
                    LLMContentBlock(
                        type="tool_use",
                        id=block.id,
                        name=block.name,
                        input=block.input,
                    )
                )
        usage = LLMUsage(
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0),
            cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0),
        )
        return LLMResponse(content=content, usage=usage, stop_reason=resp.stop_reason)

    def is_retryable(self, error: Exception) -> bool:
        if isinstance(error, anthropic.RateLimitError):
            return True
        if isinstance(error, anthropic.APIConnectionError):
            return True
        if isinstance(error, anthropic.APIStatusError):
            return error.status_code >= 500
        return False
