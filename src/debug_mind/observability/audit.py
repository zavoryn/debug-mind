"""Shared audit log writer with size-based rotation.

Both the CLI and MCP server write audit entries through this module so the
rotation logic stays in one place.

Env vars:
    DEBUG_MIND_AUDIT_MAX_BYTES   Max audit.jsonl size before rotation (default 50 MiB).
                                 Set to 0 to disable rotation.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MiB


def write_audit(
    audit_path: Path,
    op: str,
    case_id: str,
    actor: str = "cli",
    **details,
) -> None:
    """Append one audit entry to *audit_path*, rotating the file when it exceeds the size cap.

    Rotation strategy: rename current file to ``<stem>.1<suffix>`` and start fresh.
    At most one rotated copy is kept (the previous one is overwritten).
    """
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    max_bytes = int(os.environ.get("DEBUG_MIND_AUDIT_MAX_BYTES", str(_DEFAULT_MAX_BYTES)))
    if max_bytes > 0 and audit_path.exists():
        try:
            if audit_path.stat().st_size >= max_bytes:
                rotated = audit_path.with_stem(audit_path.stem + ".1")
                rotated.unlink(missing_ok=True)
                audit_path.rename(rotated)
        except OSError:
            pass  # rotation is best-effort; don't crash the write

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "op": op,
        "case_id": case_id,
        "details": details,
    }
    with open(audit_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
