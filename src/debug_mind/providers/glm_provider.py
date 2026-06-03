"""GLM (Zhipu AI) provider — OpenAI-compatible API via ZHIPU_API_KEY.

Usage:
    export DEBUG_MIND_PROVIDER=glm
    export ZHIPU_API_KEY=...
    debug-mind diagnose "..."

Zhipu's API (open.bigmodel.cn) is OpenAI-compatible. GLM-4 series supports
function calling / tool use, which is required for the ReAct loop.

Recommended models:
    glm-4-flash   — fastest, cheapest, good for agentic loops
    glm-4-air     — balanced speed/quality
    glm-4         — highest quality, slower
"""

from __future__ import annotations

import os

from debug_mind.providers.openai_provider import OpenAIProvider


class GLMProvider(OpenAIProvider):
    """Zhipu GLM backend — drop-in replacement for OpenAI provider."""

    _BASE_URL = "https://open.bigmodel.cn/api/paas/v4"

    def __init__(self, api_key: str | None = None):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "openai package is required for GLM provider. Install with: pip install openai"
            )
        key = api_key or os.environ.get("ZHIPU_API_KEY") or os.environ.get("GLM_API_KEY")
        if not key:
            raise ValueError(
                "Zhipu API key not found. "
                "Set ZHIPU_API_KEY (or GLM_API_KEY) environment variable or pass api_key."
            )
        self.client = OpenAI(api_key=key, base_url=self._BASE_URL)

    @property
    def default_model(self) -> str:
        return "glm-4-flash"

    def is_retryable(self, error: Exception) -> bool:
        try:
            from openai import RateLimitError, APIConnectionError, InternalServerError

            return isinstance(error, (RateLimitError, APIConnectionError, InternalServerError))
        except ImportError:
            return False

    # create_message is inherited from OpenAIProvider.
    # GLM-4's function-calling response format matches OpenAI's.
