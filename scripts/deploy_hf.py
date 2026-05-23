"""Deploy DebugMind to Hugging Face Spaces.

Usage:
    pip install huggingface-hub
    HF_TOKEN=your_token python scripts/deploy_hf.py

Optional env vars:
    HF_SPACE_ID  — target Space (default: zavoryn/debug-mind)
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPACE_ID = os.environ.get("HF_SPACE_ID", "chenming6886/debug-mind")

# Files/dirs to copy into the HF Space (everything else is excluded).
_INCLUDE_FILES = ["app.py", "requirements.txt"]
_INCLUDE_DIRS = ["src"]
_SEED_DIR = Path("evaluation") / "seed_cases"

_HF_README = """\
---
title: DebugMind
emoji: "\\U0001f9e0"
colorFrom: blue
colorTo: purple
sdk: gradio
sdk_version: "5.29.0"
app_file: app.py
pinned: false
---

# \\U0001f9e0 DebugMind — AI Bug Diagnosis Agent

**Every diagnosis is saved to a knowledge base. The more bugs it sees, the faster it gets.**

---

## \\U0001f50d Search Memory (No API Key Needed!)

Explore a knowledge base of **20 real-world bug cases** — semantic search + keyword matching.

Try these queries:
- `redis connection pool exhausted NullPointerException`
- `Spring Boot circular dependency on startup`
- `docker container OOM killed`
- `MySQL deadlock transaction rollback`
- `Kafka consumer lag growing indefinitely`

## \\U0001f916 Diagnose (Requires Anthropic API Key)

Get AI-powered root cause analysis. Enter your [Anthropic API key](https://console.anthropic.com) to enable.

## How It Works

1. **Memory Search** — vector similarity (75%) + keyword matching (25%) + verified-case boost
2. **AI Diagnosis** — ReAct agent loop with memory-augmented context
3. **Knowledge Accumulation** — every diagnosis is stored for future retrieval

---

[GitHub](https://github.com/zavoryn/debug-mind) · [README](https://github.com/zavoryn/debug-mind#readme)
"""


def main() -> None:
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit("HF_TOKEN env var is required. Get one at https://huggingface.co/settings/tokens")

    from huggingface_hub import HfApi

    api = HfApi(token=token)

    # Ensure the Space exists (Gradio SDK).
    try:
        api.space_info(repo_id=SPACE_ID)
        print(f"[deploy] Space {SPACE_ID} exists.")
    except Exception:
        print(f"[deploy] Creating Space {SPACE_ID} ...")
        from huggingface_hub import create_repo
        create_repo(repo_id=SPACE_ID, repo_type="space", space_sdk="gradio", exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="debugmind-hf-") as tmp_str:
        tmp = Path(tmp_str)

        # Copy files
        for f in _INCLUDE_FILES:
            shutil.copy2(ROOT / f, tmp / f)

        # Copy directories
        for d in _INCLUDE_DIRS:
            shutil.copytree(ROOT / d, tmp / d)

        # Copy seed cases
        seed_dest = tmp / _SEED_DIR
        seed_dest.mkdir(parents=True, exist_ok=True)
        seed_src = ROOT / _SEED_DIR
        if seed_src.exists():
            for f in seed_src.glob("*.md"):
                shutil.copy2(f, seed_dest / f.name)

        # Write HF README
        (tmp / "README.md").write_text(_HF_README, encoding="utf-8")

        # Upload
        print(f"[deploy] Uploading to {SPACE_ID} ...")
        api.upload_folder(
            folder_path=str(tmp),
            repo_id=SPACE_ID,
            repo_type="space",
        )
        print(f"[deploy] Done! https://huggingface.co/spaces/{SPACE_ID}")


if __name__ == "__main__":
    main()
