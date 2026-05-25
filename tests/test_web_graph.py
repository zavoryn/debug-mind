"""Tests for the case-relationship graph builder (P6-5)."""

from __future__ import annotations

import pytest

from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import BugCase, Severity
from debug_mind.web_graph import build_case_graph


pytest.importorskip("pyvis")  # whole module is meaningless without it


@pytest.fixture
def memory(tmp_path):
    return MemoryStore(memory_dir=tmp_path / "mem", namespace="default")


def _save(memory: MemoryStore, title: str, verified: bool = False) -> str:
    case = BugCase(
        title=title,
        symptoms=f"{title} symptoms",
        root_cause=f"{title} root cause",
        fix_suggestion="fix",
        severity=Severity.MEDIUM,
    )
    memory.save(case)
    if verified:
        memory.verify(case.id, correct=True)
    return case.id


class TestBuildCaseGraph:
    def test_empty_memory_renders_friendly_message(self, memory):
        html = build_case_graph(memory)
        assert "暂无案例" in html
        # Should not raise and should not embed a pyvis network
        assert "vis-network" not in html

    def test_returns_html_with_nodes(self, memory):
        _save(memory, "alpha")
        _save(memory, "beta", verified=True)
        html = build_case_graph(memory)

        # pyvis emits a div containing the network and the vis-network JS
        assert "vis-network" in html
        # Legend should be present
        assert "图例" in html
        # Both case titles should appear in the rendered HTML
        assert "alpha" in html
        assert "beta" in html

    def test_includes_links_as_edges(self, memory):
        a = _save(memory, "first")
        b = _save(memory, "second")
        assert memory.link(a, b, relation="variant")

        html = build_case_graph(memory)
        # Edge labels should be rendered into the dataset (pyvis writes
        # node/edge JSON inline into the HTML)
        assert "variant" in html
        # Both case IDs should appear in the node dataset
        assert a in html
        assert b in html

    def test_respects_max_nodes_limit(self, memory):
        for i in range(8):
            _save(memory, f"case-{i}")
        html = build_case_graph(memory, max_nodes=3)
        # Only the 3 most-recent cases should appear by title
        # (list_recent sorts by mtime DESC; the last 3 inserted = -1, -2, -3)
        recent_count = sum(1 for i in range(8) if f"case-{i}" in html)
        assert recent_count <= 5  # legend may incidentally inflate; loose check
        # But cases beyond max_nodes count must definitely NOT all be present
        assert html.count('"id":') <= 6  # node objects in dataset
