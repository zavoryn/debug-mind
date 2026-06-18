"""Tool risk classification — the policy table for what each tool can do.

Risk level drives how the governance layer treats a tool call: READ tools run
freely, DANGEROUS tools can be paused for human approval (HITL), etc. Keeping
the enum and the default table here — rather than inside Hermes — means the
risk policy is a property of the safety layer, and any layer that needs to ask
"how dangerous is this tool?" goes through one canonical classifier.
"""

from __future__ import annotations

from enum import Enum


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


def classify_risk(
    tool_name: str,
    overrides: dict[str, RiskLevel] | None = None,
) -> RiskLevel:
    """Return the risk level for a tool, falling back to UNKNOWN.

    `overrides` lets a caller layer extra policy on top of the defaults
    (e.g. a deployment that wants to treat a custom tool as DANGEROUS).
    An unknown tool deliberately resolves to UNKNOWN rather than a permissive
    default — fail closed, not open.
    """
    table = DEFAULT_TOOL_RISK if not overrides else {**DEFAULT_TOOL_RISK, **overrides}
    return table.get(tool_name, RiskLevel.UNKNOWN)
