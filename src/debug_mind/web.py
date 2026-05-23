"""Gradio web UI for DebugMind — try it in the browser.

Start with: debug-mind web
Optional dependency: pip install gradio
"""

from __future__ import annotations

import os
from pathlib import Path

from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import DiagnosisResult

_EXAMPLE_QUERIES = [
    "redis connection pool exhausted NullPointerException",
    "Spring Boot circular dependency on startup",
    "Kafka consumer lag growing indefinitely",
    "MySQL deadlock transaction rollback",
    "Node.js memory leak heap out of memory",
    "thread pool rejection policy exhausted",
    "docker container OOM killed",
    "CORS error preflight request blocked",
]


def _get_memory(memory_dir: str | None = None) -> MemoryStore:
    mem_dir = memory_dir or os.environ.get("DEBUG_MIND_MEMORY_DIR", "memory")
    return MemoryStore(memory_dir=Path(mem_dir))


def launch_ui(port: int = 7860, share: bool = False, memory_dir: str | None = None) -> None:
    """Launch the Gradio web interface."""
    try:
        import gradio as gr
    except ImportError:
        print("Gradio is required for the web UI. Install with: pip install debug-mind[web]")
        return

    memory = _get_memory(memory_dir)

    # ── Search Memory tab ─────────────────────────────────────────
    def do_search(query: str, top_k: int = 5):
        if not query.strip():
            return "Please enter a search query."
        results = memory.search(query, top_k=int(top_k))
        if not results:
            return "No matching cases found in memory."
        lines = []
        for i, r in enumerate(results, 1):
            c = r.case
            badge = "✅ verified" if c.verified else "⬜ unverified"
            severity_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(
                c.severity.value, "⚪"
            )
            tags = " · ".join(f"`{t}`" for t in c.tags[:5])
            lines.append(
                f"### {i}. {c.title}\n"
                f"{severity_emoji} **{c.severity.value.upper()}** | {badge} | score: **{r.score:.3f}** | hits: {c.hit_count}\n\n"
                f"**Root Cause:** {c.root_cause}\n\n"
                f"**Fix:** {c.fix_suggestion}\n\n"
                f"**Tags:** {tags or '—'}"
            )
        return "\n\n---\n\n".join(lines)

    def do_list(limit: int = 20):
        cases = memory.list_recent(limit=int(limit))
        if not cases:
            return "No cases in memory yet."
        lines = [f"**{len(cases)} cases in memory:**\n"]
        for c in cases:
            badge = "✅" if c.verified else "⬜"
            severity_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(
                c.severity.value, "⚪"
            )
            lines.append(f"- {badge} {severity_emoji} **{c.title}** — `{c.id}`")
        return "\n".join(lines)

    # ── Diagnose tab ──────────────────────────────────────────────
    def do_diagnose(description: str, error_log: str, env_text: str, api_key: str):
        if not description.strip():
            return "⚠️ Please enter a bug description."
        key = api_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            return (
                "⚠️ **API key required.**\n\n"
                "Enter your Anthropic API key in the field above to enable AI diagnosis.\n"
                "You can get one at [console.anthropic.com](https://console.anthropic.com).\n\n"
                "**Tip:** Use the **Search Memory** tab to explore the knowledge base without an API key."
            )
        os.environ["ANTHROPIC_API_KEY"] = key
        from debug_mind.agent import DiagnosticAgent

        env: dict[str, str] = {}
        if env_text.strip():
            for line in env_text.strip().split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
        try:
            agent = DiagnosticAgent(memory=memory)
            result: DiagnosisResult = agent.diagnose(
                description, error_log=error_log, environment=env
            )
        except Exception as e:
            return f"❌ Diagnosis failed: {e}"

        steps_md = "\n".join(f"{i}. {s}" for i, s in enumerate(result.diagnosis_steps, 1))
        return (
            f"## Diagnosis Result\n\n"
            f"**Root Cause:** {result.root_cause}\n\n"
            f"**Fix Suggestion:** {result.fix_suggestion}\n\n"
            f"**Confidence:** {result.confidence:.0%} | "
            f"**Similar Cases Found:** {result.similar_cases_found}\n\n"
            f"**Diagnosis Steps:**\n{steps_md}"
        )

    # ── Stats tab ─────────────────────────────────────────────────
    def do_stats():
        s = memory.stats()
        verified_count = s.by_status.get("fixed", 0) + s.by_status.get("root_cause_found", 0)
        top_tags_md = " · ".join(f"`{tag}` ({cnt})" for tag, cnt in s.top_tags[:8])
        severity_md = " | ".join(
            f"{k}: {v}" for k, v in sorted(s.by_severity.items(), key=lambda x: -x[1])
        )
        return (
            f"## Memory Stats\n\n"
            f"- **Total Cases:** {s.total_cases}\n"
            f"- **By Severity:** {severity_md or '—'}\n"
            f"- **Stale Cases:** {s.stale_count} (unused >30 days)\n"
            f"- **Avg Hit Rate:** {s.avg_hit_rate:.2f}\n\n"
            f"**Top Tags:** {top_tags_md or '—'}"
        )

    # ── Build UI ──────────────────────────────────────────────────
    with gr.Blocks(
        title="DebugMind — AI Bug Diagnosis Agent",
        theme=gr.themes.Soft(),
    ) as app:
        gr.Markdown(
            """# 🧠 DebugMind
**AI Bug Diagnosis Agent with Experiential Memory**

Every diagnosis is saved to a knowledge base. The more bugs it sees, the faster it gets.
The **Search Memory** tab works instantly — no API key needed. Try one of the examples below.
"""
        )

        with gr.Tab("🔍 Search Memory"):
            gr.Markdown(
                "Search the knowledge base of past bug diagnoses. "
                "Results are ranked by semantic similarity + keyword match + verification status."
            )
            with gr.Row():
                query = gr.Textbox(
                    label="Search Query",
                    placeholder="redis connection pool exhausted...",
                    scale=4,
                )
                top_k = gr.Slider(1, 10, value=3, step=1, label="Top K", scale=1)
            search_btn = gr.Button("Search", variant="primary")
            gr.Examples(
                examples=[[q] for q in _EXAMPLE_QUERIES],
                inputs=query,
                label="Example queries — click to try",
            )
            search_out = gr.Markdown()
            search_btn.click(fn=do_search, inputs=[query, top_k], outputs=search_out)

            gr.Markdown("---")
            gr.Markdown("### All Cases in Memory")
            limit = gr.Slider(5, 50, value=20, step=5, label="Show N cases")
            list_btn = gr.Button("List All")
            list_out = gr.Markdown()
            list_btn.click(fn=do_list, inputs=[limit], outputs=list_out)

        with gr.Tab("🤖 Diagnose"):
            gr.Markdown(
                "Diagnose a bug using the AI agent. Requires an **Anthropic API key**. "
                "The agent will search memory first, then analyze your codebase context."
            )
            api_key = gr.Textbox(
                label="Anthropic API Key",
                placeholder="sk-ant-...",
                type="password",
                info="Your key is used only for this request and not stored.",
            )
            desc = gr.Textbox(
                label="Bug Description",
                lines=3,
                placeholder="NullPointerException in UserService.login during peak traffic...",
            )
            with gr.Row():
                log = gr.Textbox(label="Error Log (optional)", lines=6, scale=3)
                env = gr.Textbox(
                    label="Environment (optional)",
                    lines=6,
                    placeholder="language=java\nframework=Spring Boot 3.2\njdk=17",
                    scale=1,
                )
            diagnose_btn = gr.Button("Diagnose", variant="primary")
            diagnose_out = gr.Markdown()
            diagnose_btn.click(
                fn=do_diagnose,
                inputs=[desc, log, env, api_key],
                outputs=diagnose_out,
            )

        with gr.Tab("📊 Stats"):
            stats_btn = gr.Button("Refresh Stats")
            stats_out = gr.Markdown()
            stats_btn.click(fn=do_stats, outputs=stats_out)
            app.load(fn=do_stats, outputs=stats_out)

    app.launch(server_port=port, share=share)
