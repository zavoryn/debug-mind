"""Embedding function abstraction — pluggable embedding providers.

Supports: default (ChromaDB built-in), voyage, openai, bge.
Provider is selected via DEBUG_MIND_EMBEDDING env var or explicit parameter.
If the chosen provider lacks dependencies or API keys, falls back to default.
"""

from __future__ import annotations

import os
import warnings
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingFunction(Protocol):
    """Protocol for embedding functions compatible with ChromaDB."""

    def __call__(self, texts: list[str]) -> list[list[float]]: ...


def default_embedding() -> EmbeddingFunction:
    """Return ChromaDB's built-in default embedding (all-MiniLM-L6-v2)."""
    import chromadb.utils.embedding_functions as ef

    return ef.DefaultEmbeddingFunction()


def make_embedding(provider: str | None = None) -> EmbeddingFunction:
    """Build an embedding function by provider name.

    provider=None reads DEBUG_MIND_EMBEDDING env var (default: 'default').
    Falls back to default on missing deps/keys with a warning.
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
        import voyageai

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
        import openai

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
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("BAAI/bge-m3")

        def embed(texts: list[str]) -> list[list[float]]:
            embeddings = model.encode(texts, normalize_embeddings=True)
            return embeddings.tolist()

        return embed
    except Exception as e:
        warnings.warn(f"BGE embedding unavailable ({e}), using default", stacklevel=2)
        return default_embedding()
