"""ChromaDB storage backend — the default vector store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb

from debug_mind.memory.backends.base import StorageBackend

COLLECTION_NAME = "bug_cases"


class ChromaBackend(StorageBackend):
    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir
        self._client: chromadb.PersistentClient | None = None
        self._collection: chromadb.Collection | None = None

    def initialize(self) -> None:
        chroma_dir = str(self.memory_dir / "chroma")
        self._client = chromadb.PersistentClient(path=chroma_dir)
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def collection(self) -> chromadb.Collection:
        if self._collection is None:
            self.initialize()
        return self._collection

    def count(self) -> int:
        return self.collection.count()

    def search(self, query_embedding: list[float], top_k: int) -> list[dict[str, Any]]:
        if self.count() == 0:
            return []
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, self.count()),
            include=["metadatas", "distances"],
        )
        out = []
        ids = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        for i, doc_id in enumerate(ids):
            distance = distances[i] if i < len(distances) else 1.0
            score = 1.0 - distance
            meta = metadatas[i] if i < len(metadatas) else {}
            out.append({"id": doc_id, "score": max(0.0, score), "metadata": meta})
        return out

    def upsert(
        self, ids: list[str], embeddings: list[list[float]], metadatas: list[dict[str, Any]]
    ) -> None:
        self.collection.upsert(ids=ids, embeddings=embeddings, metadatas=metadatas)

    def delete(self, ids: list[str]) -> None:
        ids_in_collection = self.get_all_ids()
        existing = [i for i in ids if i in ids_in_collection]
        if existing:
            self.collection.delete(ids=existing)

    def get_all_ids(self) -> list[str]:
        count = self.collection.count()
        if count == 0:
            return []
        results = self.collection.get(include=[], limit=count)
        return results.get("ids", [])

    def close(self) -> None:
        # Reset pointers so the next call reinitializes cleanly.
        # ChromaDB PersistentClient flushes to disk on GC; calling stop()
        # forces an immediate flush so the last writes survive process exit.
        if self._client is not None:
            try:
                self._client.stop()
            except Exception:
                pass
        self._collection = None
        self._client = None

    def rebuild(self) -> None:
        try:
            self._client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
        self._collection = None
        self.initialize()
