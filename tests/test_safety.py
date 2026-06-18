"""Tests for the consolidated safety layer.

These exercise the safety primitives directly (in isolation from Hermes and
PatchSkill), so a regression in the security boundary is caught at the layer
that owns it — not only through the higher-level skills that consume it.
"""

from __future__ import annotations

import sys

import pytest

from debug_mind.safety import (
    DEFAULT_TOOL_RISK,
    RiskLevel,
    classify_risk,
    ensure_within_sandbox,
    safe_pytest_argv,
)


# ── Risk classification ───────────────────────────────────────────────────────


def test_classify_risk_known_tools():
    assert classify_risk("search_memory") == RiskLevel.READ
    assert classify_risk("save_to_memory") == RiskLevel.WRITE
    assert classify_risk("apply_and_test") == RiskLevel.DANGEROUS


def test_classify_risk_unknown_fails_closed():
    # An unregistered tool must resolve to UNKNOWN, never a permissive default.
    assert classify_risk("rm_minus_rf") == RiskLevel.UNKNOWN


def test_classify_risk_overrides():
    overridden = classify_risk("custom_tool", overrides={"custom_tool": RiskLevel.DANGEROUS})
    assert overridden == RiskLevel.DANGEROUS
    # Overrides must not mutate the shared default table.
    assert "custom_tool" not in DEFAULT_TOOL_RISK


def test_hermes_reexports_risklevel():
    # Backward-compat contract: callers still import RiskLevel from hermes.
    from debug_mind.hermes import DEFAULT_TOOL_RISK as hermes_table
    from debug_mind.hermes import RiskLevel as hermes_risk

    assert hermes_risk is RiskLevel
    assert hermes_table is DEFAULT_TOOL_RISK


# ── Sandbox path guard ────────────────────────────────────────────────────────


def test_ensure_within_sandbox_accepts_relative(tmp_path):
    target, err = ensure_within_sandbox(tmp_path, "src/app.py")
    assert err is None
    assert target == (tmp_path.resolve() / "src" / "app.py")


def test_ensure_within_sandbox_rejects_traversal(tmp_path):
    target, err = ensure_within_sandbox(tmp_path, "../../../etc/passwd")
    assert target is None
    assert "inside the sandbox" in err


def test_ensure_within_sandbox_rejects_absolute(tmp_path):
    abs_path = "C:\\Windows\\system32" if sys.platform == "win32" else "/etc/passwd"
    target, err = ensure_within_sandbox(tmp_path, abs_path)
    assert target is None
    assert "relative path" in err


def test_ensure_within_sandbox_rejects_empty(tmp_path):
    target, err = ensure_within_sandbox(tmp_path, "   ")
    assert target is None
    assert "non-empty" in err


# ── Command allowlist ─────────────────────────────────────────────────────────


def test_safe_pytest_argv_accepts_pytest():
    argv, err = safe_pytest_argv("pytest tests/test_foo.py -v")
    assert err is None
    assert argv[:3] == [sys.executable, "-m", "pytest"]
    assert argv[3:] == ["tests/test_foo.py", "-v"]


def test_safe_pytest_argv_accepts_python_m_pytest():
    argv, err = safe_pytest_argv("python -m pytest tests/")
    assert err is None
    assert argv == [sys.executable, "-m", "pytest", "tests/"]


@pytest.mark.parametrize(
    "bad_command",
    [
        "pytest tests/ && rm -rf /",
        "pytest tests/ | tee out.txt",
        "pytest tests/; echo pwned",
        "pytest tests/ > /tmp/x",
        "pytest $(whoami)",
    ],
)
def test_safe_pytest_argv_rejects_shell_operators(bad_command):
    argv, err = safe_pytest_argv(bad_command)
    assert argv is None
    assert err.startswith("Unsafe test_command")


def test_safe_pytest_argv_rejects_non_pytest():
    argv, err = safe_pytest_argv("bash deploy.sh")
    assert argv is None
    assert "only pytest commands are allowed" in err


def test_safe_pytest_argv_rejects_empty():
    argv, err = safe_pytest_argv("   ")
    assert argv is None
    assert "Unsafe test_command" in err
