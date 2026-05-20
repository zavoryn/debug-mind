"""Concurrency safety tests — verify filelock protects parallel writes.

P2-1: Multiple processes can save concurrently without corruption.

Note: ChromaDB PersistentClient uses SQLite, which does NOT support
multi-process concurrent writes. The filelock protects Markdown files,
but workers may still fail on ChromaDB init. We retry to work around this.
For true multi-process scenarios, use ChromaDB server mode or a different
vector backend.
"""

from __future__ import annotations

import multiprocessing
from pathlib import Path
from unittest.mock import patch

import pytest

from debug_mind.memory.store import MemoryStore, MemoryBusyError
from debug_mind.schemas import BugCase


def _worker_save(tmp_dir: str, idx: int) -> str:
    """Worker that saves a case and returns its ID.

    Retries MemoryStore init because ChromaDB PersistentClient uses SQLite
    which doesn't support multi-process concurrent access — the init may fail
    if another process holds the SQLite lock.
    """
    import time

    store = None
    for attempt in range(5):
        try:
            store = MemoryStore(memory_dir=tmp_dir)
            break
        except Exception:
            time.sleep(0.3 * (attempt + 1))
    if store is None:
        raise RuntimeError(f"Worker {idx}: MemoryStore init failed after 5 retries")

    case = BugCase(title=f"concurrent-{idx}", symptoms=f"symptom-{idx}")
    store.save(case)
    return case.id


class TestConcurrentSave:
    """Multiple processes save cases at the same time."""

    def test_concurrent_saves_all_preserved(self, tmp_path: Path) -> None:
        """10 child processes save concurrently; all cases survive."""
        tmp_dir = str(tmp_path / "mem")
        num_workers = 10

        with multiprocessing.Pool(processes=num_workers) as pool:
            ids = pool.starmap(_worker_save, [(tmp_dir, i) for i in range(num_workers)])

        store = MemoryStore(memory_dir=tmp_dir)
        for case_id in ids:
            case = store.get(case_id)
            assert case is not None, f"Case {case_id} was lost"
            assert case.title.startswith("concurrent-")

        # No .tmp residue
        tmp_files = list(Path(tmp_dir).glob("cases/*.tmp"))
        assert tmp_files == [], f"Leftover .tmp files: {tmp_files}"

    def test_lock_timeout_raises_memory_busy(self, tmp_path: Path) -> None:
        """When FileLock times out, MemoryBusyError is raised with a friendly message."""
        from filelock import Timeout as FileLockTimeout

        store = MemoryStore(memory_dir=tmp_path / "mem")
        case = BugCase(title="timeout-test", symptoms="s")

        with patch.object(store, "_lock") as mock_lock:
            mock_lock.__enter__ = mock_lock.__enter__
            mock_lock.__enter__.side_effect = FileLockTimeout("lock-file")
            mock_lock.__exit__ = lambda *a: None

            with pytest.raises(MemoryBusyError, match="[Mm]emory.*busy"):
                store.save(case)

    def test_delete_under_lock(self, tmp_path: Path) -> None:
        """Delete also acquires the lock."""
        store = MemoryStore(memory_dir=tmp_path / "mem")
        case = BugCase(title="del-me", symptoms="s")
        store.save(case)
        assert store.get(case.id) is not None

        store.delete(case.id)
        assert store.get(case.id) is None

    def test_lock_timeout_on_delete(self, tmp_path: Path) -> None:
        """Delete raises MemoryBusyError on lock timeout."""
        from filelock import Timeout as FileLockTimeout

        store = MemoryStore(memory_dir=tmp_path / "mem")
        case = BugCase(title="del-timeout", symptoms="s")
        store.save(case)

        with patch.object(store, "_lock") as mock_lock:
            mock_lock.__enter__ = mock_lock.__enter__
            mock_lock.__enter__.side_effect = FileLockTimeout("lock-file")
            mock_lock.__exit__ = lambda *a: None

            with pytest.raises(MemoryBusyError):
                store.delete(case.id)

    def test_read_without_lock(self, tmp_path: Path) -> None:
        """get/search/list_recent/stats don't need a lock."""
        store = MemoryStore(memory_dir=tmp_path / "mem")
        case = BugCase(title="read-test", symptoms="s")
        store.save(case)

        # These should work even if lock is held by someone else
        # (no lock acquired for reads)
        assert store.get(case.id) is not None
        assert len(store.list_recent()) >= 1
        assert store.stats().total_cases >= 1
        results = store.search("read-test")
        assert len(results) >= 1
