"""Safety layer — DebugMind's consolidated security boundary.

This package is the single home for the security-specific logic that used to
be scattered across the governance layer (risk classification) and the patch
skill (sandbox path guards, command allowlist). Pulling it into one layer
means there is exactly one place to audit "what can this agent do, and what
stops it from doing something dangerous":

  - risk.py     : tool risk classification (read / write / dangerous / external)
  - paths.py    : sandbox-escape / path-traversal guard
  - commands.py : shell-injection guard + pytest command allowlist
  - (re-export) : input sanitization lives in debug_mind.sanitize

Both the engine (via Hermes) and the patch skill depend on this layer; the
layer depends on nothing else in the project, so it stays a leaf in the
dependency graph and can be reasoned about in isolation.
"""

from __future__ import annotations

from debug_mind.safety.commands import SHELL_METACHARS, safe_pytest_argv
from debug_mind.safety.paths import ensure_within_sandbox
from debug_mind.safety.risk import DEFAULT_TOOL_RISK, RiskLevel, classify_risk
from debug_mind.sanitize import sanitize_bug_input

__all__ = [
    "RiskLevel",
    "DEFAULT_TOOL_RISK",
    "classify_risk",
    "ensure_within_sandbox",
    "safe_pytest_argv",
    "SHELL_METACHARS",
    "sanitize_bug_input",
]
