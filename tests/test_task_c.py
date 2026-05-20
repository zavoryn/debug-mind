"""Tests for Task C — Embedding pluggability and Reranker."""

from __future__ import annotations

import warnings
from unittest.mock import MagicMock


from debug_mind.memory.embeddings import make_embedding
from debug_mind.memory.rerank import IdentityReranker, LLMReranker, make_reranker
from debug_mind.schemas import BugCase, SearchResult


class TestMakeEmbedding:
    def test_unknown_provider_falls_back_with_warning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            fn = make_embedding("nonexistent-provider")
            assert len(w) == 1
            assert "falling back" in str(w[0].message).lower()
        # Should still be callable
        assert fn is not None

    def test_default_provider(self):
        fn = make_embedding("default")
        assert fn is not None

    def test_none_provider_uses_env(self, monkeypatch):
        monkeypatch.setenv("DEBUG_MIND_EMBEDDING", "default")
        fn = make_embedding(None)
        assert fn is not None

    def test_voyage_fallback_without_key(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _fn = make_embedding("voyage")
            assert any("using default" in str(x.message).lower() for x in w)

    def test_openai_fallback_without_key(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _fn = make_embedding("openai")
            assert any("using default" in str(x.message).lower() for x in w)

    def test_bge_fallback_without_package(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _fn = make_embedding("bge")
            assert any("using default" in str(x.message).lower() for x in w)


class TestIdentityReranker:
    def test_returns_same_order(self):
        reranker = IdentityReranker()
        results = [
            SearchResult(case=BugCase(title="a", symptoms="a"), score=0.9),
            SearchResult(case=BugCase(title="b", symptoms="b"), score=0.8),
        ]
        out = reranker.rerank("query", results, top_k=2)
        assert len(out) == 2
        assert out[0].case.title == "a"

    def test_respects_top_k(self):
        reranker = IdentityReranker()
        results = [
            SearchResult(case=BugCase(title="a", symptoms="a"), score=0.9),
            SearchResult(case=BugCase(title="b", symptoms="b"), score=0.8),
            SearchResult(case=BugCase(title="c", symptoms="c"), score=0.7),
        ]
        out = reranker.rerank("query", results, top_k=2)
        assert len(out) == 2

    def test_empty_input(self):
        reranker = IdentityReranker()
        assert reranker.rerank("query", [], top_k=5) == []


class TestLLMReranker:
    def test_rerank_with_mock_client(self):
        reranker = LLMReranker()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="[8, 3, 6]")]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        reranker._client = mock_client

        results = [
            SearchResult(
                case=BugCase(title="case-a", symptoms="a", root_cause="redis pool"), score=0.9
            ),
            SearchResult(
                case=BugCase(title="case-b", symptoms="b", root_cause="memory leak"), score=0.8
            ),
            SearchResult(
                case=BugCase(title="case-c", symptoms="c", root_cause="deadlock"), score=0.7
            ),
        ]

        out = reranker.rerank("redis connection pool exhausted", results, top_k=3)

        assert len(out) == 3
        # Score [8, 3, 6] → sorted: index 0 (8), index 2 (6), index 1 (3)
        assert out[0].case.title == "case-a"  # score 8
        assert out[1].case.title == "case-c"  # score 6
        assert out[2].case.title == "case-b"  # score 3

    def test_rerank_handles_api_failure(self):
        reranker = LLMReranker()
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")
        reranker._client = mock_client

        results = [
            SearchResult(case=BugCase(title="a", symptoms="a"), score=0.9),
            SearchResult(case=BugCase(title="b", symptoms="b"), score=0.8),
        ]

        out = reranker.rerank("query", results, top_k=2)
        assert len(out) == 2
        # On failure, returns original order
        assert out[0].case.title == "a"


class TestMakeReranker:
    def test_default_is_identity(self):
        r = make_reranker()
        assert isinstance(r, IdentityReranker)

    def test_none_env_is_identity(self, monkeypatch):
        monkeypatch.setenv("DEBUG_MIND_RERANK", "none")
        r = make_reranker()
        assert isinstance(r, IdentityReranker)

    def test_llm_env_creates_llm_reranker(self, monkeypatch):
        monkeypatch.setenv("DEBUG_MIND_RERANK", "llm")
        r = make_reranker()
        assert isinstance(r, LLMReranker)
