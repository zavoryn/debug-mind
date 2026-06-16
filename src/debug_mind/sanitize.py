"""Input sanitization — truncates oversized inputs to prevent prompt injection and token bombs.

Why: User-supplied error_log / bug_description can be 100KB+ of raw text.
Sending that directly into the LLM prompt wastes tokens and opens injection vectors.
This module enforces size limits and strips control characters before data enters the prompt.
"""

from __future__ import annotations

import os
import re

_MAX_DESCRIPTION = int(os.environ.get("DEBUG_MIND_MAX_DESCRIPTION_SIZE", "4096"))
_MAX_LOG = int(os.environ.get("DEBUG_MIND_MAX_LOG_SIZE", "16384"))
_MAX_ENV_VALUE = int(os.environ.get("DEBUG_MIND_MAX_ENV_VALUE_SIZE", "256"))
_MAX_ENV_KEYS = int(os.environ.get("DEBUG_MIND_MAX_ENV_KEYS", "20"))
_MAX_TAGS = int(os.environ.get("DEBUG_MIND_MAX_TAGS", "20"))
_MAX_PATCH_ATTEMPTS = int(os.environ.get("DEBUG_MIND_MAX_PATCH_ATTEMPTS", "5"))
_MAX_PATCH_ATTEMPT_FIELD = int(os.environ.get("DEBUG_MIND_MAX_PATCH_ATTEMPT_FIELD_SIZE", "4096"))

# Strip all ASCII control chars except \n (0x0A), \r (0x0D), \t (0x09)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitize_description(text: str) -> str:
    """Truncate bug description to MAX_DESCRIPTION bytes."""
    text = _CTRL_RE.sub("", text)
    if len(text) <= _MAX_DESCRIPTION:
        return text
    return text[:_MAX_DESCRIPTION] + "\n... [truncated]"


def sanitize_error_log(text: str) -> str:
    """Truncate error log to MAX_LOG bytes, keeping head 8KB + tail 8KB."""
    text = _CTRL_RE.sub("", text)
    if len(text) <= _MAX_LOG:
        return text
    half = _MAX_LOG // 2
    return text[:half] + f"\n... [truncated {len(text) - _MAX_LOG} bytes] ...\n" + text[-half:]


def sanitize_environment(env: dict[str, str]) -> dict[str, str]:
    """Limit env keys and truncate values."""
    result = {}
    for i, (k, v) in enumerate(env.items()):
        if i >= _MAX_ENV_KEYS:
            break
        result[k] = v[:_MAX_ENV_VALUE]
    return result


def sanitize_tags(tags: list[str]) -> list[str]:
    """Limit number of tags."""
    if len(tags) <= _MAX_TAGS:
        return tags
    return tags[:_MAX_TAGS]


def sanitize_patch_attempts(
    attempts: list[dict[str, str]],
    max_attempts: int = _MAX_PATCH_ATTEMPTS,
    max_field_chars: int = _MAX_PATCH_ATTEMPT_FIELD,
) -> list[dict[str, str]]:
    """Limit failed patch attempts before writing them to durable memory."""
    if max_attempts <= 0 or max_field_chars <= 0:
        return []

    result: list[dict[str, str]] = []
    for attempt in attempts[:max_attempts]:
        if not isinstance(attempt, dict):
            continue
        cleaned: dict[str, str] = {}
        for key, value in attempt.items():
            if value is None:
                continue
            clean_key = _CTRL_RE.sub("", str(key))[:64]
            if not clean_key:
                continue
            text = _CTRL_RE.sub("", str(value))
            if len(text) > max_field_chars:
                text = text[:max_field_chars] + "\n... [truncated]"
            cleaned[clean_key] = text
        if cleaned:
            result.append(cleaned)
    return result


def sanitize_bug_input(
    description: str = "",
    error_log: str = "",
    environment: dict[str, str] | None = None,
    tags: list[str] | None = None,
) -> tuple[str, str, dict[str, str], list[str]]:
    """Sanitize all user inputs at once. Returns (desc, log, env, tags)."""
    return (
        sanitize_description(description),
        sanitize_error_log(error_log),
        sanitize_environment(environment or {}),
        sanitize_tags(tags or []),
    )
