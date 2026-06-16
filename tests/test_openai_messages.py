"""Tests for the Anthropic→OpenAI message conversion (multi-round tool use).

Regression for the DeepSeek/OpenAI round-2 failure: assistant history blocks
are LLMContentBlock dataclasses (not dicts), and assistant tool_use blocks
must round-trip as `tool_calls` or the follow-up `role:"tool"` message is
rejected by the API.
"""

from __future__ import annotations

import json

from debug_mind.providers.base import LLMContentBlock
from debug_mind.providers.openai_provider import to_openai_messages

SYSTEM = [{"type": "text", "text": "You are DebugMind."}]


def _round2_history() -> list[dict]:
    """History exactly as agent._run_loop builds it after one tool round."""
    return [
        {"role": "user", "content": "Bug: NPE in login"},
        {
            "role": "assistant",
            "content": [
                LLMContentBlock(type="text", text="Let me check memory."),
                LLMContentBlock(
                    type="tool_use",
                    id="call_1",
                    name="search_memory",
                    input={"query": "NPE login", "top_k": 5},
                ),
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": '{"found": 0, "cases": []}',
                }
            ],
        },
    ]


class TestToOpenAIMessages:
    def test_dataclass_blocks_do_not_crash(self):
        msgs = to_openai_messages(_round2_history(), SYSTEM)
        assert msgs[0] == {"role": "system", "content": "You are DebugMind."}

    def test_assistant_tool_use_round_trips_as_tool_calls(self):
        msgs = to_openai_messages(_round2_history(), SYSTEM)
        assistant = next(m for m in msgs if m["role"] == "assistant")
        assert assistant["content"] == "Let me check memory."
        (tc,) = assistant["tool_calls"]
        assert tc["id"] == "call_1"
        assert tc["function"]["name"] == "search_memory"
        assert json.loads(tc["function"]["arguments"]) == {"query": "NPE login", "top_k": 5}

    def test_tool_message_follows_with_matching_id(self):
        msgs = to_openai_messages(_round2_history(), SYSTEM)
        roles = [m["role"] for m in msgs]
        assert roles == ["system", "user", "assistant", "tool"]
        tool_msg = msgs[-1]
        assert tool_msg["tool_call_id"] == "call_1"
        assert tool_msg["content"] == '{"found": 0, "cases": []}'

    def test_assistant_tool_calls_without_text_has_null_content(self):
        history = [
            {
                "role": "assistant",
                "content": [
                    LLMContentBlock(type="tool_use", id="call_2", name="search_memory", input={})
                ],
            }
        ]
        (msg,) = to_openai_messages(history, [])
        assert msg["content"] is None
        assert msg["tool_calls"][0]["id"] == "call_2"

    def test_plain_string_content_passthrough(self):
        msgs = to_openai_messages([{"role": "user", "content": "hi"}], [])
        assert msgs == [{"role": "user", "content": "hi"}]

    def test_non_string_tool_result_is_json_encoded(self):
        history = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_3",
                        "content": {"found": 1},
                    }
                ],
            }
        ]
        (msg,) = to_openai_messages(history, [])
        assert msg["role"] == "tool"
        assert json.loads(msg["content"]) == {"found": 1}
