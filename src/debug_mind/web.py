"""Gradio web UI for DebugMind — try it in the browser.

Start with: debug-mind web
Optional dependency: pip install gradio
"""

from __future__ import annotations

import os

from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import DiagnosisResult


def _get_memory() -> MemoryStore:
    from pathlib import Path

    mem_dir = os.environ.get("DEBUG_MIND_MEMORY_DIR", "memory")
    return MemoryStore(memory_dir=Path(mem_dir))


def launch_ui(port: int = 7860, share: bool = False) -> None:
    """Launch the Gradio web interface."""
    try:
        import gradio as gr
    except ImportError:
        print("Gradio is required for the web UI. Install with: pip install gradio")
        return

    memory = _get_memory()

    # ── Diagnose tab ──────────────────────────────────────────────
    def do_diagnose(description: str, error_log: str, env_text: str):
        if not description.strip():
            return "Please enter a bug description."
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return "Set ANTHROPIC_API_KEY to enable diagnosis."
        from debug_mind.agent import DiagnosticAgent

        env: dict[str, str] = {}
        if env_text.strip():
            for line in env_text.strip().split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()

        agent = DiagnosticAgent(memory=memory)
        result: DiagnosisResult = agent.diagnose(description, error_log=error_log, environment=env)
        return f"""## Diagnosis Result

**Root Cause:** {result.root_cause}

**Fix Suggestion:** {result.fix_suggestion}

**Confidence:** {result.confidence:.0%}

**Similar Cases Found:** {result.similar_cases_found}

**Steps:**
{chr(10).join(f"- {s}" for s in result.diagnosis_steps)}
"""

    # ── Memory tab ────────────────────────────────────────────────
    def do_search(query: str, top_k: int = 5):
        if not query.strip():
            return "Enter a search query."
        results = memory.search(query, top_k=top_k)
        if not results:
            return "No matching cases found."
        lines = []
        for r in results:
            c = r.case
            verified = "✓" if c.verified else "✗"
            lines.append(
                f"### [{verified}] {c.title} (score: {r.score:.3f})\n"
                f"**Root Cause:** {c.root_cause}\n"
                f"**Fix:** {c.fix_suggestion}\n"
                f"**Hits:** {c.hit_count} | ID: `{c.id}`"
            )
        return "\n\n---\n\n".join(lines)

    def do_list(limit: int = 20):
        cases = memory.list_recent(limit=limit)
        if not cases:
            return "No cases in memory."
        lines = []
        for c in cases:
            verified = "✓" if c.verified else "✗"
            lines.append(f"- [{verified}] **{c.title}** ({c.severity.value}) — `{c.id}`")
        return "\n".join(lines)

    # ── Stats tab ─────────────────────────────────────────────────
    def do_stats():
        s = memory.stats()
        return f"""## Memory Stats

- **Total Cases:** {s.total_cases}
- **Verified:** {s.verified} | **Unverified:** {s.unverified}
- **By Severity:** {s.by_severity}
"""

    # ── Build UI ──────────────────────────────────────────────────
    with gr.Blocks(title="DebugMind") as app:
        gr.Markdown("# DebugMind — AI Bug Diagnosis Agent")

        with gr.Tab("Diagnose"):
            desc = gr.Textbox(
                label="Bug Description",
                lines=3,
                placeholder="NullPointerException in UserService...",
            )
            log = gr.Textbox(label="Error Log (optional)", lines=5)
            env = gr.Textbox(
                label="Environment (optional, key=value per line)",
                lines=2,
                placeholder="language=java\nframework=Spring Boot 3.2",
            )
            btn = gr.Button("Diagnose")
            out = gr.Markdown()
            btn.click(fn=do_diagnose, inputs=[desc, log, env], outputs=out)

        with gr.Tab("Memory"):
            query = gr.Textbox(label="Search", placeholder="redis connection timeout")
            k = gr.Slider(1, 20, value=5, label="Top K")
            search_btn = gr.Button("Search")
            search_out = gr.Markdown()
            search_btn.click(fn=do_search, inputs=[query, k], outputs=search_out)

            limit = gr.Slider(1, 100, value=20, label="Limit")
            list_btn = gr.Button("List Recent")
            list_out = gr.Markdown()
            list_btn.click(fn=do_list, inputs=[limit], outputs=list_out)

        with gr.Tab("Stats"):
            stats_btn = gr.Button("Refresh")
            stats_out = gr.Markdown()
            stats_btn.click(fn=do_stats, outputs=stats_out)

    app.launch(server_port=port, share=share)
