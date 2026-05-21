"""SQLite storage backend — zero-dependency alternative to ChromaDB.

Uses Python's built-in sqlite3 with JSON-encoded embeddings and
Python-side cosine similarity for search. No C extensions needed.
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any


class SQLiteBackend:
    """SQLite-based storage backend implementing the same interface as ChromaBackend.

    Note: does not formally inherit from StorageBackend to avoid ABC overhead,
    but implements the same protocol.
    """

    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir
        self._db_path = memory_dir / "sqlite" / "cases.db"
        self._conn: sqlite3.Connection | None = None

    @staticmethod
    def _json_safe_embedding(embedding) -> list[float]:
        if hasattr(embedding, "tolist"):
            embedding = embedding.tolist()
        return [float(v) for v in embedding]

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS cases (
                id TEXT PRIMARY KEY,
                embedding TEXT NOT NULL,
                metadata TEXT NOT NULL
            )"""
        )
        self._conn.commit()

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.initialize()
        return self._conn

    def count(self) -> int:
        row = self._ensure_conn().execute("SELECT COUNT(*) FROM cases").fetchone()
        return row[0] if row else 0

    def search(self, query_embedding: list[float], top_k: int) -> list[dict[str, Any]]:
        conn = self._ensure_conn()
        rows = conn.execute("SELECT id, embedding, metadata FROM cases").fetchall()
        if not rows:
            return []
        scored = []
        for row in rows:
            emb = json.loads(row[1])
            score = self._cosine_similarity(query_embedding, emb)
            meta = json.loads(row[2]) if row[2] else {}
            scored.append({"id": row[0], "score": score, "metadata": meta})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def upsert(
        self, ids: list[str], embeddings: list[list[float]], metadatas: list[dict[str, Any]]
    ) -> None:
        conn = self._ensure_conn()
        for i, doc_id in enumerate(ids):
            emb_json = json.dumps(self._json_safe_embedding(embeddings[i]))
            meta_json = json.dumps(metadatas[i])
            conn.execute(
                "INSERT OR REPLACE INTO cases (id, embedding, metadata) VALUES (?, ?, ?)",
                (doc_id, emb_json, meta_json),
            )
        conn.commit()

    def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        conn = self._ensure_conn()
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM cases WHERE id IN ({placeholders})", ids)
        conn.commit()

    def get_all_ids(self) -> list[str]:
        rows = self._ensure_conn().execute("SELECT id FROM cases").fetchall()
        return [r[0] for r in rows]

    def rebuild(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
        if self._db_path.exists():
            self._db_path.unlink()
        self.initialize()

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
