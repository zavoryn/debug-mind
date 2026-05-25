"""Smoke tests for the Milvus backend stub (P6-3)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from debug_mind.memory.backends.base import StorageBackend
from debug_mind.memory.backends.milvus_backend import MilvusBackend


class TestMilvusStub:
    def test_class_implements_storage_backend_protocol(self):
        # ABC subclass relationship verifies we cover every abstract method.
        # If a method gets added to StorageBackend later, instantiating this
        # class will raise TypeError, surfacing the drift here.
        assert issubclass(MilvusBackend, StorageBackend)
        # And the stub MUST be instantiable without pymilvus or a server.
        backend = MilvusBackend(Path("/tmp/whatever"))
        assert backend.host == "localhost"
        assert backend.port == 19530
        assert backend.collection == "debug_mind_cases"

    def test_initialize_without_pymilvus_raises_clear_import_error(self, monkeypatch):
        # Force the import to fail even if the dev box has pymilvus installed.
        # We use the standard sys.modules trick: set the name to None so the
        # next `import pymilvus` raises ImportError.
        monkeypatch.setitem(sys.modules, "pymilvus", None)
        backend = MilvusBackend(Path("/tmp/whatever"))
        with pytest.raises(ImportError, match="pymilvus"):
            backend.initialize()

    def test_stub_methods_raise_not_implemented_with_doc_pointer(self):
        backend = MilvusBackend(Path("/tmp/whatever"))
        # Each stubbed method should point a reader at docs/MILVUS.md.
        for name in ("count", "get_all_ids", "rebuild"):
            with pytest.raises(NotImplementedError, match="docs/MILVUS.md"):
                getattr(backend, name)()
        with pytest.raises(NotImplementedError, match="docs/MILVUS.md"):
            backend.search([0.0, 0.0], top_k=3)
        with pytest.raises(NotImplementedError, match="docs/MILVUS.md"):
            backend.upsert(["x"], [[0.0]], [{}])
        with pytest.raises(NotImplementedError, match="docs/MILVUS.md"):
            backend.delete(["x"])
        # close() is intentionally a no-op (no resources held)
        assert backend.close() is None
