"""Embedding function abstraction — pluggable embedding providers.

Supports: default (ChromaDB built-in when available, trigram-hash otherwise),
voyage, openai, bge.

Provider is selected via DEBUG_MIND_EMBEDDING env var or explicit parameter.
If the chosen provider lacks dependencies or API keys, falls back gracefully.
"""

from __future__ import annotations

import hashlib
import math
import os
import warnings
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingFunction(Protocol):
    """Protocol for embedding functions."""

    def __call__(self, texts: list[str]) -> list[list[float]]: ...


# ── Trigram hash fallback — pure Python, zero deps ────────────────────────────

def _trigram_hash_embedding(texts: list[str]) -> list[list[float]]:
    """Deterministic embedding via character trigram hashing.

    Produces 384-dim vectors (same dimensionality as all-MiniLM-L6-v2).
    Quality is much lower than a real embedding model — use only as last resort.
    Appropriate for CI / offline environments without any model access.
    """
    dim = 384
    vectors = []
    for text in texts:
        vec = [0.0] * dim
        text_lower = text.lower()
        for i in range(len(text_lower) - 2):
            trigram = text_lower[i : i + 3]
            h = int(hashlib.md5(trigram.encode()).hexdigest(), 16)
            idx = h % dim
            vec[idx] += 0.01
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        vectors.append(vec)
    return vectors


def default_embedding() -> EmbeddingFunction:
    """Return the best available embedding function.

    Priority:
    1. ChromaDB's DefaultEmbeddingFunction (all-MiniLM-L6-v2 via ONNX)
       — if ``debug-mind[chroma]`` is installed.
    2. Trigram-hash fallback — pure Python, no extra deps, degraded quality.
    """
    try:
        import chromadb.utils.embedding_functions as ef  # type: ignore[import]

        return ef.DefaultEmbeddingFunction()
    except ImportError:
        warnings.warn(
            "chromadb not installed — using trigram-hash embedding (degraded quality). "
            "For better search: pip install debug-mind[chroma]",
            stacklevel=2,
        )
        return _trigram_hash_embedding


def make_embedding(provider: str | None = None) -> EmbeddingFunction:
    """Build an embedding function by provider name.

    provider=None reads DEBUG_MIND_EMBEDDING env var (default: 'default').
    Falls back to trigram-hash on missing deps/keys with a warning.
    """
    if provider is None:
        provider = os.environ.get("DEBUG_MIND_EMBEDDING", "default")

    provider = provider.lower().strip()

    if provider == "default":
        return default_embedding()
    elif provider == "voyage":
        return _make_voyage()
    elif provider == "openai":
        return _make_openai()
    elif provider == "bge":
        return _make_bge()
    else:
        warnings.warn(
            f"Unknown embedding provider '{provider}', falling back to default",
            stacklevel=2,
        )
        return default_embedding()


def _make_voyage() -> EmbeddingFunction:
    try:
        import voyageai  # type: ignore[import]

        api_key = os.environ.get("VOYAGE_API_KEY")
        if not api_key:
            raise ValueError("VOYAGE_API_KEY not set")
        client = voyageai.Client(api_key=api_key)

        def embed(texts: list[str]) -> list[list[float]]:
            result = client.embed(texts, model="voyage-3")
            return result.embeddings

        return embed
    except Exception as e:
        warnings.warn(f"Voyage embedding unavailable ({e}), using default", stacklevel=2)
        return default_embedding()


def _make_openai() -> EmbeddingFunction:
    try:
        import openai  # type: ignore[import]

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set")
        client = openai.OpenAI(api_key=api_key)

        def embed(texts: list[str]) -> list[list[float]]:
            result = client.embeddings.create(input=texts, model="text-embedding-3-large")
            return [item.embedding for item in result.data]

        return embed
    except Exception as e:
        warnings.warn(f"OpenAI embedding unavailable ({e}), using default", stacklevel=2)
        return default_embedding()


def _make_bge() -> EmbeddingFunction:
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import]

        model = SentenceTransformer("BAAI/bge-m3")

        def embed(texts: list[str]) -> list[list[float]]:
            embeddings = model.encode(texts, normalize_embeddings=True)
            return embeddings.tolist()

        return embed
    except Exception as e:
        warnings.warn(f"BGE embedding unavailable ({e}), using default", stacklevel=2)
        return default_embedding()
