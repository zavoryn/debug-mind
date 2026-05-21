"""Tests for SQLite backend — embedding serialization and rebuild."""

from __future__ import annotations

from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import BugCase


class FakeArray:
    def __init__(self, values):
        self._values = values

    def tolist(self):
        return list(self._values)


def test_sqlite_backend_accepts_numpy_like_embeddings(tmp_path, monkeypatch):
    monkeypatch.setenv("DEBUG_MIND_BACKEND", "sqlite")

    def embed(texts: list[str]):
        return [FakeArray([1.0, 0.0, 0.0]) for _ in texts]

    store = MemoryStore(memory_dir=tmp_path / "mem", embedding_fn=embed)
    case = BugCase(title="sqlite vector", symptoms="searchable symptom")

    store.save(case)

    results = store.search("searchable symptom", min_score=0.0)
    assert [r.case.id for r in results] == [case.id]


def test_sqlite_rebuild_indexes_markdown(tmp_path, monkeypatch):
    monkeypatch.setenv("DEBUG_MIND_BACKEND", "sqlite")

    def embed(texts: list[str]):
        return [FakeArray([1.0, 0.0, 0.0]) for _ in texts]

    store = MemoryStore(memory_dir=tmp_path / "mem", embedding_fn=embed)
    case = BugCase(title="sqlite rebuild", symptoms="rebuild symptom")
    store._save_to_markdown(case)

    assert store.rebuild_index() == 1
    assert store.search("rebuild symptom", min_score=0.0)[0].case.id == case.id
