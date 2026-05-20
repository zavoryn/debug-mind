"""Protocol for storage backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class StorageBackend(ABC):
    """Protocol for pluggable storage backends behind MemoryStore."""

    @abstractmethod
    def initialize(self) -> None:
        """Create or verify the necessary collections/tables."""

    @abstractmethod
    def count(self) -> int:
        """Return total document count."""

    @abstractmethod
    def search(self, query_embedding: list[float], top_k: int) -> list[dict[str, Any]]:
        """Return top_k results as [{"id": ..., "score": ..., "metadata": ...}, ...]."""

    @abstractmethod
    def upsert(
        self, ids: list[str], embeddings: list[list[float]], metadatas: list[dict[str, Any]]
    ) -> None:
        """Insert or update documents."""

    @abstractmethod
    def delete(self, ids: list[str]) -> None:
        """Remove documents by ID."""

    @abstractmethod
    def get_all_ids(self) -> list[str]:
        """Return all document IDs in the collection."""

    @abstractmethod
    def rebuild(self) -> None:
        """Delete the collection/table and recreate it (used by rebuild_index)."""
