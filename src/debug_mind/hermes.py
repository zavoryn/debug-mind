"""Hermes tool governance for DebugMind agents.

Hermes sits between the LLM and concrete skills. It keeps the tool boundary
explicit: validate parameters, classify risk, block repeated calls, and write a
compact trajectory record that can be replayed during debugging.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class RiskLevel(str, Enum):
    """Risk class for an agent tool call."""

    READ = "read"
    WRITE = "write"
    DANGEROUS = "dangerous"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


DEFAULT_TOOL_RISK: dict[str, RiskLevel] = {
    "search_memory": RiskLevel.READ,
    "search_code": RiskLevel.READ,
    "read_file": RiskLevel.READ,
    "list_project_structure": RiskLevel.READ,
    "parse_symbols": RiskLevel.READ,
    "search_jvm_stack_pattern": RiskLevel.READ,
    "propose_patch": RiskLevel.WRITE,
    "save_to_memory": RiskLevel.WRITE,
    "apply_and_test": RiskLevel.DANGEROUS,
}

_SCHEMA_TYPE_NAMES = {"string", "integer", "number", "boolean", "array", "object"}
_REDACT_KEYS = {"api_key", "auth", "auth_token", "password", "secret", "token"}
_HASH_ONLY_KEYS = {"patch_diff", "original_content", "fixed_content"}


@dataclass(frozen=True)
class ToolDecision:
    """The policy decision made before a tool is executed."""

    tool_name: str
    params: dict[str, Any]
    turn: int
    allowed: bool
    risk_level: RiskLevel
    reason: str = "allowed"
    message: str = ""
    fingerprint: str = ""
    duplicate_of: int | None = None


class HermesGovernance:
    """Policy layer for tool calls within one diagnosis run."""

    def __init__(
        self,
        tool_schemas: dict[str, dict[str, Any]] | None = None,
        tool_risks: dict[str, RiskLevel] | None = None,
        trace_path: Path | str | None = None,
        trace_id: str | None = None,
    ) -> None:
        self.tool_schemas = tool_schemas or {}
        self.tool_risks = {**DEFAULT_TOOL_RISK, **(tool_risks or {})}
        self.trace_path = Path(trace_path) if trace_path else None
        self.trace_id = trace_id or ""
        self._seen_fingerprints: dict[str, int] = {}

    def inspect_tool_call(
        self,
        tool_name: str,
        params: dict[str, Any],
        turn: int,
    ) -> ToolDecision:
        """Validate and classify a tool call before execution."""
        risk = self.tool_risks.get(tool_name, RiskLevel.UNKNOWN)
        fingerprint = self._fingerprint(tool_name, params)

        validation_error = validate_tool_params(
            tool_name, params, self.tool_schemas.get(tool_name, {})
        )
        if validation_error:
            return ToolDecision(
                tool_name=tool_name,
                params=params,
                turn=turn,
                allowed=False,
                risk_level=risk,
                reason="validation_error",
                message=validation_error,
                fingerprint=fingerprint,
            )

        duplicate_of = self._seen_fingerprints.get(fingerprint)
        if duplicate_of is not None:
            return ToolDecision(
                tool_name=tool_name,
                params=params,
                turn=turn,
                allowed=False,
                risk_level=risk,
                reason="duplicate_tool_call",
                message=(
                    f"Tool '{tool_name}' was already called with the same parameters "
                    f"on turn {duplicate_of}."
                ),
                fingerprint=fingerprint,
                duplicate_of=duplicate_of,
            )

        self._seen_fingerprints[fingerprint] = turn
        return ToolDecision(
            tool_name=tool_name,
            params=params,
            turn=turn,
            allowed=True,
            risk_level=risk,
            fingerprint=fingerprint,
        )

    def blocked_result(self, decision: ToolDecision) -> dict[str, Any]:
        """Build a tool_result payload the LLM can read and self-correct from."""
        hint = "Fix the parameters and retry this tool call."
        if decision.reason == "duplicate_tool_call":
            hint = "Use the previous result or change the query, file range, or patch."

        return {
            "blocked": True,
            "reason": decision.reason,
            "risk_level": decision.risk_level.value,
            "error": decision.message or decision.reason,
            "hint": hint,
        }

    def record_tool_result(
        self,
        decision: ToolDecision,
        result: dict[str, Any],
        side_effect: str | None,
        latency_ms: int,
    ) -> None:
        """Append one compact trajectory event to the trace file."""
        if self.trace_path is None:
            return

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "trace_id": self.trace_id,
            "turn": decision.turn,
            "tool": decision.tool_name,
            "risk_level": decision.risk_level.value,
            "decision": "allowed" if decision.allowed else "blocked",
            "reason": decision.reason,
            "side_effect": side_effect,
            "latency_ms": latency_ms,
            "input_hash": decision.fingerprint,
            "input_summary": _summarize_mapping(decision.params),
            "result_summary": _summarize_mapping(result),
        }
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.trace_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    @staticmethod
    def _fingerprint(tool_name: str, params: dict[str, Any]) -> str:
        raw = json.dumps(
            {"tool": tool_name, "params": params},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_tool_params(
    tool_name: str,
    params: dict[str, Any],
    schema: dict[str, Any],
) -> str | None:
    """Validate LLM-generated tool params against a JSON-schema-like object."""
    if not schema:
        return None
    if not isinstance(params, dict):
        return f"Tool '{tool_name}' parameters must be an object."

    required: list[str] = schema.get("required", [])
    properties: dict[str, Any] = schema.get("properties", {})

    missing = [field for field in required if field not in params or params[field] is None]
    if missing:
        return (
            f"Tool '{tool_name}' is missing required field(s): {missing}. "
            f"Required: {required}. Got keys: {list(params.keys())}."
        )

    errors: list[str] = []
    for name, value in params.items():
        prop_schema = properties.get(name)
        if not prop_schema or value is None:
            continue
        errors.extend(_validate_value(path=name, value=value, schema=prop_schema))

    if errors:
        return f"Tool '{tool_name}' parameter validation error(s): {'; '.join(errors)}."
    return None


def _validate_value(path: str, value: Any, schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_type = schema.get("type")

    if expected_type in _SCHEMA_TYPE_NAMES and not _matches_type(value, expected_type):
        errors.append(f"'{path}' should be {expected_type}, got {type(value).__name__}")
        return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"'{path}' should be one of {schema['enum']}, got {value!r}")

    if expected_type in {"integer", "number"}:
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            errors.append(f"'{path}' should be >= {minimum}, got {value!r}")
        if maximum is not None and value > maximum:
            errors.append(f"'{path}' should be <= {maximum}, got {value!r}")

    if expected_type == "string" and schema.get("minLength") is not None:
        min_length = int(schema["minLength"])
        if len(value) < min_length:
            errors.append(f"'{path}' should have length >= {min_length}")

    if expected_type == "array":
        item_schema = schema.get("items") or {}
        for idx, item in enumerate(value):
            errors.extend(_validate_value(f"{path}[{idx}]", item, item_schema))

    if expected_type == "object":
        prop_schemas = schema.get("properties") or {}
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            child_schema = prop_schemas.get(key)
            if child_schema:
                errors.extend(_validate_value(f"{path}.{key}", item, child_schema))
            elif isinstance(additional, dict):
                errors.extend(_validate_value(f"{path}.{key}", item, additional))
            elif additional is False:
                errors.append(f"'{path}.{key}' is not an allowed field")

    return errors


def _matches_type(value: Any, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "object":
        return isinstance(value, dict)
    return True


def _summarize_mapping(data: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _summarize_value(str(key), value) for key, value in data.items()}


def _summarize_value(key: str, value: Any) -> Any:
    key_lc = key.lower()
    if any(marker in key_lc for marker in _REDACT_KEYS):
        return "[redacted]"
    if key in _HASH_ONLY_KEYS:
        text = str(value)
        return {
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "chars": len(text),
        }
    if isinstance(value, dict):
        return _summarize_mapping(value)
    if isinstance(value, list):
        return [_summarize_value(key, item) for item in value[:10]]
    if isinstance(value, str):
        return value[:500] + "...[truncated]" if len(value) > 500 else value
    return value
