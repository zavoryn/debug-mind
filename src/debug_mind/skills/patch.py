"""Patch skill — propose, apply, and test code fixes in a sandbox.

This skill upgrades DebugMind from "diagnosis advisor" to "self-healing agent":
the agent can generate a unified diff, apply it to an isolated sandbox copy of
the project, run the test suite, and decide whether to commit or discard.

Failed patches are returned with full test output so the caller (agent or user)
can record them as dead-end attempts — the foundation of the "complete solution-
space map including dead ends" design philosophy.

Two tools are exposed:
  propose_patch   — turn original + fixed content into a unified diff
  apply_and_test  — apply a diff in a sandbox, run tests, return pass/fail
"""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from debug_mind.observability.logger import get_logger
from debug_mind.skills.registry import Skill

_log = get_logger("patch_skill")

# Directories to exclude from sandbox copy (expensive / irrelevant to tests)
_SANDBOX_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
    "chroma",
    ".idea",
    ".gradle",
}

# Hard cap on test run time (seconds)
_TEST_TIMEOUT_SECS = 60


# ── Pure helper functions (no Skill dependency) ──────────────────────────────


def propose_patch(
    file_path: str,
    original_content: str,
    fixed_content: str,
    description: str = "",
) -> dict[str, Any]:
    """Generate a unified diff between original and fixed file content.

    unified_diff's lineterm parameter only applies to HEADER lines (---, +++, @@).
    Content lines carry whatever terminator the input has. So we strip all
    terminators from input with splitlines() (no keepends), use lineterm="" so
    headers also have no terminator, then join uniformly with "\\n" so every line
    gets exactly one newline. This avoids double-newline issues on all platforms.
    """
    if original_content == fixed_content:
        return {"error": "original_content and fixed_content are identical — no patch needed"}

    original_lines = original_content.splitlines()
    fixed_lines = fixed_content.splitlines()

    diff_lines = list(
        difflib.unified_diff(
            original_lines,
            fixed_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm="",
        )
    )

    if not diff_lines:
        return {"error": "difflib produced an empty diff — check your inputs"}

    diff_text = "\n".join(diff_lines) + "\n"
    changed_lines = sum(
        1 for ln in diff_lines if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))
    )

    return {
        "diff": diff_text,
        "file_path": file_path,
        "description": description,
        "changed_lines": changed_lines,
    }


def apply_and_test(
    file_path: str,
    patch_diff: str,
    test_command: str,
    project_path: str,
) -> dict[str, Any]:
    """Apply a unified diff in a sandbox copy of the project, then run tests.

    Steps:
      1. Copy project to a temp directory (excluding .git / .venv / etc.)
      2. Apply the patch to the sandboxed file (pure Python, no `patch` binary)
      3. Run test_command using the current Python interpreter
      4. Clean up the sandbox regardless of outcome
      5. Return {passed, output, patch_diff}

    The caller is responsible for recording failed patches as dead-end attempts.
    """
    project = Path(project_path).resolve()
    if not project.is_dir():
        return {"error": f"project_path does not exist: {project_path}", "passed": False}

    sandbox = Path(tempfile.mkdtemp(prefix="debugmind_sandbox_"))
    try:
        _copy_project(project, sandbox)

        target = sandbox / file_path
        if not target.exists():
            return {
                "error": f"File not found in sandbox: {file_path}",
                "passed": False,
                "patch_diff": patch_diff,
            }

        apply_result = _apply_diff(target, patch_diff)
        if not apply_result["ok"]:
            return {
                "error": f"Patch failed to apply: {apply_result['reason']}",
                "passed": False,
                "patch_diff": patch_diff,
            }

        test_result = _run_tests(test_command, cwd=sandbox)

        return {
            "passed": test_result["passed"],
            "output": test_result["output"],
            "patch_diff": patch_diff,
            "return_code": test_result["return_code"],
        }

    except Exception as exc:
        _log.warning("apply_and_test failed", extra={"err": str(exc)})
        return {"error": str(exc), "passed": False, "patch_diff": patch_diff}

    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _copy_project(src: Path, dst: Path) -> None:
    """Shallow copy of project, skipping heavy / irrelevant directories."""
    for item in src.iterdir():
        if item.name in _SANDBOX_SKIP_DIRS or item.name.startswith("."):
            continue
        dest_item = dst / item.name
        if item.is_dir():
            shutil.copytree(
                item,
                dest_item,
                ignore=shutil.ignore_patterns(*_SANDBOX_SKIP_DIRS),
                dirs_exist_ok=False,
            )
        else:
            shutil.copy2(item, dest_item)


def _apply_diff(target_file: Path, diff_text: str) -> dict[str, Any]:
    """Apply a unified diff to target_file in-place using pure Python.

    Key design: processes each hunk line-by-line (context / remove / add)
    in order. This correctly handles context lines that appear BEFORE the
    first changed line in a hunk — the previous approach of skipping context
    and jumping straight to removes caused off-by-N mismatches.

    Line endings: normalises the file to LF before comparison and writes back
    with LF (newline='') to avoid Windows CRLF translation issues.
    """
    try:
        raw = target_file.read_text(encoding="utf-8")
        normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
        file_lines = normalized.splitlines(keepends=True)
    except Exception as exc:
        return {"ok": False, "reason": f"Cannot read target file: {exc}"}

    diff_lines = diff_text.splitlines(keepends=True)
    result_lines = list(file_lines)

    i = 0
    applied = 0

    while i < len(diff_lines):
        line = diff_lines[i]

        if line.startswith(("---", "+++")):
            i += 1
            continue

        if not line.startswith("@@"):
            i += 1
            continue

        # Parse hunk header: @@ -start[,count] +start[,count] @@
        try:
            old_part = line.split("@@")[1].strip().split()[0]  # e.g. "-10,5"
            old_start_1 = int(old_part.split(",")[0].lstrip("-"))  # 1-indexed
        except (IndexError, ValueError) as exc:
            return {"ok": False, "reason": f"Bad hunk header {line!r}: {exc}"}

        # Collect hunk body lines
        i += 1
        hunk: list[str] = []
        while i < len(diff_lines):
            dl = diff_lines[i]
            if dl.startswith("@@") or dl.startswith("---") or dl.startswith("+++"):
                break
            hunk.append(dl)
            i += 1

        # Apply this hunk
        res = _apply_hunk(result_lines, old_start_1, hunk)
        if not res["ok"]:
            return res
        result_lines = res["lines"]
        applied += 1

    if applied == 0:
        return {"ok": False, "reason": "No hunks found in diff"}

    try:
        with open(target_file, "w", encoding="utf-8", newline="") as fh:
            fh.write("".join(result_lines))
    except Exception as exc:
        return {"ok": False, "reason": f"Cannot write patched file: {exc}"}

    return {"ok": True, "reason": ""}


def _apply_hunk(lines: list[str], start_1indexed: int, hunk: list[str]) -> dict[str, Any]:
    """Apply a single parsed hunk to lines.

    Walks the hunk body sequentially:
      ' ' prefix  -> context: keep the original line, advance read position
      '-' prefix  -> remove:  skip the original line, advance read position
      '+' prefix  -> add:     insert the new content, do NOT advance read position
      empty line  -> treat as context empty line

    Splices the rebuilt region back into the full file and returns the new list.
    """
    pos = start_1indexed - 1  # 0-indexed read position in original lines
    new_segment: list[str] = []

    for hl in hunk:
        stripped = hl.rstrip("\n\r")

        if stripped.startswith(" "):
            # Context line: keep the original
            if pos >= len(lines):
                return {
                    "ok": False,
                    "reason": f"Context line out of range at pos {pos}",
                    "lines": lines,
                }
            new_segment.append(lines[pos])
            pos += 1

        elif stripped.startswith("-"):
            # Remove line: skip original
            if pos >= len(lines):
                return {
                    "ok": False,
                    "reason": f"Remove line out of range at pos {pos}",
                    "lines": lines,
                }
            pos += 1

        elif stripped.startswith("+"):
            # Add line: insert new content
            content = stripped[1:]
            new_segment.append(content + "\n")

        else:
            # Empty or unknown — treat as context empty line if file has one
            if pos < len(lines):
                new_segment.append(lines[pos])
                pos += 1

    prefix = lines[: start_1indexed - 1]
    suffix = lines[pos:]
    return {"ok": True, "lines": prefix + new_segment + suffix, "reason": ""}


def _run_tests(test_command: str, cwd: Path) -> dict[str, Any]:
    """Run test_command in cwd using the current Python interpreter.

    Replaces bare `pytest` with `{sys.executable} -m pytest` so the correct
    venv is always used — no dependency on PATH.
    """
    cmd = test_command.strip()
    if cmd.startswith("pytest"):
        cmd = f"{sys.executable} -m {cmd}"
    elif cmd.startswith("python "):
        cmd = cmd.replace("python ", f"{sys.executable} ", 1)

    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=_TEST_TIMEOUT_SECS,
            env={**os.environ, "PYTHONPATH": str(cwd)},
        )
        output = (proc.stdout + proc.stderr).strip()
        return {
            "passed": proc.returncode == 0,
            "return_code": proc.returncode,
            "output": output[:3000],
        }
    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "return_code": -1,
            "output": f"Test run timed out after {_TEST_TIMEOUT_SECS}s",
        }
    except Exception as exc:
        return {"passed": False, "return_code": -1, "output": str(exc)}


# ── Skill wrapper ─────────────────────────────────────────────────────────────


class PatchSkill(Skill):
    """Generate, apply, and validate code patches in a sandbox.

    Only active when a project_path is set — patches require a real codebase.
    Failed patches include full test output so the agent can record them as
    dead-end attempts in memory, preventing repeated failed strategies.
    """

    name = "patch"
    description = (
        "Propose a code fix as a unified diff, apply it in a sandbox, "
        "and validate with tests. Enables self-healing: diagnose -> patch -> verify."
    )

    def get_tools(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        if not context.get("project_path"):
            return []
        return [
            {
                "name": "propose_patch",
                "description": (
                    "Generate a unified diff (patch) between the original file content "
                    "and your proposed fix. Use after diagnosing the root cause and before "
                    "applying the fix. The diff can then be passed to apply_and_test."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Relative path to the file being fixed (e.g. 'src/utils.py').",
                        },
                        "original_content": {
                            "type": "string",
                            "description": "The current content of the file (as read by read_file).",
                        },
                        "fixed_content": {
                            "type": "string",
                            "description": "Your corrected version of the file content.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Brief explanation of what the patch changes and why.",
                        },
                    },
                    "required": ["file_path", "original_content", "fixed_content"],
                },
            },
            {
                "name": "apply_and_test",
                "description": (
                    "Apply a unified diff to a sandbox copy of the project and run the test suite. "
                    "Returns passed=true if tests pass (fix is valid), or passed=false with full "
                    "test output if they fail. On failure, record this patch in save_to_memory "
                    "as a dead-end attempt so the system never repeats the same broken fix."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Relative path to the file being patched.",
                        },
                        "patch_diff": {
                            "type": "string",
                            "description": "Unified diff string from propose_patch.",
                        },
                        "test_command": {
                            "type": "string",
                            "description": (
                                "Test command to run in the sandbox, e.g. 'pytest tests/' "
                                "or 'pytest tests/test_foo.py -v'. "
                                "Use the most targeted test that covers the fixed code."
                            ),
                        },
                    },
                    "required": ["file_path", "patch_diff", "test_command"],
                },
            },
        ]

    def execute(
        self, tool_name: str, params: dict[str, Any], context: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        project_path = context.get("project_path")
        if not project_path:
            return {"error": "PatchSkill requires a project_path in context"}, None

        if tool_name == "propose_patch":
            result = propose_patch(
                file_path=params["file_path"],
                original_content=params["original_content"],
                fixed_content=params["fixed_content"],
                description=params.get("description", ""),
            )
            return result, None

        if tool_name == "apply_and_test":
            result = apply_and_test(
                file_path=params["file_path"],
                patch_diff=params["patch_diff"],
                test_command=params["test_command"],
                project_path=project_path,
            )
            return result, "patch_test"

        return {"error": f"PatchSkill does not handle tool: {tool_name}"}, None
