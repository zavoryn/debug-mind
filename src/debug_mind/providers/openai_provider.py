"""OpenAI provider — alternative backend via OPENAI_API_KEY."""

from __future__ import annotations

import json
import os
from typing import Any

from debug_mind.providers.base import (
    LLMProvider,
    LLMResponse,
    LLMUsage,
    LLMContentBlock,
    anthropic_tools_to_openai,
)


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str | None = None):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "openai package is required for OpenAI provider. Install with: pip install openai"
            )
        self.client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))

    @property
    def default_model(self) -> str:
        return "gpt-4o"

    def create_message(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: list[dict[str, Any]],
        max_tokens: int,
        **kwargs: Any,
    ) -> LLMResponse:
        # Build OpenAI-format messages
        openai_msgs: list[dict[str, Any]] = []
        for block in system:
            if block.get("type") == "text":
                openai_msgs.append({"role": "system", "content": block["text"]})

        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")
            if isinstance(content, str):
                openai_msgs.append({"role": role, "content": content})
            elif isinstance(content, list):
                # Multi-part content — convert tool results
                text_parts = []
                tool_calls = []
                for part in content:
                    if part.get("type") == "text":
                        text_parts.append(part["text"])
                    elif part.get("type") == "tool_result":
                        # OpenAI needs the tool_call_id
                        tool_calls.append(
                            {
                                "role": "tool",
                                "tool_call_id": part.get("tool_use_id", ""),
                                "content": part.get("content", ""),
                            }
                        )
                if text_parts:
                    openai_msgs.append({"role": role, "content": "\n".join(text_parts)})
                if tool_calls:
                    openai_msgs.extend(tool_calls)

        # Convert tools
        openai_tools = anthropic_tools_to_openai(tools) if tools else None

        resp = self.client.chat.completions.create(
            model=model,
            messages=openai_msgs,
            tools=openai_tools,
            max_tokens=max_tokens,
            **kwargs,
        )

        choice = resp.choices[0]
        content: list[LLMContentBlock] = []
        if choice.message.content:
            content.append(LLMContentBlock(type="text", text=choice.message.content))
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                try:
                    tool_input = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_input = {}
                content.append(
                    LLMContentBlock(
                        type="tool_use",
                        id=tc.id,
                        name=tc.function.name,
                        input=tool_input,
                    )
                )

        usage = LLMUsage(
            input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            output_tokens=resp.usage.completion_tokens if resp.usage else 0,
        )
        return LLMResponse(
            content=content,
            usage=usage,
            stop_reason=choice.finish_reason or "end_turn",
        )

    def is_retryable(self, error: Exception) -> bool:
        from openai import RateLimitError, APIConnectionError, InternalServerError

        if isinstance(error, RateLimitError):
            return True
        if isinstance(error, APIConnectionError):
            return True
        if isinstance(error, InternalServerError):
            return True
        return False
