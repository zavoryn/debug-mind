"""Milvus backend — stub implementation.

Why this exists:
This file is a deliberate placeholder, not a working implementation. It
proves that the StorageBackend interface is genuinely pluggable: dropping
in Milvus is a matter of completing the methods below, not refactoring
the rest of the codebase. The selection rationale (when to actually
adopt Milvus vs the default SQLite / Chroma backends) lives in
`docs/MILVUS.md`.

The stub:
  - imports cleanly without pymilvus installed
  - constructs successfully *without* attempting any network connection
    (so a developer browsing the code can instantiate the class to look
    at it)
  - raises `NotImplementedError` from every protocol method, with a
    pointer to docs/MILVUS.md
  - raises a clear `ImportError` *only* when initialize() is called
    without pymilvus on the path

Wiring this into the backend selector in `MemoryStore._create_backend`
is intentionally out of scope for the stub — leaving that as a one-line
change makes the eventual real adopter's diff small and reviewable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from debug_mind.memory.backends.base import StorageBackend

_DOCS_URL = "docs/MILVUS.md"


class MilvusBackend(StorageBackend):
    """Stub Milvus backend — see docs/MILVUS.md for the production checklist."""

    def __init__(
        self,
        memory_dir: Path,
        host: str = "localhost",
        port: int = 19530,
        collection: str = "debug_mind_cases",
    ) -> None:
        # Stash configuration but DO NOT connect. The constructor is allowed
        # to run in any environment so the class is inspectable, importable,
        # and documentable without a running Milvus cluster.
        self.memory_dir = memory_dir
        self.host = host
        self.port = port
        self.collection = collection

    # ── Lifecycle ─────────────────────────────────────────────────────

    def initialize(self) -> None:
        """Verify pymilvus is installed and the cluster is reachable.

        Currently raises so callers learn the stub state explicitly rather
        than failing later in an opaque place.
        """
        try:
            import pymilvus  # noqa: F401  -- import check only
        except ImportError as exc:
            raise ImportError(
                "pymilvus is not installed. Install it with:\n"
                "    pip install pymilvus\n"
                f"See {_DOCS_URL} for full setup."
            ) from exc

        raise NotImplementedError(
            f"MilvusBackend.initialize is a stub. See {_DOCS_URL} for the "
            "production implementation checklist."
        )

    # ── Read ──────────────────────────────────────────────────────────

    def count(self) -> int:
        raise NotImplementedError(f"MilvusBackend.count — stub. See {_DOCS_URL}.")

    def search(self, query_embedding: list[float], top_k: int) -> list[dict[str, Any]]:
        raise NotImplementedError(f"MilvusBackend.search — stub. See {_DOCS_URL}.")

    def get_all_ids(self) -> list[str]:
        raise NotImplementedError(f"MilvusBackend.get_all_ids — stub. See {_DOCS_URL}.")

    # ── Write ─────────────────────────────────────────────────────────

    def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> None:
        raise NotImplementedError(f"MilvusBackend.upsert — stub. See {_DOCS_URL}.")

    def delete(self, ids: list[str]) -> None:
        raise NotImplementedError(f"MilvusBackend.delete — stub. See {_DOCS_URL}.")

    def rebuild(self) -> None:
        raise NotImplementedError(f"MilvusBackend.rebuild — stub. See {_DOCS_URL}.")

    def close(self) -> None:
        # No resources held by the stub.
        return None
