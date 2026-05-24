"""Gradio web UI for DebugMind — try it in the browser.

Start with: debug-mind web
Optional dependency: pip install gradio
"""

from __future__ import annotations

import json
import os
import time
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

# ── Pre-recorded demo scenarios (no API key needed) ────────────────────────
_DEMO_SCENARIOS = [
    {
        "id": "redis_npe",
        "title": "🔴 Redis 连接池耗尽 → NullPointerException",
        "description": "高峰期 UserService.login 出现 NullPointerException，Redis 日志显示连接超时，所有请求卡死约 30s",
        "environment": {"language": "Java", "framework": "Spring Boot 3.2", "redis": "6.2"},
        "steps": [
            ("thinking", "开始分析 Bug 报告...首先搜索历史记忆库中的相似案例。"),
            ("tool_call", {"name": "search_memory", "input": {"query": "redis 连接池耗尽 NullPointerException", "top_k": 3}}),
            ("tool_result", {"name": "search_memory", "result": {
                "found": 2,
                "cases": [
                    {"title": "Redis maxActive 未配置导致 NPE", "score": 0.87, "root_cause": "连接池 maxActive 默认值过小（8），高并发时耗尽", "verified": True, "hit_count": 5},
                    {"title": "Jedis 连接未释放导致泄漏", "score": 0.71, "root_cause": "try-with-resource 未正确关闭连接", "verified": False, "hit_count": 2},
                ],
            }}),
            ("thinking", "✅ 命中 2 条历史案例。相似度最高的案例（0.87）已被验证并命中 5 次——这是一个高置信度的参考。\n\n根据案例 #1，根因几乎可以确定：`maxActive` 默认值为 8，在高并发下耗尽后 `getConnection()` 返回 null，代码未做 null 检查直接调用导致 NPE。"),
            ("tool_call", {"name": "save_to_memory", "input": {
                "title": "高峰期 Redis 连接池耗尽导致 NPE",
                "severity": "high",
                "tags": ["redis", "npe", "connection-pool", "spring-boot"],
                "root_cause": "Redis maxActive=8 在高并发下耗尽，getConnection() 返回 null 未被处理",
                "fix_suggestion": "spring.redis.jedis.pool.max-active=50, max-wait=2000ms",
            }}),
            ("tool_result", {"name": "save_to_memory", "result": {"saved": True, "case_id": "a3f2b1c8"}}),
            ("done", {
                "root_cause": "Redis 连接池 `maxActive` 配置过小（默认 8），高并发时连接耗尽，`Jedis.getConnection()` 返回 null，未做 null 检查直接调用导致 NPE。",
                "fix_suggestion": (
                    "**方案 1（推荐）：** 调大连接池\n"
                    "```yaml\nspring:\n  redis:\n    jedis:\n      pool:\n        max-active: 50\n        max-wait: 2000ms\n        max-idle: 20\n```\n\n"
                    "**方案 2：** 在代码层加 null guard\n"
                    "```java\nJedis jedis = pool.getResource();\nif (jedis == null) throw new ServiceUnavailableException(\"Redis unavailable\");\ntry { ... } finally { jedis.close(); }\n```"
                ),
                "confidence": 0.92,
                "similar_cases_found": 2,
            }),
        ],
    },
    {
        "id": "kafka_lag",
        "title": "🟠 Kafka 消费者 Lag 持续增长",
        "description": "订单服务消费者 lag 超过 10 万条，消费速度明显低于生产速度，SQL Timeout 告警频发",
        "environment": {"language": "Java", "kafka": "3.5", "db": "MySQL 8.0", "consumer-threads": "32"},
        "steps": [
            ("thinking", "Kafka 消费积压问题通常有三类根因：消费者处理慢、Rebalance 频繁、或下游依赖瓶颈。先搜索历史案例缩小范围。"),
            ("tool_call", {"name": "search_memory", "input": {"query": "kafka consumer lag 积压 消费慢 DB timeout", "top_k": 3}}),
            ("tool_result", {"name": "search_memory", "result": {
                "found": 1,
                "cases": [
                    {"title": "Kafka 消费者 DB 连接池瓶颈", "score": 0.79, "root_cause": "DB 连接池大小 < consumer 线程数，导致消费线程阻塞等待连接", "verified": True, "hit_count": 3},
                ],
            }}),
            ("thinking", "日志中同时出现 `SQLTimeoutException: Timeout waiting for connection from pool`，说明瓶颈在 **数据库连接层** 而非 Kafka 本身。\n\n历史案例（0.79）印证：消费线程数（32）远大于 DB 连接池上限（默认 10），导致大量线程阻塞等待连接，消费速率骤降。"),
            ("tool_call", {"name": "save_to_memory", "input": {
                "title": "Kafka 消费积压：DB 连接池瓶颈",
                "severity": "high",
                "tags": ["kafka", "lag", "db", "connection-pool", "hikari"],
                "root_cause": "consumer 线程数（32）> HikariCP 连接池上限（10），线程争抢连接导致消费阻塞",
                "fix_suggestion": "maximum-pool-size >= consumer 线程数，或改批量消费",
            }}),
            ("tool_result", {"name": "save_to_memory", "result": {"saved": True, "case_id": "b7e4c219"}}),
            ("done", {
                "root_cause": "Kafka consumer 并发线程数（32）远超 HikariCP 连接池上限（默认 10），大量线程阻塞在 `getConnection()` 等待，消费吞吐量降至约 1/3。",
                "fix_suggestion": (
                    "**方案 1（最快）：** 调大 DB 连接池\n"
                    "```yaml\nspring:\n  datasource:\n    hikari:\n      maximum-pool-size: 40  # >= consumer 线程数\n      connection-timeout: 3000\n```\n\n"
                    "**方案 2（更优）：** 改为批量消费减少 DB 压力\n"
                    "```java\n@KafkaListener(topics = \"orders\", batch = \"true\")\npublic void consume(List<OrderEvent> events) {\n    orderRepo.saveAll(events);  // 批量写一次\n}\n```"
                ),
                "confidence": 0.88,
                "similar_cases_found": 1,
            }),
        ],
    },
]


def _get_memory(memory_dir: str | None = None) -> MemoryStore:
    mem_dir = memory_dir or os.environ.get("DEBUG_MIND_MEMORY_DIR", "memory")
    return MemoryStore(memory_dir=Path(mem_dir))


# ── Demo streaming helpers ─────────────────────────────────────────────────

def _fmt_tool_call(name: str, inp: dict) -> str:
    inp_short = json.dumps(inp, ensure_ascii=False)
    if len(inp_short) > 100:
        inp_short = inp_short[:97] + "..."
    return f"🔧 **`{name}`** `{inp_short}`"


def _fmt_tool_result(name: str, result: dict) -> str:
    if name == "search_memory":
        found = result.get("found", 0)
        cases = result.get("cases", [])
        if not cases:
            return "→ ⚪ 未找到相关案例（首次遇到此类 Bug）"
        top = cases[0]
        badge = "✅ 已验证" if top.get("verified") else "⬜ 未验证"
        return f"→ 找到 **{found}** 条，最相似: **{top['title']}** 相似度 `{top['score']:.2f}` {badge} 命中 {top.get('hit_count', 0)} 次"
    elif name == "save_to_memory":
        cid = result.get("case_id", "?")
        return f"→ ✅ 已写入记忆库 `#{cid}`"
    return f"→ `{json.dumps(result, ensure_ascii=False)[:120]}`"


def _render_demo_state(scenario: dict, steps_done: list[str], done: bool = False) -> str:
    env_str = " · ".join(f"`{k}={v}`" for k, v in scenario["environment"].items())
    out = (
        f"## {scenario['title']}\n\n"
        f"**Bug 描述：** {scenario['description']}\n\n"
        f"**环境：** {env_str}\n\n"
        f"---\n\n"
        f"### 🤖 Agent 推理过程\n\n"
    )
    if steps_done:
        for i, s in enumerate(steps_done, 1):
            out += f"**{i}.** {s}\n\n"
    if not done:
        out += "_⏳ 分析中..._"
    return out


def _do_demo_stream(scenario_id: str):
    """Generator: replay a pre-recorded demo scenario step by step."""
    scenario = next((s for s in _DEMO_SCENARIOS if s["id"] == scenario_id), None)
    if scenario is None:
        yield "请选择一个演示场景。"
        return

    steps_done: list[str] = []
    yield _render_demo_state(scenario, steps_done)

    for etype, data in scenario["steps"]:
        time.sleep(0.9)

        if etype == "thinking":
            steps_done.append(f"💭 {data}")
            yield _render_demo_state(scenario, steps_done)

        elif etype == "tool_call":
            steps_done.append(_fmt_tool_call(data["name"], data["input"]))
            yield _render_demo_state(scenario, steps_done)

        elif etype == "tool_result":
            formatted = _fmt_tool_result(data["name"], data["result"])
            steps_done.append(f"&nbsp;&nbsp;&nbsp;&nbsp;{formatted}")
            yield _render_demo_state(scenario, steps_done)

        elif etype == "done":
            diag = data
            steps_done.append("✅ 诊断完成，结果已写入记忆库")
            out = _render_demo_state(scenario, steps_done, done=True)
            out += (
                "\n---\n\n"
                "### 诊断结果\n\n"
                f"**根因：** {diag['root_cause']}\n\n"
                f"**修复建议：**\n\n{diag['fix_suggestion']}\n\n"
                f"**置信度：** `{diag['confidence']:.0%}` &nbsp;|&nbsp; "
                f"**相似案例：** {diag['similar_cases_found']} 条\n\n"
                "> 💡 此次诊断结果已写入记忆库。下次遇到相似 Bug，Agent 会直接命中此案例，诊断速度大幅提升。"
            )
            yield out
            return


# ── Real AI diagnose (streaming) ───────────────────────────────────────────

def _do_diagnose_stream(description: str, error_log: str, env_text: str, api_key: str, memory: MemoryStore):
    """Generator: run a real diagnosis and stream agent events as formatted markdown."""
    if not description.strip():
        yield "⚠️ 请输入 Bug 描述。"
        return

    key = api_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        yield (
            "⚠️ **需要 API Key。**\n\n"
            "请在上方输入你的 Anthropic API Key 以启用 AI 诊断。\n"
            "获取地址: [console.anthropic.com](https://console.anthropic.com)\n\n"
            "**提示：** 先在 **🎬 演示** 或 **🔍 搜索** 标签页体验，无需 Key。"
        )
        return

    os.environ["ANTHROPIC_API_KEY"] = key

    env: dict[str, str] = {}
    if env_text.strip():
        for line in env_text.strip().split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

    from debug_mind.agent import DiagnosticAgent

    agent = DiagnosticAgent(memory=memory)

    steps: list[str] = []
    thinking_buf = ""
    current_turn = 0

    def _render(final_result: DiagnosisResult | None = None) -> str:
        header = (
            "## 🤖 AI 诊断进行中...\n\n"
            if final_result is None
            else "## 诊断结果\n\n"
        )
        out = header
        if steps:
            out += "### 推理步骤\n\n"
            for i, s in enumerate(steps, 1):
                out += f"**{i}.** {s}\n\n"
            out += "---\n\n"
        if thinking_buf:
            out += "### Agent 分析\n\n" + thinking_buf + "\n\n---\n\n"
        if final_result:
            conf_str = f"{final_result.confidence:.0%}" if final_result.confidence > 0 else "部分"
            out += (
                f"**根因：** {final_result.root_cause}\n\n"
                f"**修复建议：** {final_result.fix_suggestion}\n\n"
                f"**置信度：** `{conf_str}` &nbsp;|&nbsp; **相似案例：** {final_result.similar_cases_found} 条"
            )
        elif not steps and not thinking_buf:
            out += "_⏳ 连接 AI 中..._"
        return out

    yield _render()

    try:
        for event_type, data in agent.diagnose_stream(description, error_log, env):
            if event_type == "turn":
                current_turn = data["turn"]
            elif event_type == "thinking":
                thinking_buf += data
                yield _render()
            elif event_type == "tool_call":
                steps.append(_fmt_tool_call(data["name"], data["input"]))
                yield _render()
            elif event_type == "tool_result":
                formatted = _fmt_tool_result(data["name"], data["result"])
                steps.append(f"&nbsp;&nbsp;&nbsp;&nbsp;{formatted}")
                yield _render()
            elif event_type == "done":
                yield _render(final_result=data)
                return
    except Exception as e:
        yield f"❌ 诊断失败: {e}"


# ── Memory operations ──────────────────────────────────────────────────────

def launch_ui(port: int = 7860, share: bool = False, memory_dir: str | None = None) -> None:
    """Launch the Gradio web interface."""
    try:
        import gradio as gr
    except ImportError:
        print("Gradio is required for the web UI. Install with: pip install debug-mind[web]")
        return

    memory = _get_memory(memory_dir)

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

    def do_stats():
        s = memory.stats()
        top_tags_md = " · ".join(f"`{tag}` ({cnt})" for tag, cnt in s.top_tags[:8])
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

    # ── Build UI ─────────────────────────────────────────────────────────
    with gr.Blocks(
        title="DebugMind — AI Bug 诊断智能体",
        theme=gr.themes.Soft(),
    ) as app:
        gr.Markdown(
            """# 🧠 DebugMind
**记忆增强的 AI Bug 诊断智能体** — 每次诊断都写入知识库，见过的 Bug 越多，下次越快

> **🎬 演示** 无需 API Key | **🤖 AI 诊断** 需要 Anthropic Key | **🔍 搜索** 直接检索知识库
"""
        )

        # ── Tab 1: Demo ───────────────────────────────────────────────
        with gr.Tab("🎬 演示"):
            gr.Markdown(
                "选择一个预录场景，观看 Agent 完整推理过程：**搜索记忆 → 分析 → 写回记忆库**。无需 API Key。"
            )
            scenario_labels = [s["title"] for s in _DEMO_SCENARIOS]
            scenario_radio = gr.Radio(
                choices=scenario_labels,
                value=scenario_labels[0],
                label="选择演示场景",
            )
            demo_btn = gr.Button("▶ 开始演示", variant="primary")
            demo_out = gr.Markdown(value="点击「开始演示」查看 Agent 推理过程。")

            def _run_demo(label: str):
                sid = next(
                    (s["id"] for s in _DEMO_SCENARIOS if s["title"] == label),
                    _DEMO_SCENARIOS[0]["id"],
                )
                yield from _do_demo_stream(sid)

            demo_btn.click(fn=_run_demo, inputs=[scenario_radio], outputs=demo_out)

        # ── Tab 2: Search ─────────────────────────────────────────────
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
            search_out = gr.Markdown()

            query.submit(fn=do_search, inputs=[query, top_k], outputs=search_out)
            search_btn.click(fn=do_search, inputs=[query, top_k], outputs=search_out)

            gr.Examples(
                examples=[[q] for q in _EXAMPLE_QUERIES],
                inputs=[query],
                fn=lambda q, k=3: do_search(q, k),
                outputs=search_out,
                label="示例查询 — 点击后自动搜索",
                run_on_click=True,
            )

            gr.Markdown("---")
            gr.Markdown("### 记忆库中的全部案例")
            limit = gr.Slider(5, 50, value=20, step=5, label="显示条数")
            list_btn = gr.Button("📋 列出全部")
            list_out = gr.Markdown()
            list_btn.click(fn=do_list, inputs=[limit], outputs=list_out)

        # ── Tab 3: AI Diagnose (streaming) ────────────────────────────
        with gr.Tab("🤖 AI 诊断"):
            gr.Markdown(
                "输入 Bug 信息，AI Agent 会实时展示推理过程：**搜索记忆 → 分析日志 → 输出根因 → 写回记忆**。需要 Anthropic API Key。"
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

            def _run_diagnose(description, error_log, env_text, api_key_val):
                yield from _do_diagnose_stream(description, error_log, env_text, api_key_val, memory)

            diagnose_btn.click(
                fn=_run_diagnose,
                inputs=[desc, log, env, api_key],
                outputs=diagnose_out,
            )

        # ── Tab 4: Stats ──────────────────────────────────────────────
        with gr.Tab("📊 统计"):
            stats_btn = gr.Button("🔄 刷新统计")
            stats_out = gr.Markdown()
            stats_btn.click(fn=do_stats, outputs=stats_out)
            app.load(fn=do_stats, outputs=stats_out)

    app.launch(server_port=port, share=share)
