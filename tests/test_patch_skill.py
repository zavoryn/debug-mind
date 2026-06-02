"""Tests for PatchSkill — propose_patch and apply_and_test."""

from __future__ import annotations

import shutil
from pathlib import Path


from debug_mind.skills.patch import apply_and_test, propose_patch


# ── Fixtures ─────────────────────────────────────────────────────────────────

DEMO_PROJECT = Path(__file__).parent / "fixtures" / "demo_bug"

BUGGY_CONTENT = '''\
"""A simple calculator module — contains a deliberate bug for demo purposes."""


def add(a: float, b: float) -> float:
    return a + b


def subtract(a: float, b: float) -> float:
    return a - b


def multiply(a: float, b: float) -> float:
    return a * b


def divide(a: float, b: float) -> float:
    """Divide a by b. BUG: uses multiplication instead of division."""
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a * b  # ← BUG: should be a / b
'''

FIXED_CONTENT = '''\
"""A simple calculator module — contains a deliberate bug for demo purposes."""


def add(a: float, b: float) -> float:
    return a + b


def subtract(a: float, b: float) -> float:
    return a - b


def multiply(a: float, b: float) -> float:
    return a * b


def divide(a: float, b: float) -> float:
    """Divide a by b."""
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b
'''

WRONG_CONTENT = '''\
"""A simple calculator module."""


def add(a: float, b: float) -> float:
    return a + b


def subtract(a: float, b: float) -> float:
    return a - b


def multiply(a: float, b: float) -> float:
    return a * b


def divide(a: float, b: float) -> float:
    """Wrong fix: returns 0 always."""
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return 0  # ← WRONG FIX
'''


# ── propose_patch ─────────────────────────────────────────────────────────────


def test_propose_patch_produces_diff():
    result = propose_patch(
        file_path="calculator.py",
        original_content=BUGGY_CONTENT,
        fixed_content=FIXED_CONTENT,
        description="Fix divide: use / instead of *",
    )
    assert "diff" in result
    assert "error" not in result
    assert "a/calculator.py" in result["diff"]
    assert "-    return a * b" in result["diff"]
    assert "+    return a / b" in result["diff"]
    assert result["changed_lines"] > 0


def test_propose_patch_identical_content():
    result = propose_patch("foo.py", BUGGY_CONTENT, BUGGY_CONTENT)
    assert "error" in result


# ── apply_and_test — happy path ───────────────────────────────────────────────


def test_apply_and_test_passing(tmp_path):
    """Correct patch → tests pass."""
    # Set up a temp project mirroring demo_bug
    proj = tmp_path / "project"
    shutil.copytree(DEMO_PROJECT, proj)

    patch_result = propose_patch("calculator.py", BUGGY_CONTENT, FIXED_CONTENT, "fix divide")
    assert "diff" in patch_result

    result = apply_and_test(
        file_path="calculator.py",
        patch_diff=patch_result["diff"],
        test_command="pytest test_calculator.py -v",
        project_path=str(proj),
    )

    assert result["passed"] is True, f"Expected tests to pass. Output:\n{result.get('output')}"
    assert "error" not in result


# ── apply_and_test — failure / dead-end path ─────────────────────────────────


def test_apply_and_test_failing(tmp_path):
    """Wrong patch → tests fail → caller should record as dead-end."""
    proj = tmp_path / "project"
    shutil.copytree(DEMO_PROJECT, proj)

    patch_result = propose_patch("calculator.py", BUGGY_CONTENT, WRONG_CONTENT, "wrong fix")
    assert "diff" in patch_result

    result = apply_and_test(
        file_path="calculator.py",
        patch_diff=patch_result["diff"],
        test_command="pytest test_calculator.py -v",
        project_path=str(proj),
    )

    assert result["passed"] is False, "Expected tests to fail with wrong fix"
    assert result["patch_diff"] == patch_result["diff"]  # diff echoed back for dead-end recording
    assert result.get("output")  # has test output for diagnosis


# ── apply_and_test — sandbox isolation ───────────────────────────────────────


def test_apply_and_test_does_not_modify_original(tmp_path):
    """Sandbox copy is used — original project files must be untouched."""
    proj = tmp_path / "project"
    shutil.copytree(DEMO_PROJECT, proj)

    original_content = (proj / "calculator.py").read_text(encoding="utf-8")

    patch_result = propose_patch("calculator.py", BUGGY_CONTENT, FIXED_CONTENT)
    apply_and_test(
        file_path="calculator.py",
        patch_diff=patch_result["diff"],
        test_command="pytest test_calculator.py",
        project_path=str(proj),
    )

    # Original file must be identical after apply_and_test
    after_content = (proj / "calculator.py").read_text(encoding="utf-8")
    assert after_content == original_content, "apply_and_test must not modify the original project"


# ── apply_and_test — missing file ────────────────────────────────────────────


def test_apply_and_test_missing_file(tmp_path):
    proj = tmp_path / "project"
    shutil.copytree(DEMO_PROJECT, proj)

    patch_result = propose_patch("calculator.py", BUGGY_CONTENT, FIXED_CONTENT)
    result = apply_and_test(
        file_path="nonexistent.py",
        patch_diff=patch_result["diff"],
        test_command="pytest test_calculator.py",
        project_path=str(proj),
    )

    assert result["passed"] is False
    assert "error" in result


# ── PatchSkill registration ───────────────────────────────────────────────────


def test_patch_skill_registered():
    """PatchSkill must be in the default registry."""
    from debug_mind.skills.registry import get_default_registry, reset_default_registry_for_tests

    reset_default_registry_for_tests()
    registry = get_default_registry()
    skill = registry.get("patch")
    assert skill is not None
    assert skill.name == "patch"


def test_patch_skill_no_tools_without_project():
    """PatchSkill exposes no tools when project_path is absent."""
    from debug_mind.skills.patch import PatchSkill

    skill = PatchSkill()
    tools = skill.get_tools({"project_path": None})
    assert tools == []


def test_patch_skill_tools_with_project(tmp_path):
    """PatchSkill exposes propose_patch and apply_and_test when project_path is set."""
    from debug_mind.skills.patch import PatchSkill

    skill = PatchSkill()
    tools = skill.get_tools({"project_path": str(tmp_path)})
    names = {t["name"] for t in tools}
    assert "propose_patch" in names
    assert "apply_and_test" in names
