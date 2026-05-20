"""Tests for consistency reconciliation — P2-7."""

from __future__ import annotations

import os
import time


from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import BugCase


class TestCleanupTmps:
    def test_stale_tmp_removed_on_init(self, tmp_path):
        """tmp files older than 10 min are cleaned on init."""
        store = MemoryStore(memory_dir=tmp_path / "mem")
        case = BugCase(title="test", symptoms="s")
        store.save(case)

        # Create a stale .tmp
        tmp_file = store.cases_dir / "stale.md.tmp"
        tmp_file.write_text("old content", encoding="utf-8")
        # Set mtime to 11 min ago
        old_time = time.time() - 660
        os.utime(str(tmp_file), (old_time, old_time))

        # Re-init should clean it
        _store = MemoryStore(memory_dir=tmp_path / "mem")
        assert not tmp_file.exists()

    def test_fresh_tmp_kept(self, tmp_path):
        """tmp files newer than 10 min are NOT cleaned."""
        store = MemoryStore(memory_dir=tmp_path / "mem")
        tmp_file = store.cases_dir / "fresh.md.tmp"
        tmp_file.write_text("fresh content", encoding="utf-8")

        _store = MemoryStore(memory_dir=tmp_path / "mem")
        assert tmp_file.exists()


class TestDoctorCommand:
    def test_missing_vectors_detected(self, tmp_path):
        """doctor detects markdown files without vectors."""
        store = MemoryStore(memory_dir=tmp_path / "mem")

        # Write markdown directly (bypass save to skip vector)
        case = BugCase(title="orphan-md", symptoms="s")
        md_path = store.cases_dir / f"{case.id}.md"
        md_path.write_text(
            f"# orphan-md\n> case_id: `{case.id}` | severity: **medium** | status: **reported**\n",
            encoding="utf-8",
        )

        result = store.doctor()
        assert result["missing_vectors"] >= 1

    def test_doctor_fix_reindexes(self, tmp_path):
        """doctor --fix reindexes missing vectors."""
        store = MemoryStore(memory_dir=tmp_path / "mem")

        case = BugCase(title="fix-me", symptoms="s")
        md_path = store.cases_dir / f"{case.id}.md"
        md_path.write_text(
            f"# fix-me\n> case_id: `{case.id}` | severity: **medium** | status: **reported**\n",
            encoding="utf-8",
        )

        result = store.doctor(fix=True)
        assert result["fixed_missing"] >= 1
        assert result["missing_vectors"] >= 1  # was missing before fix

        # After fix, search should find it
        results = store.search("fix-me")
        assert len(results) >= 1

    def test_orphan_vectors_detected(self, tmp_path):
        """doctor detects vectors without markdown."""
        store = MemoryStore(memory_dir=tmp_path / "mem")
        case = BugCase(title="orphan-vec", symptoms="s")
        store.save(case)

        # Delete markdown but keep vector
        md_path = store.cases_dir / f"{case.id}.md"
        md_path.unlink()

        result = store.doctor()
        assert result["orphan_vectors"] >= 1

    def test_doctor_fix_no_delete_by_default(self, tmp_path):
        """doctor --fix without --delete-orphans doesn't delete."""
        store = MemoryStore(memory_dir=tmp_path / "mem")
        case = BugCase(title="keep-vec", symptoms="s")
        store.save(case)

        md_path = store.cases_dir / f"{case.id}.md"
        md_path.unlink()

        result = store.doctor(fix=True)
        assert result["deleted_orphans"] == 0

    def test_doctor_fix_delete_orphans(self, tmp_path):
        """doctor --fix --delete-orphans removes orphan vectors."""
        store = MemoryStore(memory_dir=tmp_path / "mem")
        case = BugCase(title="del-vec", symptoms="s")
        store.save(case)

        md_path = store.cases_dir / f"{case.id}.md"
        md_path.unlink()

        result = store.doctor(fix=True, delete_orphans=True)
        assert result["deleted_orphans"] >= 1


class TestVerifyTransactional:
    def test_verify_wrong_uses_pending(self, tmp_path):
        """verify(correct=False) creates .pending then .rejected."""
        store = MemoryStore(memory_dir=tmp_path / "mem")
        case = BugCase(title="reject-me", symptoms="s")
        store.save(case)
        case_id = case.id

        store.verify(case_id, correct=False)

        # Should end up as .rejected, no .pending
        rejected = store.cases_dir / f"{case_id}.md.rejected"
        pending = store.cases_dir / f"{case_id}.md.rejected.pending"
        md = store.cases_dir / f"{case_id}.md"

        assert rejected.exists()
        assert not pending.exists()
        assert not md.exists()

    def test_pending_recoverable_by_doctor(self, tmp_path):
        """If verify crashes after .pending, doctor can complete the rename."""
        store = MemoryStore(memory_dir=tmp_path / "mem")
        case = BugCase(title="crash-test", symptoms="s")
        store.save(case)
        case_id = case.id

        # Simulate crash: manually create .pending
        md_path = store.cases_dir / f"{case_id}.md"
        pending_path = store.cases_dir / f"{case_id}.md.rejected.pending"
        md_path.rename(pending_path)

        # doctor --fix should complete the rename
        result = store.doctor(fix=True)
        assert result["pending_rejected"] >= 1
        assert result["fixed_pending"] >= 1

        rejected_path = store.cases_dir / f"{case_id}.md.rejected"
        assert rejected_path.exists()
        assert not pending_path.exists()
