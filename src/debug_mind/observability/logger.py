"""Structured logging for DebugMind — JSON or text format based on env var.

Why: Production debugging needs machine-parseable logs. This module wraps
Python's stdlib logging to emit structured JSON (or plain text) with fields
like trace_id, case_id, model, tokens_in, tokens_out, latency_ms.

Usage:
    from debug_mind.observability.logger import get_logger
    log = get_logger(__name__)
    log.info("tool call complete", extra={"trace_id": tid, "tool": "search_memory"})

Env vars:
    DEBUG_MIND_LOG_FORMAT=json|text  (default: text)
    DEBUG_MIND_LOG_FILE=path         (default: stderr)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_FORMAT = os.environ.get("DEBUG_MIND_LOG_FORMAT", "text").lower()
_LOG_FILE = os.environ.get("DEBUG_MIND_LOG_FILE", "")


class JSONFormatter(logging.Formatter):
    """Emits one JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Merge structured extra fields
        for key in (
            "trace_id",
            "case_id",
            "model",
            "tokens_in",
            "tokens_out",
            "latency_ms",
            "tool",
            "found",
            "saved",
            "op",
        ):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = str(record.exc_info[1])
        return json.dumps(entry, ensure_ascii=False, default=str)


def get_logger(name: str) -> logging.Logger:
    """Get a structured logger. Respects DEBUG_MIND_LOG_FORMAT and DEBUG_MIND_LOG_FILE."""
    logger = logging.getLogger(f"debug_mind.{name}")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    handler: logging.Handler

    if _FORMAT == "json":
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JSONFormatter())
    else:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )

    if _LOG_FILE:
        path = Path(_LOG_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(str(path), encoding="utf-8")
        handler.setFormatter(
            JSONFormatter()
            if _FORMAT == "json"
            else logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )

    logger.addHandler(handler)
    return logger


def _try_otel_span(trace_id: str, **attrs) -> None:
    """Best-effort OTel integration: if opentelemetry-api is installed, set span attributes."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span.is_recording():
            span.set_attribute("debug_mind.trace_id", trace_id)
            for k, v in attrs.items():
                span.set_attribute(f"debug_mind.{k}", v)
    except ImportError:
        pass
