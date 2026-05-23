"""Hugging Face Spaces entry point for DebugMind demo.

On startup:
1. Seeds the memory with pre-built bug cases from evaluation/seed_cases/
2. Launches the Gradio web UI on the default port.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

# ── Demo config ───────────────────────────────────────────────────────────────
DEMO_MEMORY_DIR = Path(os.environ.get("DEBUG_MIND_MEMORY_DIR", "/tmp/debug_mind_demo"))
os.environ["DEBUG_MIND_MEMORY_DIR"] = str(DEMO_MEMORY_DIR)
os.environ.setdefault("DEBUG_MIND_BACKEND", "sqlite")


def _seed_memory() -> None:
    """Copy seed cases into demo memory directory and rebuild the vector index.

    Skips if already seeded (cases_dir has >= 10 files).
    """
    from debug_mind.memory.store import MemoryStore, _markdown_to_case

    cases_dir = DEMO_MEMORY_DIR / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)

    seed_dir = ROOT / "evaluation" / "seed_cases"
    if not seed_dir.exists():
        print("[demo] No seed_cases directory found — starting with empty memory.")
        return

    existing = list(cases_dir.glob("*.md"))
    if len(existing) >= 10:
        print(f"[demo] Memory already seeded ({len(existing)} cases). Skipping.")
        return

    copied = 0
    for src in seed_dir.glob("*.md"):
        # Derive dest filename from the case_id embedded in the file
        case = _markdown_to_case(src)
        if case is None:
            continue
        dest = cases_dir / f"{case.id}.md"
        if not dest.exists():
            shutil.copy2(src, dest)
            copied += 1

    if copied:
        print(f"[demo] Copied {copied} seed cases. Rebuilding vector index...")
        store = MemoryStore(memory_dir=DEMO_MEMORY_DIR)
        indexed = store.rebuild_index()
        store.close()
        print(f"[demo] Index ready — {indexed} cases indexed.")
    else:
        print("[demo] Seed cases already present.")


# ── Run ───────────────────────────────────────────────────────────────────────
# HF Spaces executes this file directly with `python app.py`
_seed_memory()

from debug_mind.web import launch_ui
launch_ui(port=7860, share=False, memory_dir=str(DEMO_MEMORY_DIR))
