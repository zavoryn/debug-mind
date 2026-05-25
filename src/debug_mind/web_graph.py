"""Case relationship graph builder (P6-5).

Why this exists:
Phase 5 added `MemoryStore.link(case_a, case_b, relation)` but nothing in the
Gradio UI surfaced the resulting graph. A picture of "this NPE is a variant
of that connection-pool exhaustion" tells a much more compelling story in a
demo than reading a list of case IDs. This module renders the in-memory case
graph as a pyvis HTML widget that the web Tab embeds via `gr.HTML`.

Sibling-module placement (web_graph.py next to web.py) is deliberate: the
web layer is currently a single module, and converting it into a package
purely for this file would touch many imports for no semantic benefit.
"""

from __future__ import annotations

from typing import Any

from debug_mind.memory.store import MemoryStore
from debug_mind.observability.logger import get_logger

_log = get_logger("web_graph")

_RELATION_COLORS: dict[str, str] = {
    "variant": "#3b82f6",     # blue   — same root cause, different surface
    "caused_by": "#ef4444",   # red    — A → B causal chain
    "fixed_by": "#10b981",    # green  — fix applied
    "related": "#94a3b8",     # gray   — loose association
}

_EMPTY_HTML = (
    "<div style='padding:24px;font-family:sans-serif;color:#64748b;text-align:center;'>"
    "<p>📭 暂无案例。先运行 <code>debug-mind diagnose</code> 让 agent 写入一些案例，"
    "或在「演示」Tab 跑几次场景。</p>"
    "</div>"
)


def build_case_graph(
    memory: MemoryStore,
    max_nodes: int = 50,
    height: str = "600px",
) -> str:
    """Build an HTML/JS string visualising the case-link graph of one namespace.

    The graph shows the `max_nodes` most-recent cases as dots:
      - green = verified, gray = unverified
      - size scaled by verified status (verified are larger)
    Edges come from `BugCase.links` (the Phase-5 link/unlink mechanism),
    coloured by relation type (variant/caused_by/fixed_by/related).

    Returns an HTML string suitable for `gradio.HTML`. Renders a friendly
    empty-state if the memory has no cases yet.
    """
    cases = memory.list_recent(limit=max_nodes)
    if not cases:
        return _EMPTY_HTML

    try:
        from pyvis.network import Network
    except ImportError:
        _log.warning("pyvis not installed — graph viz disabled")
        return (
            "<div style='padding:24px;color:#ef4444;'>pyvis 未安装。"
            "请运行 <code>pip install pyvis</code> 后刷新。</div>"
        )

    net = Network(
        height=height,
        width="100%",
        bgcolor="#ffffff",
        font_color="#0f172a",
        notebook=False,
        cdn_resources="in_line",
        directed=False,
    )

    case_by_id = {c.id: c for c in cases}

    for c in cases:
        color = "#22c55e" if c.verified else "#94a3b8"
        size = 22 if c.verified else 14
        # Tooltip — pyvis renders HTML in node `title`
        verified_str = "✅ verified" if c.verified else "⬜ unverified"
        tooltip = (
            f"<b>{_escape_html(c.title)}</b><br>"
            f"severity: {c.severity.value}<br>"
            f"id: {c.id}<br>"
            f"hit_count: {c.hit_count}<br>"
            f"{verified_str}"
        )
        label = c.title[:38] + ("…" if len(c.title) > 38 else "")
        net.add_node(
            c.id,
            label=label,
            title=tooltip,
            color=color,
            shape="dot",
            size=size,
        )

    # De-duplicate edges: link A→B and B→A are stored on both nodes (see
    # MemoryStore.link). We collapse to one undirected edge per relation.
    seen_edges: set[tuple[str, str, str]] = set()
    for c in cases:
        for link in c.links:
            target = link.get("case_id")
            rel = link.get("relation", "related")
            if not target or target not in case_by_id:
                continue
            key = (*sorted([c.id, target]), rel)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            net.add_edge(
                c.id,
                target,
                color=_RELATION_COLORS.get(rel, "#999"),
                title=rel,
                label=rel,
                font={"size": 10, "color": "#64748b"},
            )

    # Subtle physics — settles fast, then locks
    net.set_options(
        """{
          "physics": {
            "enabled": true,
            "stabilization": {"iterations": 120, "fit": true},
            "barnesHut": {"springLength": 140}
          },
          "interaction": {"hover": true, "tooltipDelay": 100}
        }"""
    )

    legend = _legend_html()
    try:
        graph_html = net.generate_html(notebook=False)
    except Exception as e:
        _log.warning("graph html generation failed", extra={"err": str(e)})
        return (
            f"<div style='padding:24px;color:#ef4444;'>图谱生成失败：{_escape_html(str(e))}</div>"
        )
    return legend + graph_html


def _legend_html() -> str:
    parts = []
    for rel, color in _RELATION_COLORS.items():
        parts.append(
            f"<span style='display:inline-block;width:12px;height:12px;background:{color};"
            f"border-radius:50%;margin-right:6px;'></span>{rel}"
        )
    return (
        "<div style='padding:12px 16px;font-size:13px;color:#475569;font-family:sans-serif;"
        "display:flex;gap:18px;flex-wrap:wrap;align-items:center;'>"
        "<b>图例：</b>"
        "<span style='display:inline-block;width:12px;height:12px;background:#22c55e;"
        "border-radius:50%;margin-right:6px;'></span>verified  "
        "<span style='display:inline-block;width:12px;height:12px;background:#94a3b8;"
        "border-radius:50%;margin-right:6px;'></span>unverified  "
        "<b>关系：</b>" + "  ".join(parts) +
        "</div>"
    )


def _escape_html(text: Any) -> str:
    s = str(text)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
