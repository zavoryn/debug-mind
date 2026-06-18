"""Path-traversal / sandbox-escape guard.

A patch target arrives as an agent-supplied string. Without a guard, a value
like ``../../../etc/passwd`` (or an absolute path) would let a write escape the
sandboxed project copy. ``ensure_within_sandbox`` is the single check that all
file-write paths must pass through.
"""

from __future__ import annotations

from pathlib import Path


def ensure_within_sandbox(sandbox: Path, file_path: str) -> tuple[Path | None, str | None]:
    """Resolve ``file_path`` against ``sandbox`` and reject any escape.

    Returns ``(resolved_path, None)`` when the target is a relative path that
    stays inside the sandbox, or ``(None, error_message)`` otherwise. The error
    strings are part of the public contract (the agent reads them to
    self-correct, and tests assert on them) — do not reword casually.
    """
    if not file_path or not file_path.strip():
        return None, "Patch target must be a non-empty relative path inside the sandbox"

    raw_path = Path(file_path)
    if raw_path.is_absolute():
        return None, "Patch target must be a relative path inside the sandbox"

    sandbox_root = sandbox.resolve()
    target = (sandbox_root / raw_path).resolve()
    try:
        target.relative_to(sandbox_root)
    except ValueError:
        return None, "Patch target must stay inside the sandbox"

    return target, None
