"""Provider-specific API key resolution shared by CLI and Web UI."""

from __future__ import annotations

import os

PROVIDER_KEY_ENVS = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "glm": ("ZHIPU_API_KEY", "GLM_API_KEY"),
    "zhipu": ("ZHIPU_API_KEY", "GLM_API_KEY"),
}

KNOWN_PROVIDERS = set(PROVIDER_KEY_ENVS)


def resolve_provider_api_key(explicit_key: str | None = None) -> tuple[str | None, str, str]:
    """Resolve the API key for DEBUG_MIND_PROVIDER.

    Returns (key_or_none, expected_env_var, provider). An explicit key from UI
    input wins over environment variables, but the provider-specific env name is
    still returned for user-facing setup hints.
    """
    provider = os.environ.get("DEBUG_MIND_PROVIDER", "anthropic").lower()
    env_names = PROVIDER_KEY_ENVS.get(provider, PROVIDER_KEY_ENVS["anthropic"])

    if explicit_key and explicit_key.strip():
        return explicit_key.strip(), env_names[0], provider

    for name in env_names:
        key = os.environ.get(name)
        if key:
            return key, name, provider
    return None, env_names[0], provider
