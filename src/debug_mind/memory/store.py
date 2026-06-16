"""Memory Store — the core of DebugMind's experiential memory.

Hybrid storage: pluggable vector backend (ChromaDB or SQLite) + Markdown
files for human-readable persistence and git-based knowledge sharing.

Design decisions:
- ChromaDB is the default backend; set DEBUG_MIND_BACKEND=sqlite to switch.
- Every write goes to both the vector backend and a Markdown file.
- Markdown is the source of truth — the backend can be rebuilt from it.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from datetime import datetime, timezone

from filelock import FileLock, Timeout as FileLockTimeout

from debug_mind.schemas import BugCase, SearchResult, MemoryStats, Severity, BugStatus
from debug_mind.observability.logger import get_logger

_log = get_logger("memory")

# Pre-compiled patterns for _markdown_to_case — compiled once at import time
_RE_CASE_ID = re.compile(r"case_id:\s*`(\w+)`")
_RE_SEVERITY = re.compile(r"severity:\s*\*\*(\w+)\*\*")
_RE_STATUS = re.compile(r"status:\s*\*\*(\w+)\*\*")
_RE_TITLE = re.compile(r"^# (.+)$", re.MULTILINE)
_RE_CREATED = re.compile(r"created:\s*(\S+)")
_RE_UPDATED = re.compile(r"updated:\s*(\S+)")
_RE_SIMILAR_CASES = re.compile(r"similar_cases:\s*(\[.*?\])")
_RE_VERIFIED = re.compile(r"^- verified:\s*(true|false)", re.MULTILINE | re.IGNORECASE)
_RE_VERIFICATION_NOTES = re.compile(r"^- verification_notes:\s*(.+)", re.MULTILINE)
_RE_HIT_COUNT = re.compile(r"^- hit_count:\s*(\d+)", re.MULTILINE)
_RE_LAST_USED_AT = re.compile(r"^- last_used_at:\s*(\S+)", re.MULTILINE)
_RE_SUPERSEDED_BY = re.compile(r"^- superseded_by:\s*(\S+)", re.MULTILINE)
_RE_LAST_VERIFIED_AT = re.compile(r"^- last_verified_at:\s*(\S+)", re.MULTILINE)
_RE_VERIFY_COUNT = re.compile(r"^- verify_count:\s*(\d+)", re.MULTILINE)
_RE_VERSION = re.compile(r"^- version:\s*(\d+)", re.MULTILINE)
_RE_LINKS = re.compile(r"^- links:\s*(\[.*\])", re.MULTILINE)
_RE_SECTIONS: dict[str, re.Pattern] = {
    hdr: re.compile(rf"## {re.escape(hdr)}\s*\n(.*?)(?=\n## |\n---)", re.DOTALL)
    for hdr in (
        "Symptoms",
        "Root Cause",
        "Fix Suggestion",
        "Patch Attempts",
        "Error Log",
        "Environment",
        "Diagnosis Steps",
        "Tags",
    )
}

DEFAULT_MEMORY_DIR = Path(os.environ.get("DEBUG_MIND_MEMORY_DIR", "memory"))
DEDUP_THRESHOLD = float(os.environ.get("DEBUG_MIND_DEDUP_THRESHOLD", "0.92"))
HIT_COUNT_WEIGHT = float(os.environ.get("DEBUG_MIND_HIT_COUNT_WEIGHT", "0.05"))
DEFAULT_NAMESPACE = os.environ.get("DEBUG_MIND_NAMESPACE", "default")


def _as_float_list(vector) -> list[float]:
    """Convert numpy/list-like embedding outputs into JSON-safe floats."""
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    return [float(v) for v in vector]


def _fallback_embed(texts: list[str]) -> list[list[float]]:
    """Last-resort embedding — trigram hash, pure Python, zero deps."""
    from debug_mind.memory.embeddings import _trigram_hash_embedding

    return _trigram_hash_embedding(texts)


class MemoryBusyError(Exception):
    """Raised when a write operation cannot acquire the memory lock within timeout."""


class ConflictError(Exception):
    """Raised when optimistic lock version mismatch is detected."""


class MemoryStore:
    """Persistent bug memory with vector similarity search."""

    def __init__(
        self,
        memory_dir: Path | str | None = None,
        embedding_fn=None,
        reranker=None,
        namespace: str = DEFAULT_NAMESPACE,
    ):
        # Base directory: the user-facing "memory/" root.
        # Effective directory: {base}/{namespace}/ where cases/sqlite/chroma live.
        self.base_dir = Path(memory_dir) if memory_dir else DEFAULT_MEMORY_DIR
        if not namespace or "/" in namespace or "\\" in namespace or namespace.startswith("."):
            raise ValueError(
                f"Invalid namespace: {namespace!r}. Must be a non-empty single path "
                "segment without separators or leading dot."
            )
        self.namespace = namespace
        self.memory_dir = self.base_dir / namespace

        # Auto-migrate pre-Phase-6 layout (memory/cases/ → memory/default/cases/)
        # but only when the caller is asking for the "default" namespace.
        if namespace == "default":
            self._migrate_legacy_layout(self.base_dir)

        self.cases_dir = self.memory_dir / "cases"
        self.cases_dir.mkdir(parents=True, exist_ok=True)
        self.reranker = reranker
        self._embedding_fn = embedding_fn
        self._cached_embed_fn = None

        self._lock = FileLock(str(self.memory_dir / ".lock"), timeout=30)

        self.backend = self._create_backend()
        self.backend.initialize()

        self._cleanup_stale_tmps()

    # ── Migration ─────────────────────────────────────────────────

    @staticmethod
    def _migrate_legacy_layout(base_dir: Path) -> bool:
        """Move pre-Phase-6 files from base_dir/ into base_dir/default/.

        Only runs when:
          - base_dir/cases/ exists (legacy marker)
          - base_dir/default/ does NOT exist (not yet migrated)

        Moves are per-entry via os.rename; on same filesystem each rename is
        atomic. Backend artifacts (sqlite/, chroma/) are moved too so the
        vector index is preserved without a rebuild. If a backend move fails
        the markdown move still succeeds and the backend will rebuild lazily.

        Returns True if anything was migrated, False otherwise.
        """
        legacy_cases = base_dir / "cases"
        new_default = base_dir / "default"
        if not legacy_cases.exists() or not legacy_cases.is_dir():
            return False
        if new_default.exists():
            return False

        _log.warning(
            "memory: migrating legacy layout → default namespace",
            extra={"base_dir": str(base_dir)},
        )
        try:
            new_default.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            _log.error(
                "memory: migration failed to create default/ dir",
                extra={"err": str(e)},
            )
            return False

        moved_anything = False
        try:
            os.rename(str(legacy_cases), str(new_default / "cases"))
            moved_anything = True
        except OSError as e:
            _log.error("memory: migration cases move failed", extra={"err": str(e)})
            return False

        for sub in ("sqlite", "chroma"):
            src = base_dir / sub
            if src.exists() and src.is_dir():
                try:
                    os.rename(str(src), str(new_default / sub))
                except OSError as e:
                    _log.warning(
                        "memory: migration backend move failed (will rebuild lazily)",
                        extra={"sub": sub, "err": str(e)},
                    )

        for fname in ("audit.jsonl",):
            src = base_dir / fname
            if src.exists() and src.is_file():
                try:
                    os.rename(str(src), str(new_default / fname))
                except OSError:
                    pass

        _log.info(
            "memory: legacy layout migrated to default namespace",
            extra={"base_dir": str(base_dir)},
        )
        return moved_anything

    def _create_backend(self):
        # Default is SQLite — pure stdlib, no C extensions, works on every platform.
        # Set DEBUG_MIND_BACKEND=chroma to use ChromaDB (pip install debug-mind[chroma]).
        backend_choice = os.environ.get("DEBUG_MIND_BACKEND", "sqlite").lower()
        if backend_choice == "sqlite":
            from debug_mind.memory.backends.sqlite_backend import SQLiteBackend

            return SQLiteBackend(self.memory_dir)
        if backend_choice == "chroma":
            try:
                from debug_mind.memory.backends.chroma_backend import ChromaBackend
            except ImportError:
                raise ImportError(
                    "ChromaDB is not installed. "
                    "Run: pip install debug-mind[chroma]\n"
                    "Or use the default SQLite backend (no extra install needed)."
                ) from None
            return ChromaBackend(self.memory_dir)
        raise ValueError(f"Unknown backend {backend_choice!r}. Valid values: sqlite, chroma.")

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Embed one or more texts, returning a list of embedding vectors."""
        if self._embedding_fn is not None:
            return [_as_float_list(v) for v in self._embedding_fn(texts)]
        if self._cached_embed_fn is None:
            from debug_mind.memory.embeddings import default_embedding

            try:
                self._cached_embed_fn = default_embedding()
            except Exception as e:
                _log.warning(
                    "Embedding init failed — falling back to trigram hash. "
                    "Search quality is significantly degraded. "
                    "Fix: pip install debug-mind[embeddings]",
                    extra={"error": str(e)},
                )
                self._cached_embed_fn = _fallback_embed
        try:
            vectors = self._cached_embed_fn(texts)
        except Exception as e:
            _log.warning(
                "Embedding call failed — falling back to trigram hash. "
                "Search quality is significantly degraded.",
                extra={"error": str(e)},
            )
            vectors = _fallback_embed(texts)
        return [_as_float_list(v) for v in vectors]

    # ── Write ──────────────────────────────────────────────────────

    def save(self, case: BugCase) -> BugCase:
        """Persist a bug case. Deduplicates against verified existing cases."""
        # Defense-in-depth: sanitize all text inputs at the store level
        # so even callers that skip sanitization are protected.
        from debug_mind.sanitize import (
            sanitize_description,
            sanitize_error_log,
            sanitize_environment,
            sanitize_patch_attempts,
            sanitize_tags,
        )

        case.title = sanitize_description(case.title)
        case.symptoms = sanitize_description(case.symptoms)
        case.root_cause = sanitize_description(case.root_cause)
        case.fix_suggestion = sanitize_description(case.fix_suggestion)
        case.error_log = sanitize_error_log(case.error_log)
        case.environment = sanitize_environment(case.environment)
        case.tags = sanitize_tags(case.tags)
        case.patch_attempts = sanitize_patch_attempts(case.patch_attempts)

        # Embedding can be slow (model init / API fallback). Keep it outside the
        # write lock so concurrent writers wait only for file/backend mutation.
        precomputed_emb = self._embed([case.to_search_text()])[0]

        try:
            with self._lock:
                case.updated_at = datetime.now(timezone.utc)
                case.version = (case.version or 1) + 1

                existing, precomputed_emb = self._find_dedup_target(
                    case, embedding=precomputed_emb
                )
                if existing:
                    return existing

                self._save_to_markdown(case)

                try:
                    self._save_to_vector(case, embedding=precomputed_emb)
                except Exception as e:
                    _log.warning(f"Vector upsert failed for {case.id}: {e}")
        except FileLockTimeout:
            raise MemoryBusyError("Memory is busy — another process is writing. Retry in a moment.")
        _log.info("case saved", extra={"op": "save", "case_id": case.id})
        return case

    def _find_dedup_target(
        self, case: BugCase, embedding: list[float] | None = None
    ) -> tuple[BugCase | None, list[float] | None]:
        """Check if a verified case with high similarity already exists.

        Returns (existing_case, embedding) so the caller can reuse the embedding
        for the subsequent _save_to_vector call, avoiding a redundant embed.

        Only merges against verified cases — unverified cases are kept separate
        to preserve diversity (wrong diagnoses may look similar to correct ones).
        """
        count = self.backend.count()
        if count == 0:
            return None, embedding

        if embedding is None:
            query_text = case.to_search_text()
            embedding = self._embed([query_text])[0]
        results = self.backend.search(embedding, min(3, count))

        if not results:
            return None, embedding

        for entry in results:
            case_id = entry["id"]
            score = entry["score"]
            if score < DEDUP_THRESHOLD:
                continue

            md_path = self.cases_dir / f"{case_id}.md"
            if not md_path.exists():
                continue

            existing = _markdown_to_case(md_path)
            if not existing or not existing.verified:
                # Unverified high-similarity cases are NOT merged — keep diversity
                continue

            # Merge: append new symptoms as a variant
            variant_text = f"\n---\nVariant ({case.created_at.isoformat()}): {case.symptoms[:200]}"
            existing.symptoms += variant_text
            existing.updated_at = datetime.now(timezone.utc)
            self._save_to_markdown(existing)
            try:
                self._save_to_vector(existing)
            except Exception:
                pass
            return existing, embedding

        return None, embedding

    def _save_to_vector(self, case: BugCase, embedding: list[float] | None = None) -> None:
        if embedding is None:
            embedding = self._embed([case.to_search_text()])[0]
        metadata = {
            "severity": case.severity.value,
            "status": case.status.value,
            "tags": json.dumps(case.tags),
            "created_at": case.created_at.isoformat(),
        }

        self.backend.upsert(
            ids=[case.id],
            embeddings=[embedding],
            metadatas=[metadata],
        )

    def _save_to_markdown(self, case: BugCase) -> None:
        md_path = self.cases_dir / f"{case.id}.md"
        content = _case_to_markdown(case)
        tmp_path = self.cases_dir / f"{case.id}.md.tmp"
        try:
            tmp_path.write_text(content, encoding="utf-8")
            os.replace(str(tmp_path), str(md_path))
        except Exception:
            # Clean up temp file on failure
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    # ── Lexical helpers ────────────────────────────────────────────

    @staticmethod
    def _tokenize_for_search(text: str) -> set[str]:
        return {t for t in re.findall(r"[a-zA-Z0-9_+-]+", text.lower()) if len(t) >= 2}

    @staticmethod
    def _lexical_score(query: str, case: BugCase) -> float:
        query_tokens = MemoryStore._tokenize_for_search(query)
        if not query_tokens:
            return 0.0
        case_tokens = MemoryStore._tokenize_for_search(case.to_search_text())
        if not case_tokens:
            return 0.0
        return len(query_tokens & case_tokens) / len(query_tokens)

    # ── Search ─────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.1,
        include_unverified: bool = True,
        fallback_namespaces: list[str] | None = None,
    ) -> list[SearchResult]:
        """Search for similar bug cases by semantic similarity.

        Results are reranked: verified cases get a boost (effective_score = score * 1.0
        for verified, score * 0.7 for unverified), but the returned score field
        contains the original cosine similarity for evaluation transparency.

        If `fallback_namespaces` is given and the local namespace returns fewer
        than 3 results, additional namespaces are searched to pad up to top_k.
        Results from a fallback have `from_namespace` populated.
        """
        results = self._search_local(query, top_k, min_score, include_unverified)

        if fallback_namespaces and len(results) < 3:
            for ns in fallback_namespaces:
                if not ns or ns == self.namespace:
                    continue
                if len(results) >= top_k:
                    break
                try:
                    fb = MemoryStore(
                        memory_dir=self.base_dir,
                        embedding_fn=self._embedding_fn,
                        reranker=self.reranker,
                        namespace=ns,
                    )
                except (FileNotFoundError, OSError, ValueError) as e:
                    _log.warning(
                        "memory: fallback ns init failed",
                        extra={"ns": ns, "err": str(e)},
                    )
                    continue
                try:
                    fb_results = fb._search_local(
                        query, top_k - len(results), min_score, include_unverified
                    )
                    seen_ids = {r.case.id for r in results}
                    for r in fb_results:
                        if r.case.id in seen_ids:
                            continue
                        r.from_namespace = ns
                        results.append(r)
                finally:
                    fb.close()

        return results[:top_k]

    def _search_local(
        self,
        query: str,
        top_k: int,
        min_score: float,
        include_unverified: bool,
    ) -> list[SearchResult]:
        """The namespace-local search — does NOT touch fallback namespaces."""
        count = self.backend.count()
        if count == 0:
            return []

        # Fetch more candidates to allow for reranking
        fetch_k = min(top_k * 3, count)
        embedding = self._embed([query])[0]
        backend_results = self.backend.search(embedding, fetch_k)

        search_results: list[tuple[BugCase, float]] = []
        if not backend_results:
            return []

        for entry in backend_results:
            case_id = entry["id"]
            score = entry["score"]

            if score < min_score:
                continue

            md_path = self.cases_dir / f"{case_id}.md"
            if not md_path.exists():
                continue

            case = _markdown_to_case(md_path)
            if case:
                if not include_unverified and not case.verified:
                    continue
                search_results.append((case, round(score, 4)))

        # Rerank: verified boost + hit_count log-weighted boost
        hc_weight = HIT_COUNT_WEIGHT

        def effective_score(item: tuple[BugCase, float]) -> float:
            case, score = item
            lexical = self._lexical_score(query, case)
            blended = (score * 0.75) + (lexical * 0.25)
            verified_mult = 1.0 if case.verified else 0.7
            hc_mult = 1.0 + math.log1p(case.hit_count) * hc_weight if hc_weight > 0 else 1.0
            stale_mult = 0.5 if self._is_stale(case) else 1.0
            return blended * verified_mult * hc_mult * stale_mult

        search_results.sort(key=effective_score, reverse=True)
        result_list = [SearchResult(case=case, score=score) for case, score in search_results]

        # Apply external reranker if configured
        if self.reranker is not None:
            result_list = self.reranker.rerank(query, result_list, top_k)

        return result_list[:top_k]

    def search_by_tags(self, tags: list[str], top_k: int = 10) -> list[SearchResult]:
        """Filter cases by tags — matches if ANY tag overlaps."""
        all_cases = self.list_recent(limit=10000)
        results = []
        for case in all_cases:
            if any(t in case.tags for t in tags):
                results.append(SearchResult(case=case, score=1.0))
                if len(results) >= top_k:
                    break
        return results

    # ── Read ───────────────────────────────────────────────────────

    def get(self, case_id: str) -> BugCase | None:
        md_path = self.cases_dir / f"{case_id}.md"
        return _markdown_to_case(md_path) if md_path.exists() else None

    def list_recent(self, limit: int = 20) -> list[BugCase]:
        """List most recent cases by file modification time."""
        md_files = sorted(
            self.cases_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        cases = []
        for f in md_files[:limit]:
            case = _markdown_to_case(f)
            if case:
                cases.append(case)
        return cases

    def stats(self) -> MemoryStats:
        cases = self.list_recent(limit=10000)
        by_severity: dict[str, int] = {}
        by_status: dict[str, int] = {}
        tag_counts: dict[str, int] = {}
        stale_count = 0
        hit_sum = 0

        for c in cases:
            by_severity[c.severity.value] = by_severity.get(c.severity.value, 0) + 1
            by_status[c.status.value] = by_status.get(c.status.value, 0) + 1
            for t in c.tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1
            if self._is_stale(c):
                stale_count += 1
            hit_sum += c.hit_count

        top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        avg_hit_rate = round(hit_sum / len(cases), 2) if cases else 0.0
        return MemoryStats(
            total_cases=len(cases),
            by_severity=by_severity,
            by_status=by_status,
            top_tags=top_tags,
            stale_count=stale_count,
            avg_hit_rate=avg_hit_rate,
        )

    def _is_stale(self, case: BugCase, days: int = 30) -> bool:
        if case.hit_count > 0:
            return False
        if case.last_used_at is None:
            return False
        age = (datetime.now(timezone.utc) - case.last_used_at).days
        return age >= days

    def decay(self, days: int = 30, dry_run: bool = False) -> dict:
        """Mark unused cases as stale. Returns count of stale cases."""
        cases = self.list_recent(limit=10000)
        stale_ids = [c.id for c in cases if self._is_stale(c, days=days)]
        result = {"stale_count": len(stale_ids), "stale_ids": stale_ids, "dry_run": dry_run}
        return result

    def reverify(self, days: int = 90) -> list[str]:
        """Return verified case IDs that haven't been re-verified in N days."""
        cases = self.list_recent(limit=10000)
        now = datetime.now(timezone.utc)
        stale_ids = []
        for c in cases:
            if not c.verified:
                continue
            if c.last_verified_at is None:
                stale_ids.append(c.id)
                continue
            if (now - c.last_verified_at).days >= days:
                stale_ids.append(c.id)
        return stale_ids

    def link(self, case_a: str, case_b: str, relation: str = "related") -> bool:
        """Link two cases with a relation type."""
        ca = self.get(case_a)
        cb = self.get(case_b)
        if not ca or not cb:
            return False
        ca.links.append({"case_id": case_b, "relation": relation})
        cb.links.append({"case_id": case_a, "relation": relation})
        self._save_to_markdown(ca)
        self._save_to_markdown(cb)
        return True

    def unlink(self, case_a: str, case_b: str) -> bool:
        """Remove all links between two cases."""
        ca = self.get(case_a)
        cb = self.get(case_b)
        if not ca or not cb:
            return False
        ca.links = [lk for lk in ca.links if lk["case_id"] != case_b]
        cb.links = [lk for lk in cb.links if lk["case_id"] != case_a]
        self._save_to_markdown(ca)
        self._save_to_markdown(cb)
        return True

    # ── Delete ────────────────────────────────────────────────────

    def delete(self, case_id: str) -> bool:
        """Delete a bug case from both vector store and Markdown."""
        md_path = self.cases_dir / f"{case_id}.md"
        if not md_path.exists():
            return False
        try:
            with self._lock:
                md_path.unlink()
                try:
                    self.backend.delete(ids=[case_id])
                except Exception:
                    pass
        except FileLockTimeout:
            raise MemoryBusyError("Memory is busy — another process is writing. Retry in a moment.")
        _log.info("case deleted", extra={"op": "delete", "case_id": case_id})
        return True

    # ── Feedback ──────────────────────────────────────────────────

    def mark_used(self, case_id: str) -> None:
        """Increment hit_count and update last_used_at for an adopted case."""
        case = self.get(case_id)
        if not case:
            return
        try:
            with self._lock:
                case.hit_count += 1
                case.last_used_at = datetime.now(timezone.utc)
                self._save_to_markdown(case)
                try:
                    self._save_to_vector(case)
                except Exception:
                    pass
        except FileLockTimeout:
            raise MemoryBusyError("Memory is busy — another process is writing. Retry in a moment.")
        _log.info("case marked used", extra={"op": "mark_used", "case_id": case_id})

    def verify(self, case_id: str, correct: bool, notes: str = "") -> bool:
        """Mark a case as verified (correct=True) or rejected (correct=False).

        Rejected cases get renamed to .rejected suffix and removed from vector store.
        """
        md_path = self.cases_dir / f"{case_id}.md"
        if not md_path.exists():
            return False

        case = _markdown_to_case(md_path)
        if not case:
            return False

        try:
            with self._lock:
                if correct:
                    case.verified = True
                    case.verification_notes = notes
                    case.updated_at = datetime.now(timezone.utc)
                    case.last_verified_at = datetime.now(timezone.utc)
                    case.verify_count = (case.verify_count or 0) + 1
                    self._save_to_markdown(case)
                    try:
                        self._save_to_vector(case)
                    except Exception:
                        pass
                else:
                    # Transactional reject: .pending → vector delete → .rejected
                    pending_path = self.cases_dir / f"{case_id}.md.rejected.pending"
                    rejected_path = self.cases_dir / f"{case_id}.md.rejected"
                    md_path.rename(pending_path)
                    try:
                        self.backend.delete(ids=[case_id])
                    except Exception:
                        pass
                    pending_path.rename(rejected_path)
        except FileLockTimeout:
            raise MemoryBusyError("Memory is busy — another process is writing. Retry in a moment.")
        _log.info("case verified", extra={"op": "verify", "case_id": case_id, "saved": correct})
        return True

    # ── Rebuild ────────────────────────────────────────────────────

    def rebuild_index(self) -> int:
        """Rebuild the vector index from all Markdown files. Returns count of indexed cases."""
        md_files = list(self.cases_dir.glob("*.md"))
        try:
            with self._lock:
                for md_path in md_files:
                    case = _markdown_to_case(md_path)
                    if case:
                        self._save_to_vector(case)
        except FileLockTimeout:
            raise MemoryBusyError("Memory is busy — another process is writing. Retry in a moment.")
        return len(md_files)

    # ── Cleanup ──────────────────────────────────────────────────

    def close(self) -> None:
        close = getattr(self.backend, "close", None)
        if callable(close):
            close()

    # ── Reconciliation ────────────────────────────────────────────

    def _cleanup_stale_tmps(self) -> int:
        """Remove .tmp files older than 10 minutes. Returns count removed."""
        import time

        now = time.time()
        removed = 0
        for tmp in self.cases_dir.glob("*.tmp"):
            try:
                age = now - tmp.stat().st_mtime
                if age > 600:
                    tmp.unlink()
                    removed += 1
            except OSError:
                pass
        # Also clean up .pending files older than 10 min
        for pending in self.cases_dir.glob("*.pending"):
            try:
                age = now - pending.stat().st_mtime
                if age > 600:
                    pending.unlink()
                    removed += 1
            except OSError:
                pass
        return removed

    def doctor(self, fix: bool = False, delete_orphans: bool = False) -> dict:
        """Diagnose and optionally fix inconsistencies between markdown and vector store.

        Returns dict with counts of issues found and fixed.
        """
        md_ids = {p.stem for p in self.cases_dir.glob("*.md")}

        # Get all vector IDs
        all_ids = set()
        count = self.backend.count()
        if count > 0:
            all_ids = set(self.backend.get_all_ids())

        missing_vectors = md_ids - all_ids
        orphan_vectors = all_ids - md_ids

        # Check for .pending files (incomplete verify(correct=False))
        pending_fixes = []
        for pending in self.cases_dir.glob("*.rejected.pending"):
            case_id = pending.stem.replace(".md", "").replace(".rejected", "")
            rejected_path = self.cases_dir / f"{case_id}.md.rejected"
            pending_fixes.append(
                {
                    "case_id": case_id,
                    "pending_path": str(pending),
                    "target_path": str(rejected_path),
                }
            )
            if fix:
                pending.rename(rejected_path)

        fixed_missing = 0
        if fix and missing_vectors:
            for case_id in missing_vectors:
                md_path = self.cases_dir / f"{case_id}.md"
                case = _markdown_to_case(md_path)
                if case:
                    try:
                        self._save_to_vector(case)
                        fixed_missing += 1
                    except Exception:
                        pass

        deleted_orphans = 0
        if fix and delete_orphans and orphan_vectors:
            for case_id in orphan_vectors:
                try:
                    self.backend.delete(ids=[case_id])
                    deleted_orphans += 1
                except Exception:
                    pass

        result = {
            "missing_vectors": len(missing_vectors),
            "orphan_vectors": len(orphan_vectors),
            "pending_rejected": len(pending_fixes),
            "fixed_missing": fixed_missing,
            "deleted_orphans": deleted_orphans,
            "fixed_pending": len(pending_fixes) if fix else 0,
        }
        _log.info("doctor check", extra={"op": "doctor", **result})
        return result


def _case_to_markdown(case: BugCase) -> str:
    """Serialize a BugCase to a human-readable Markdown file."""
    env_lines = "\n".join(f"- {k}: {v}" for k, v in case.environment.items())
    steps_lines = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(case.diagnosis_steps))
    tags_str = ", ".join(case.tags) if case.tags else "none"
    patch_attempts_json = json.dumps(case.patch_attempts, ensure_ascii=False, indent=2)

    return f"""# {case.title}

> case_id: `{case.id}` | severity: **{case.severity.value}** | status: **{case.status.value}**

## Environment
{env_lines or "- not specified"}

## Symptoms
{case.symptoms}

## Error Log
```
{case.error_log or "(no log provided)"}
```

## Root Cause
{case.root_cause or "(not yet diagnosed)"}

## Diagnosis Steps
{steps_lines or "- (pending)"}

## Fix Suggestion
{case.fix_suggestion or "(pending)"}

## Patch Attempts
```json
{patch_attempts_json}
```

## Tags
{tags_str}

---
- created: {case.created_at.isoformat()}
- updated: {case.updated_at.isoformat()}
- similar_cases: {json.dumps(case.similar_case_ids)}
- verified: {json.dumps(case.verified)}
- verification_notes: {case.verification_notes}
- hit_count: {case.hit_count}
- last_used_at: {case.last_used_at.isoformat() if case.last_used_at else ""}
- superseded_by: {case.superseded_by or ""}
- last_verified_at: {case.last_verified_at.isoformat() if case.last_verified_at else ""}
- verify_count: {case.verify_count}
- version: {case.version}
- links: {json.dumps(case.links, ensure_ascii=False)}
"""


_RE_STEP_PREFIX = re.compile(r"^\d+\.\s*")


def _markdown_to_case(path: Path) -> BugCase | None:
    """Parse a Markdown file back into a BugCase. Tolerant of missing fields."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    case = BugCase(title="parsed", symptoms="")

    if m := _RE_CASE_ID.search(text):
        case.id = m.group(1)
    else:
        case.id = path.stem

    if m := _RE_SEVERITY.search(text):
        try:
            case.severity = Severity(m.group(1))
        except ValueError:
            pass
    if m := _RE_STATUS.search(text):
        try:
            case.status = BugStatus(m.group(1))
        except ValueError:
            pass

    if m := _RE_TITLE.search(text):
        case.title = m.group(1).strip()

    def section(header: str) -> str:
        pat = _RE_SECTIONS.get(header)
        if pat and (m := pat.search(text)):
            return m.group(1).strip()
        return ""

    case.symptoms = section("Symptoms")
    case.root_cause = section("Root Cause")
    case.fix_suggestion = section("Fix Suggestion")

    patch_attempts_section = section("Patch Attempts")
    if patch_attempts_section:
        patch_attempts_json = _strip_json_fence(patch_attempts_section)
        try:
            raw_attempts = json.loads(patch_attempts_json) if patch_attempts_json else []
        except json.JSONDecodeError:
            raw_attempts = []
        if isinstance(raw_attempts, list):
            case.patch_attempts = [
                {str(k): str(v) for k, v in item.items() if v is not None}
                for item in raw_attempts
                if isinstance(item, dict)
            ]

    error_section = section("Error Log")
    case.error_log = (
        error_section.replace("```\n", "").replace("\n```", "").replace("(no log provided)", "")
    )

    env_section = section("Environment")
    for line in env_section.split("\n"):
        line = line.strip().lstrip("- ")
        if ": " in line:
            k, v = line.split(": ", 1)
            case.environment[k.strip()] = v.strip()

    steps_section = section("Diagnosis Steps")
    case.diagnosis_steps = [
        _RE_STEP_PREFIX.sub("", line.strip())
        for line in steps_section.split("\n")
        if line.strip() and line.strip() != "- (pending)"
    ]

    tags_section = section("Tags")
    if tags_section and tags_section != "none":
        case.tags = [t.strip() for t in tags_section.split(",")]

    if m := _RE_CREATED.search(text):
        try:
            case.created_at = datetime.fromisoformat(m.group(1))
        except ValueError:
            pass
    if m := _RE_UPDATED.search(text):
        try:
            case.updated_at = datetime.fromisoformat(m.group(1))
        except ValueError:
            pass

    if m := _RE_SIMILAR_CASES.search(text):
        try:
            case.similar_case_ids = json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    if m := _RE_VERIFIED.search(text):
        case.verified = m.group(1).lower() == "true"
    if m := _RE_VERIFICATION_NOTES.search(text):
        case.verification_notes = m.group(1).strip()
    if m := _RE_HIT_COUNT.search(text):
        case.hit_count = int(m.group(1))
    if m := _RE_LAST_USED_AT.search(text):
        val = m.group(1).strip()
        if val:
            try:
                case.last_used_at = datetime.fromisoformat(val)
            except ValueError:
                pass
    if m := _RE_SUPERSEDED_BY.search(text):
        val = m.group(1).strip()
        if val:
            case.superseded_by = val
    if m := _RE_LAST_VERIFIED_AT.search(text):
        val = m.group(1).strip()
        if val:
            try:
                case.last_verified_at = datetime.fromisoformat(val)
            except ValueError:
                pass
    if m := _RE_VERIFY_COUNT.search(text):
        case.verify_count = int(m.group(1))
    if m := _RE_VERSION.search(text):
        case.version = int(m.group(1))
    if m := _RE_LINKS.search(text):
        try:
            parsed_links = json.loads(m.group(1))
            if isinstance(parsed_links, list):
                case.links = [
                    {"case_id": str(item["case_id"]), "relation": str(item["relation"])}
                    for item in parsed_links
                    if isinstance(item, dict) and "case_id" in item and "relation" in item
                ]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    return case


def _strip_json_fence(text: str) -> str:
    """Return JSON content from a fenced Markdown block or raw JSON text."""
    lines = text.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
