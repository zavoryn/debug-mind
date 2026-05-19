"""Reranker abstraction — post-retrieval result reordering.

Provides IdentityReranker (no-op) and LLMReranker (uses Claude Haiku
to score each candidate). The default is IdentityReranker to keep
zero-config behavior unchanged.
"""

from __future__ import annotations

import json
import os
from typing import Protocol, runtime_checkable

from debug_mind.schemas import SearchResult


@runtime_checkable
class Reranker(Protocol):
    """Protocol for reranking search results."""

    def rerank(self, query: str, results: list[SearchResult], top_k: int) -> list[SearchResult]: ...


class IdentityReranker:
    """No-op reranker — returns results unchanged."""

    def rerank(self, query: str, results: list[SearchResult], top_k: int) -> list[SearchResult]:
        return results[:top_k]


class LLMReranker:
    """Reranks results using Claude Haiku to score each candidate 1-10.

    max_candidates: truncate input to this many before scoring (default 10).
    """

    def __init__(self, max_candidates: int = 10, model: str = "claude-haiku-4-5-20251001"):
        self.max_candidates = max_candidates
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        return self._client

    def rerank(self, query: str, results: list[SearchResult], top_k: int) -> list[SearchResult]:
        if not results:
            return []

        candidates = results[: self.max_candidates]

        prompt = self._build_prompt(query, candidates)
        client = self._get_client()

        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            scores = self._parse_scores(response.content[0].text, len(candidates))
        except Exception:
            # On any failure, return original order
            return results[:top_k]

        scored = list(zip(candidates, scores))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [r for r, _ in scored[:top_k]]

    def _build_prompt(self, query: str, candidates: list[SearchResult]) -> str:
        lines = [
            "Rate how relevant each bug case is to the query on a scale of 1-10.",
            "Return ONLY a JSON array of integers, no other text.",
            f"Query: {query}",
            "",
        ]
        for i, r in enumerate(candidates):
            lines.append(f"[{i}] {r.case.title} | {r.case.root_cause[:100]}")
        lines.append("")
        lines.append("JSON array of scores:")
        return "\n".join(lines)

    def _parse_scores(self, text: str, expected_count: int) -> list[float]:
        try:
            # Try to extract JSON array from the response
            text = text.strip()
            start = text.index("[")
            end = text.rindex("]") + 1
            scores = json.loads(text[start:end])
            if len(scores) != expected_count:
                return [5.0] * expected_count
            return [float(s) for s in scores]
        except Exception:
            return [5.0] * expected_count


def make_reranker() -> Reranker:
    """Build a reranker from the DEBUG_MIND_RERANK env var (none|llm)."""
    provider = os.environ.get("DEBUG_MIND_RERANK", "none").lower().strip()

    if provider == "llm":
        return LLMReranker()
    return IdentityReranker()
