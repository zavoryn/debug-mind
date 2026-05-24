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
from debug_mind.schemas import BugCase, DiagnosisResult, Severity, BugStatus

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

# ── Pre-recorded demo scenarios ────────────────────────────────────────────
# Each scenario shows the FULL loop: diagnose → save draft → engineer verifies
_DEMO_SCENARIOS = [
    {
        "id": "redis_npe",
        "title": "🔴 Redis 连接池耗尽 → NullPointerException",
        "description": "高峰期 UserService.login 出现 NullPointerException，Redis 日志显示连接超时，所有请求卡死约 30s",
        "environment": {"language": "Java", "framework": "Spring Boot 3.2", "redis": "6.2"},
        # Real BugCase data — actually saved to memory when demo runs
        "bug_case": {
            "title": "高峰期 Redis 连接池耗尽导致 NPE",
            "symptoms": "高峰期 UserService.login 出现 NullPointerException，Redis 日志显示连接超时",
            "error_log": "JedisConnectionException: Could not get a resource from the pool\nNullPointerException at UserService.java:42",
            "root_cause": "Redis 连接池 maxActive 默认值为 8，高并发时连接耗尽，getConnection() 返回 null，代码未做 null 检查导致 NPE",
            "fix_suggestion": (
                "方案1（推荐）：调大连接池 spring.redis.jedis.pool.max-active=50, max-wait=2000ms\n"
                "方案2：在代码层对 connection 加 null guard，try-with-resource 确保释放"
            ),
            "severity": "high",
            "tags": ["redis", "npe", "connection-pool", "spring-boot"],
        },
        # Visual steps for the pre-recorded animation
        "steps": [
            ("thinking", "开始分析 Bug 报告，首先检索历史记忆库中的相似案例。"),
            ("tool_call", {"name": "search_memory", "input": {"query": "redis 连接池耗尽 NullPointerException", "top_k": 3}}),
            ("tool_result", {"name": "search_memory", "result": {
                "found": 2,
                "cases": [
                    {"title": "Redis maxActive 未配置导致 NPE", "score": 0.87, "root_cause": "连接池 maxActive 默认值过小（8），高并发时耗尽", "verified": True, "hit_count": 5},
                    {"title": "Jedis 连接未释放导致泄漏", "score": 0.71, "root_cause": "try-with-resource 未关闭连接", "verified": False, "hit_count": 2},
                ],
            }}),
            ("thinking", (
                "✅ 命中 2 条历史案例，最高相似度 0.87（已验证，命中 5 次）。\n\n"
                "根因几乎确定：`maxActive` 默认值 8 在高并发下耗尽，`getConnection()` 返回 null，"
                "代码未做 null 检查直接调用导致 NPE。"
            )),
            ("save_draft", None),  # handled specially: actually saves to memory
            ("thinking", (
                "⏳ **诊断草稿已保存（状态：未验证）**\n\n"
                "系统不会自动标记为正确——需要工程师将修复方案应用到生产环境，"
                "确认修复有效后再提升为「已验证」，未验证案例的搜索权重仅为已验证案例的 70%。"
            )),
        ],
    },
    {
        "id": "kafka_lag",
        "title": "🟠 Kafka 消费者 Lag 持续增长",
        "description": "订单服务消费者 lag 超过 10 万条，消费速度明显低于生产速度，SQL Timeout 告警频发",
        "environment": {"language": "Java", "kafka": "3.5", "db": "MySQL 8.0", "consumer-threads": "32"},
        "bug_case": {
            "title": "Kafka 消费积压：HikariCP 连接池瓶颈",
            "symptoms": "Kafka consumer lag 持续增长超过 10 万条，伴随 SQLTimeoutException: Timeout waiting for connection from pool",
            "error_log": "SQLTimeoutException: Timeout waiting for connection from pool\nConsumerCoordinator - Offset commit failed on partition orders-0",
            "root_cause": "Kafka consumer 并发线程数（32）远超 HikariCP 连接池上限（默认 10），大量线程阻塞等待 DB 连接，消费吞吐量骤降",
            "fix_suggestion": (
                "方案1：调大 DB 连接池 spring.datasource.hikari.maximum-pool-size=40（>= consumer 线程数）\n"
                "方案2：改为批量消费 @KafkaListener(batch=true)，减少 DB 写入次数"
            ),
            "severity": "high",
            "tags": ["kafka", "lag", "db", "connection-pool", "hikari"],
        },
        "steps": [
            ("thinking", "Kafka 消费积压通常有三类根因：消费慢、Rebalance 频繁、下游依赖瓶颈。先搜索历史案例缩小范围。"),
            ("tool_call", {"name": "search_memory", "input": {"query": "kafka consumer lag 积压 消费慢 DB timeout", "top_k": 3}}),
            ("tool_result", {"name": "search_memory", "result": {
                "found": 1,
                "cases": [
                    {"title": "Kafka 消费者 DB 连接池瓶颈", "score": 0.79, "root_cause": "DB 连接池大小 < consumer 线程数，消费线程阻塞等待连接", "verified": True, "hit_count": 3},
                ],
            }}),
            ("thinking", (
                "日志同时出现 `SQLTimeoutException: Timeout waiting for connection from pool`，"
                "说明瓶颈在 **数据库连接层** 而非 Kafka 本身。\n\n"
                "历史案例（相似度 0.79）印证：consumer 线程数（32）> HikariCP 上限（10），"
                "线程争抢连接，消费速率骤降至约 1/3。"
            )),
            ("save_draft", None),
            ("thinking", (
                "⏳ **诊断草稿已保存（状态：未验证）**\n\n"
                "等待工程师将 `maximum-pool-size` 调整为 40 并重启服务，"
                "观察 lag 是否下降后再来确认修复效果。"
            )),
        ],
    },
]


def _get_memory(memory_dir: str | None = None) -> MemoryStore:
    mem_dir = memory_dir or os.environ.get("DEBUG_MIND_MEMORY_DIR", "memory")
    return MemoryStore(memory_dir=Path(mem_dir))


# ── Formatting helpers ─────────────────────────────────────────────────────

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
        return (
            f"→ 找到 **{found}** 条，最相似: **{top['title']}** "
            f"相似度 `{top['score']:.2f}` {badge} 命中 {top.get('hit_count', 0)} 次"
        )
    elif name == "save_to_memory":
        cid = result.get("case_id", "?")
        return f"→ 草稿已保存 `#{cid}`（状态：⬜ 未验证，等待工程师确认）"
    return f"→ `{json.dumps(result, ensure_ascii=False)[:120]}`"


def _render_demo_state(scenario: dict, steps_done: list[str], finished: bool = False) -> str:
    env_str = " · ".join(f"`{k}={v}`" for k, v in scenario["environment"].items())
    out = (
        f"## {scenario['title']}\n\n"
        f"**Bug 描述：** {scenario['description']}\n\n"
        f"**环境：** {env_str}\n\n"
        f"---\n\n"
        f"### 🤖 Agent 推理过程\n\n"
    )
    for i, s in enumerate(steps_done, 1):
        out += f"**{i}.** {s}\n\n"
    if not finished:
        out += "_⏳ 分析中..._"
    return out


# ── Demo streaming ─────────────────────────────────────────────────────────

def _do_demo_stream(scenario_id: str, memory: MemoryStore):
    """Generator: replay a pre-recorded scenario step by step, actually saving to memory.

    Yields (markdown, case_id) tuples.
    case_id is "" until the save_draft step completes, then becomes the real saved ID.
    """
    import gradio as gr

    scenario = next((s for s in _DEMO_SCENARIOS if s["id"] == scenario_id), None)
    if scenario is None:
        yield "请选择一个演示场景。", ""
        return

    steps_done: list[str] = []
    saved_case_id = ""
    yield _render_demo_state(scenario, steps_done), "", gr.update(visible=False)

    for etype, data in scenario["steps"]:
        time.sleep(0.9)

        if etype == "thinking":
            steps_done.append(f"💭 {data}")
            yield _render_demo_state(scenario, steps_done), saved_case_id, gr.update(visible=False)

        elif etype == "tool_call":
            steps_done.append(_fmt_tool_call(data["name"], data["input"]))
            yield _render_demo_state(scenario, steps_done), saved_case_id, gr.update(visible=False)

        elif etype == "tool_result":
            formatted = _fmt_tool_result(data["name"], data["result"])
            steps_done.append(f"&nbsp;&nbsp;&nbsp;&nbsp;{formatted}")
            yield _render_demo_state(scenario, steps_done), saved_case_id, gr.update(visible=False)

        elif etype == "save_draft":
            # Actually save to memory as unverified draft
            bc = scenario["bug_case"]
            case = BugCase(
                title=bc["title"],
                symptoms=bc["symptoms"],
                error_log=bc.get("error_log", ""),
                root_cause=bc["root_cause"],
                fix_suggestion=bc["fix_suggestion"],
                severity=Severity(bc["severity"]),
                status=BugStatus.ROOT_CAUSE_FOUND,
                tags=bc["tags"],
                environment=scenario["environment"],
                verified=False,  # Critical: starts unverified
            )
            try:
                saved = memory.save(case)
                saved_case_id = saved.id
                steps_done.append(
                    f"🔧 **`save_to_memory`** → 草稿已保存 `#{saved_case_id}`（⬜ **未验证**，搜索权重 ×0.7）"
                )
            except Exception as e:
                steps_done.append(f"🔧 save_to_memory → 保存失败: {e}")
            yield _render_demo_state(scenario, steps_done), saved_case_id, gr.update(visible=False)

    # Final state: show verify row if we have a saved case
    final_md = _render_demo_state(scenario, steps_done, finished=True)
    final_md += (
        "\n---\n\n"
        "### 诊断草稿\n\n"
        f"**根因：** {scenario['bug_case']['root_cause']}\n\n"
        f"**修复建议：** {scenario['bug_case']['fix_suggestion']}\n\n"
        "> ⬜ **状态：未验证** — 请工程师应用修复方案并确认效果后，点击下方按钮完成闭环。"
    )
    show_verify = gr.update(visible=bool(saved_case_id))
    yield final_md, saved_case_id, show_verify


# ── Real AI diagnose (streaming) ───────────────────────────────────────────

def _do_diagnose_stream(description: str, error_log: str, env_text: str, api_key: str, memory: MemoryStore):
    """Generator: run a real diagnosis and stream agent events as formatted markdown.

    Yields (markdown, case_id) tuples. case_id is "" until diagnosis completes.
    """
    import gradio as gr

    if not description.strip():
        yield "⚠️ 请输入 Bug 描述。", "", gr.update(visible=False)
        return

    key = api_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        yield (
            "⚠️ **需要 API Key。**\n\n"
            "请在上方输入你的 Anthropic API Key 以启用 AI 诊断。\n"
            "获取地址: [console.anthropic.com](https://console.anthropic.com)\n\n"
            "**提示：** 先在 **🎬 演示** 标签页体验完整推理流程，无需 Key。",
            "",
            gr.update(visible=False),
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
    final_case_id = ""

    def _render(final_result: DiagnosisResult | None = None) -> str:
        header = "## 🤖 AI 诊断进行中...\n\n" if final_result is None else "## 诊断完成\n\n"
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
                f"**置信度：** `{conf_str}` &nbsp;|&nbsp; **相似案例：** {final_result.similar_cases_found} 条\n\n"
                f"**案例 ID：** `#{final_result.case_id}` （状态：⬜ 未验证）\n\n"
                "> 请应用修复方案后，用下方按钮告知系统修复是否有效。"
            )
        elif not steps and not thinking_buf:
            out += "_⏳ 连接 AI 中..._"
        return out

    yield _render(), "", gr.update(visible=False)

    try:
        for event_type, data in agent.diagnose_stream(description, error_log, env):
            if event_type == "thinking":
                thinking_buf += data
                yield _render(), "", gr.update(visible=False)
            elif event_type == "tool_call":
                steps.append(_fmt_tool_call(data["name"], data["input"]))
                yield _render(), "", gr.update(visible=False)
            elif event_type == "tool_result":
                formatted = _fmt_tool_result(data["name"], data["result"])
                steps.append(f"&nbsp;&nbsp;&nbsp;&nbsp;{formatted}")
                yield _render(), "", gr.update(visible=False)
            elif event_type == "done":
                final_case_id = data.case_id if data.case_id != "unknown" else ""
                yield _render(final_result=data), final_case_id, gr.update(visible=bool(final_case_id))
                return
    except Exception as e:
        yield f"❌ 诊断失败: {e}", "", gr.update(visible=False)


# ── Memory operations ──────────────────────────────────────────────────────

def _do_verify(case_id: str, correct: bool, memory: MemoryStore):
    """Mark a case as verified or rejected. Returns a status message."""
    if not case_id:
        return "⚠️ 没有可验证的案例，请先完成诊断。"
    ok = memory.verify(case_id, correct=correct)
    if not ok:
        return f"⚠️ 案例 `#{case_id}` 不存在（可能已被删除）。"
    if correct:
        return (
            f"✅ **案例 `#{case_id}` 已标记为已验证。**\n\n"
            f"搜索权重从 ×0.7（未验证）提升至 ×1.0（已验证），"
            f"下次遇到相似 Bug 时此案例将优先返回。\n\n"
            f"前往 **🔍 搜索记忆库** 查看效果。"
        )
    else:
        return (
            f"🗑️ **案例 `#{case_id}` 已标记为无效并从知识库移除。**\n\n"
            f"错误的诊断不会保留在向量索引中，不会影响未来搜索结果。"
        )


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
**记忆增强的 AI Bug 诊断智能体** — 诊断草稿经工程师验证后写入知识库，形成持续改善的闭环

> **🎬 演示** 无需 API Key | **🤖 AI 诊断** 需要 Anthropic Key | **🔍 搜索** 直接检索知识库
"""
        )

        # ── Tab 1: Demo ───────────────────────────────────────────────
        with gr.Tab("🎬 演示"):
            gr.Markdown(
                "观看完整闭环：**搜索记忆 → 分析根因 → 保存草稿 → 工程师验证 → 写入知识库**。无需 API Key。"
            )
            scenario_labels = [s["title"] for s in _DEMO_SCENARIOS]
            scenario_radio = gr.Radio(
                choices=scenario_labels,
                value=scenario_labels[0],
                label="选择演示场景",
            )
            demo_btn = gr.Button("▶ 开始演示", variant="primary")
            demo_out = gr.Markdown(value="点击「开始演示」查看完整 Agent 推理过程。")
            demo_case_id_state = gr.State(value="")

            # Verification row — hidden until demo completes with a saved case
            with gr.Row(visible=False) as demo_verify_row:
                gr.Markdown("### 🔧 工程师验证\n应用修复方案后，确认效果：")
                demo_verify_yes = gr.Button("✅ 修复有效，写入知识库", variant="primary")
                demo_verify_no = gr.Button("❌ 方案无效，移除诊断", variant="stop")
            demo_verify_result = gr.Markdown(visible=False)

            def _run_demo(label: str):
                sid = next(
                    (s["id"] for s in _DEMO_SCENARIOS if s["title"] == label),
                    _DEMO_SCENARIOS[0]["id"],
                )
                yield from _do_demo_stream(sid, memory)

            demo_btn.click(
                fn=_run_demo,
                inputs=[scenario_radio],
                outputs=[demo_out, demo_case_id_state, demo_verify_row],
            )

            def _demo_verify(case_id: str, correct: bool):
                msg = _do_verify(case_id, correct, memory)
                return gr.update(value=msg, visible=True)

            demo_verify_yes.click(
                fn=lambda cid: _demo_verify(cid, True),
                inputs=[demo_case_id_state],
                outputs=[demo_verify_result],
            )
            demo_verify_no.click(
                fn=lambda cid: _demo_verify(cid, False),
                inputs=[demo_case_id_state],
                outputs=[demo_verify_result],
            )

        # ── Tab 2: Search ─────────────────────────────────────────────
        with gr.Tab("🔍 搜索记忆库"):
            gr.Markdown(
                "搜索历史 Bug 诊断知识库。"
                "已验证案例（✅）搜索权重 ×1.0，未验证（⬜）×0.7。"
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
                "AI Agent 实时推理：**检索记忆 → 分析日志 → 输出根因 → 保存草稿**。"
                "诊断完成后，请确认修复效果以完成闭环。需要 Anthropic API Key。"
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
            diag_case_id_state = gr.State(value="")

            # Verification row — hidden until diagnosis completes
            with gr.Row(visible=False) as diag_verify_row:
                gr.Markdown("### 🔧 应用修复后，确认效果：")
                diag_verify_yes = gr.Button("✅ 修复有效，写入知识库", variant="primary")
                diag_verify_no = gr.Button("❌ 方案无效，移除诊断", variant="stop")
            diag_verify_result = gr.Markdown(visible=False)

            def _run_diagnose(description, error_log, env_text, api_key_val):
                yield from _do_diagnose_stream(description, error_log, env_text, api_key_val, memory)

            diagnose_btn.click(
                fn=_run_diagnose,
                inputs=[desc, log, env, api_key],
                outputs=[diagnose_out, diag_case_id_state, diag_verify_row],
            )

            diag_verify_yes.click(
                fn=lambda cid: gr.update(value=_do_verify(cid, True, memory), visible=True),
                inputs=[diag_case_id_state],
                outputs=[diag_verify_result],
            )
            diag_verify_no.click(
                fn=lambda cid: gr.update(value=_do_verify(cid, False, memory), visible=True),
                inputs=[diag_case_id_state],
                outputs=[diag_verify_result],
            )

        # ── Tab 4: Stats ──────────────────────────────────────────────
        with gr.Tab("📊 统计"):
            stats_btn = gr.Button("🔄 刷新统计")
            stats_out = gr.Markdown()
            stats_btn.click(fn=do_stats, outputs=stats_out)
            app.load(fn=do_stats, outputs=stats_out)

    app.launch(server_port=port, share=share)
