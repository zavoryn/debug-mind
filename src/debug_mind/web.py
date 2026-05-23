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
    "redis 连接池耗尽导致 NullPointerException",
    "Spring Boot 启动时循环依赖",
    "Kafka 消费者 lag 持续增长",
    "MySQL 死锁导致事务回滚",
    "Node.js 内存泄漏 heap out of memory",
    "线程池拒绝策略耗尽",
    "Docker 容器 OOM 被杀",
    "CORS 跨域预检请求被拦截",
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
            return "请输入搜索关键词。"
        results = memory.search(query, top_k=int(top_k))
        if not results:
            return "未找到匹配的案例。"
        lines = []
        for i, r in enumerate(results, 1):
            c = r.case
            badge = "✅ 已验证" if c.verified else "⬜ 未验证"
            severity_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(
                c.severity.value, "⚪"
            )
            tags = " · ".join(f"`{t}`" for t in c.tags[:5])
            lines.append(
                f"### {i}. {c.title}\n"
                f"{severity_emoji} **{c.severity.value.upper()}** | {badge} | "
                f"相似度: **{r.score:.3f}** | 命中次数: {c.hit_count}\n\n"
                f"**根因:** {c.root_cause}\n\n"
                f"**修复建议:** {c.fix_suggestion}\n\n"
                f"**标签:** {tags or '—'}"
            )
        return "\n\n---\n\n".join(lines)

    def do_list(limit: int = 20):
        cases = memory.list_recent(limit=int(limit))
        if not cases:
            return "记忆库中暂无案例。"
        lines = [f"**记忆库中共有 {len(cases)} 个案例：**\n"]
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
            return "⚠️ 请输入 Bug 描述。"
        key = api_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            return (
                "⚠️ **需要 API Key。**\n\n"
                "请在上方输入你的 Anthropic API Key 以启用 AI 诊断。\n"
                "获取地址: [console.anthropic.com](https://console.anthropic.com)\n\n"
                "**提示:** 你可以先在 **🔍 搜索记忆库** 标签页中浏览知识库，无需 API Key。"
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
            return f"❌ 诊断失败: {e}"

        steps_md = "\n".join(f"{i}. {s}" for i, s in enumerate(result.diagnosis_steps, 1))
        return (
            f"## 诊断结果\n\n"
            f"**根因:** {result.root_cause}\n\n"
            f"**修复建议:** {result.fix_suggestion}\n\n"
            f"**置信度:** {result.confidence:.0%} | "
            f"**相似案例数:** {result.similar_cases_found}\n\n"
            f"**诊断步骤:**\n{steps_md}"
        )

    # ── Stats tab ─────────────────────────────────────────────────
    def do_stats():
        s = memory.stats()
        top_tags_md = " · ".join(f"`{tag}` ({cnt})" for tag, cnt in s.top_tags[:8])
        severity_md = " | ".join(
            f"{k}: {v}" for k, v in sorted(s.by_severity.items(), key=lambda x: -x[1])
        )
        severity_labels = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}
        severity_cn = " | ".join(
            f"{severity_labels.get(k, k)}: {v}"
            for k, v in sorted(s.by_severity.items(), key=lambda x: -x[1])
        )
        return (
            f"## 记忆库统计\n\n"
            f"- **案例总数:** {s.total_cases}\n"
            f"- **按严重度:** {severity_cn or '—'}\n"
            f"- **陈旧案例:** {s.stale_count}（超过 30 天未命中）\n"
            f"- **平均命中次数:** {s.avg_hit_rate:.2f}\n\n"
            f"**热门标签:** {top_tags_md or '—'}"
        )

    # ── Build UI ──────────────────────────────────────────────────
    with gr.Blocks(
        title="DebugMind — AI Bug 诊断智能体",
        theme=gr.themes.Soft(),
    ) as app:
        gr.Markdown(
            """# 🧠 DebugMind
**记忆增强的 AI Bug 诊断智能体**

每次诊断都会存入知识库，见过的 Bug 越多，下次诊断越快。
**🔍 搜索记忆库** 标签页无需 API Key，点击下方示例即可体验。
"""
        )

        with gr.Tab("🔍 搜索记忆库"):
            gr.Markdown(
                "搜索历史 Bug 诊断知识库。"
                "结果按语义相似度 + 关键词匹配 + 验证状态综合排序。"
            )
            with gr.Row():
                query = gr.Textbox(
                    label="搜索关键词",
                    placeholder="redis 连接池耗尽 NullPointerException...",
                    scale=4,
                )
                top_k = gr.Slider(1, 10, value=3, step=1, label="返回条数", scale=1)
            search_btn = gr.Button("🔍 搜索", variant="primary")
            gr.Examples(
                examples=[[q] for q in _EXAMPLE_QUERIES],
                inputs=[query],
                fn=lambda q, k=3: do_search(q, k),
                outputs=search_out,
                label="示例查询 — 点击后自动搜索",
                run_on_click=True,
            )
            search_out = gr.Markdown()

            # Auto-search when example is clicked
            query.submit(fn=do_search, inputs=[query, top_k], outputs=search_out)
            search_btn.click(fn=do_search, inputs=[query, top_k], outputs=search_out)

            gr.Markdown("---")
            gr.Markdown("### 记忆库中的全部案例")
            limit = gr.Slider(5, 50, value=20, step=5, label="显示条数")
            list_btn = gr.Button("📋 列出全部")
            list_out = gr.Markdown()
            list_btn.click(fn=do_list, inputs=[limit], outputs=list_out)

        with gr.Tab("🤖 AI 诊断"):
            gr.Markdown(
                "使用 AI 智能体诊断 Bug。需要 **Anthropic API Key**。"
                "智能体会先搜索记忆库，再结合上下文分析根因。"
            )
            api_key = gr.Textbox(
                label="Anthropic API Key",
                placeholder="sk-ant-...",
                type="password",
                info="仅用于本次请求，不会被保存。",
            )
            desc = gr.Textbox(
                label="Bug 描述",
                lines=3,
                placeholder="高峰期 UserService.login 出现 NullPointerException，日志中有 Redis 错误...",
            )
            with gr.Row():
                log = gr.Textbox(label="错误日志（可选）", lines=6, scale=3)
                env = gr.Textbox(
                    label="环境信息（可选）",
                    lines=6,
                    placeholder="language=java\nframework=Spring Boot 3.2\njdk=17",
                    scale=1,
                )
            diagnose_btn = gr.Button("🤖 开始诊断", variant="primary")
            diagnose_out = gr.Markdown()
            diagnose_btn.click(
                fn=do_diagnose,
                inputs=[desc, log, env, api_key],
                outputs=diagnose_out,
            )

        with gr.Tab("📊 统计"):
            stats_btn = gr.Button("🔄 刷新统计")
            stats_out = gr.Markdown()
            stats_btn.click(fn=do_stats, outputs=stats_out)
            app.load(fn=do_stats, outputs=stats_out)

    app.launch(server_port=port, share=share)
