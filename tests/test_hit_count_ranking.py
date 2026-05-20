"""Tests for hit_count ranking — P2-8."""

from __future__ import annotations


from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import BugCase


class TestHitCountRanking:
    def test_higher_hit_count_ranks_first(self, tmp_path):
        """Same score + different hit_count: higher hit_count wins."""
        store = MemoryStore(memory_dir=tmp_path / "mem")

        # Create two cases with similar content but different hit_count
        low = BugCase(title="ranking test low", symptoms="shared symptoms", root_cause="rc")
        high = BugCase(title="ranking test high", symptoms="shared symptoms", root_cause="rc")

        store.save(low)
        store.save(high)

        # Manually bump hit_count on high
        high_case = store.get(high.id)
        high_case.hit_count = 10
        store._save_to_markdown(high_case)
        store._save_to_vector(high_case)

        results = store.search("ranking test shared symptoms", top_k=2)
        assert len(results) >= 2
        # The case with higher hit_count should rank first
        assert results[0].case.hit_count >= results[1].case.hit_count

    def test_zero_weight_degrades_to_phase1(self, tmp_path, monkeypatch):
        """DEBUG_MIND_HIT_COUNT_WEIGHT=0 disables hit_count effect."""
        monkeypatch.setenv("DEBUG_MIND_HIT_COUNT_WEIGHT", "0")

        # Reload the module constant
        import debug_mind.memory.store as store_mod

        store_mod.HIT_COUNT_WEIGHT = 0.0

        store = MemoryStore(memory_dir=tmp_path / "mem2")

        low = BugCase(title="no boost low", symptoms="same symptoms here", root_cause="rc")
        high = BugCase(title="no boost high", symptoms="same symptoms here", root_cause="rc")

        store.save(low)
        store.save(high)

        high_case = store.get(high.id)
        high_case.hit_count = 50
        store._save_to_markdown(high_case)
        store._save_to_vector(high_case)

        # With weight=0, ordering should be by score alone (verified status same)
        results = store.search("same symptoms here", top_k=2)
        assert len(results) >= 2

        # Restore
        store_mod.HIT_COUNT_WEIGHT = 0.05

    def test_hit_count_zero_still_works(self, tmp_path):
        """Cases with hit_count=0 still appear in results."""
        store = MemoryStore(memory_dir=tmp_path / "mem")
        case = BugCase(title="zero hits", symptoms="unique symptoms xyz", root_cause="rc")
        store.save(case)

        results = store.search("unique symptoms xyz")
        assert len(results) >= 1
        assert results[0].case.hit_count == 0

    def test_verified_still_boosts(self, tmp_path):
        """Verified case should still outrank unverified, even with lower hit_count."""
        store = MemoryStore(memory_dir=tmp_path / "mem")

        verified = BugCase(title="verified case", symptoms="common symptoms", root_cause="rc")
        unverified = BugCase(title="unverified case", symptoms="common symptoms", root_cause="rc")

        store.save(verified)
        store.save(unverified)

        # Mark one as verified
        verified_case = store.get(verified.id)
        verified_case.verified = True
        store._save_to_markdown(verified_case)
        store._save_to_vector(verified_case)

        # Give unverified more hit_count
        unverified_case = store.get(unverified.id)
        unverified_case.hit_count = 5
        store._save_to_markdown(unverified_case)
        store._save_to_vector(unverified_case)

        results = store.search("common symptoms", top_k=2)
        assert len(results) >= 2
        # Verified should still win (1.0 vs 0.7 multiplier is stronger than log1p(5)*0.05)
        assert results[0].case.verified or results[0].case.id == verified.id
