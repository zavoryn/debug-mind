"""Command-injection guard + pytest allowlist.

The test command that drives sandbox verification is supplied by the agent, so
it is untrusted input. Two defenses live here:

  1. Reject any shell metacharacter — the command is never run through a shell
     (``shell=False``), but rejecting operators stops attempts like
     ``pytest x && rm -rf /`` from even being parsed as a single "command".
  2. Allowlist: only ``pytest`` (or ``python -m pytest``) entrypoints are
     accepted. Anything else is refused.

The returned argv is always normalised to ``[sys.executable, "-m", "pytest", ...]``
so the sandbox runs the project's own interpreter, not an arbitrary binary.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

SHELL_METACHARS = ("&&", "||", ";", "|", ">", "<", "`", "$(", "\n", "\r")


def safe_pytest_argv(test_command: str) -> tuple[list[str] | None, str | None]:
    """Convert an agent-provided test command into a safe argv list.

    Returns ``(argv, None)`` on success or ``(None, error_message)`` on refusal.
    Error strings begin with ``"Unsafe test_command"`` — tests assert on this
    prefix, so keep it stable.
    """
    raw = test_command.strip()
    if not raw:
        return None, "Unsafe test_command: command is empty"

    if any(token in raw for token in SHELL_METACHARS):
        return None, "Unsafe test_command: shell operators are not allowed"

    try:
        parts = shlex.split(raw, posix=os.name != "nt")
    except ValueError as exc:
        return None, f"Unsafe test_command: cannot parse command: {exc}"

    parts = [_strip_matching_quotes(part) for part in parts]
    if not parts:
        return None, "Unsafe test_command: command is empty"

    executable = Path(parts[0]).name.lower()
    if executable == "pytest":
        return [sys.executable, "-m", "pytest", *parts[1:]], None

    if executable in {"python", "python.exe", "python3", "python3.exe"}:
        if len(parts) >= 3 and parts[1:3] == ["-m", "pytest"]:
            return [sys.executable, "-m", "pytest", *parts[3:]], None

    current_python = Path(sys.executable).name.lower()
    if executable == current_python and len(parts) >= 3 and parts[1:3] == ["-m", "pytest"]:
        return [sys.executable, "-m", "pytest", *parts[3:]], None

    return None, "Unsafe test_command: only pytest commands are allowed"


def _strip_matching_quotes(value: str) -> str:
    """Remove one layer of matching quotes left by Windows shlex mode."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
