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

DEFAULT_MEMORY_DIR = Path(os.environ.get("DEBUG_MIND_MEMORY_DIR", "memory"))
DEDUP_THRESHOLD = float(os.environ.get("DEBUG_MIND_DEDUP_THRESHOLD", "0.92"))
HIT_COUNT_WEIGHT = float(os.environ.get("DEBUG_MIND_HIT_COUNT_WEIGHT", "0.05"))


def _fallback_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic fallback embedding using character trigram hashing.

    Used when the ONNX model fails to download (e.g. CI without internet).
    Produces 384-dim vectors — same dimensionality as all-MiniLM-L6-v2.
    """
    import hashlib

    dim = 384
    vectors = []
    for text in texts:
        vec = [0.0] * dim
        # Hash trigrams to fill the vector
        text_lower = text.lower()
        for i in range(len(text_lower) - 2):
            trigram = text_lower[i : i + 3]
            h = int(hashlib.md5(trigram.encode()).hexdigest(), 16)
            idx = h % dim
            vec[idx] += 0.01
        # Normalize
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        vectors.append(vec)
    return vectors


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
    ):
        self.memory_dir = Path(memory_dir) if memory_dir else DEFAULT_MEMORY_DIR
        self.cases_dir = self.memory_dir / "cases"
        self.cases_dir.mkdir(parents=True, exist_ok=True)
        self.reranker = reranker
        self._embedding_fn = embedding_fn
        self._cached_embed_fn = None

        self._lock = FileLock(str(self.memory_dir / ".lock"), timeout=30)

        self.backend = self._create_backend()
        self.backend.initialize()

        self._cleanup_stale_tmps()

    def _create_backend(self):
        backend_choice = os.environ.get("DEBUG_MIND_BACKEND", "chroma").lower()
        if backend_choice == "sqlite":
            from debug_mind.memory.backends.sqlite_backend import SQLiteBackend

            return SQLiteBackend(self.memory_dir)
        from debug_mind.memory.backends.chroma_backend import ChromaBackend

        return ChromaBackend(self.memory_dir)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Embed one or more texts, returning a list of embedding vectors."""
        if self._embedding_fn is not None:
            return self._embedding_fn(texts)
        if self._cached_embed_fn is None:
            from debug_mind.memory.embeddings import default_embedding

            try:
                self._cached_embed_fn = default_embedding()
            except Exception as e:
                _log.warning(
                    "Failed to init embedding function, using fallback", extra={"error": str(e)}
                )
                self._cached_embed_fn = _fallback_embed
        try:
            return self._cached_embed_fn(texts)
        except Exception:
            return _fallback_embed(texts)

    # ── Write ──────────────────────────────────────────────────────

    def save(self, case: BugCase) -> BugCase:
        """Persist a bug case. Deduplicates against verified existing cases."""
        # Defense-in-depth: sanitize all text inputs at the store level
        # so even callers that skip sanitization are protected.
        from debug_mind.sanitize import (
            sanitize_description,
            sanitize_error_log,
            sanitize_environment,
            sanitize_tags,
        )

        case.title = sanitize_description(case.title)
        case.symptoms = sanitize_description(case.symptoms)
        case.root_cause = sanitize_description(case.root_cause)
        case.fix_suggestion = sanitize_description(case.fix_suggestion)
        case.error_log = sanitize_error_log(case.error_log)
        case.environment = sanitize_environment(case.environment)
        case.tags = sanitize_tags(case.tags)

        try:
            with self._lock:
                case.updated_at = datetime.now(timezone.utc)
                case.version = (case.version or 1) + 1

                existing = self._find_dedup_target(case)
                if existing:
                    return existing

                self._save_to_markdown(case)

                try:
                    self._save_to_vector(case)
                except Exception as e:
                    _log.warning(f"Vector upsert failed for {case.id}: {e}")
        except FileLockTimeout:
            raise MemoryBusyError("Memory is busy — another process is writing. Retry in a moment.")
        _log.info("case saved", extra={"op": "save", "case_id": case.id})
        return case

    def _find_dedup_target(self, case: BugCase) -> BugCase | None:
        """Check if a verified case with high similarity already exists.

        Only merges against verified cases — unverified cases are kept separate
        to preserve diversity (wrong diagnoses may look similar to correct ones).
        """
        count = self.backend.count()
        if count == 0:
            return None

        query_text = case.to_search_text()
        embedding = self._embed([query_text])[0]
        results = self.backend.search(embedding, min(3, count))

        if not results:
            return None

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
            return existing

        return None

    def _save_to_vector(self, case: BugCase) -> None:
        search_text = case.to_search_text()
        embedding = self._embed([search_text])[0]
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

    # ── Search ─────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.3,
        include_unverified: bool = True,
    ) -> list[SearchResult]:
        """Search for similar bug cases by semantic similarity.

        Results are reranked: verified cases get a boost (effective_score = score * 1.0
        for verified, score * 0.7 for unverified), but the returned score field
        contains the original cosine similarity for evaluation transparency.
        """
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
            verified_mult = 1.0 if case.verified else 0.7
            hc_mult = 1.0 + math.log1p(case.hit_count) * hc_weight if hc_weight > 0 else 1.0
            stale_mult = 0.5 if self._is_stale(case) else 1.0
            return score * verified_mult * hc_mult * stale_mult

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
"""


def _markdown_to_case(path: Path) -> BugCase | None:
    """Parse a Markdown file back into a BugCase. Tolerant of missing fields."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    case = BugCase(title="parsed", symptoms="")

    # Extract case_id from backtick
    if m := re.search(r"case_id:\s*`(\w+)`", text):
        case.id = m.group(1)
    else:
        case.id = path.stem

    # Extract severity and status
    if m := re.search(r"severity:\s*\*\*(\w+)\*\*", text):
        try:
            case.severity = Severity(m.group(1))
        except ValueError:
            pass
    if m := re.search(r"status:\s*\*\*(\w+)\*\*", text):
        try:
            case.status = BugStatus(m.group(1))
        except ValueError:
            pass

    # Title from H1
    if m := re.search(r"^# (.+)$", text, re.MULTILINE):
        case.title = m.group(1).strip()

    # Sections — extract between ## headings (stop at next ## or --- separator)
    def section(header: str) -> str:
        pattern = rf"## {header}\s*\n(.*?)(?=\n## |\n---)"
        if m := re.search(pattern, text, re.DOTALL):
            return m.group(1).strip()
        return ""

    case.symptoms = section("Symptoms")
    case.root_cause = section("Root Cause")
    case.fix_suggestion = section("Fix Suggestion")

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
        re.sub(r"^\d+\.\s*", "", line.strip())
        for line in steps_section.split("\n")
        if line.strip() and line.strip() != "- (pending)"
    ]

    tags_section = section("Tags")
    if tags_section and tags_section != "none":
        case.tags = [t.strip() for t in tags_section.split(",")]

    # Timestamps
    if m := re.search(r"created:\s*(\S+)", text):
        try:
            case.created_at = datetime.fromisoformat(m.group(1))
        except ValueError:
            pass
    if m := re.search(r"updated:\s*(\S+)", text):
        try:
            case.updated_at = datetime.fromisoformat(m.group(1))
        except ValueError:
            pass

    # Similar cases
    if m := re.search(r"similar_cases:\s*(\[.*?\])", text):
        try:
            case.similar_case_ids = json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Feedback fields (tolerant of missing — old files have defaults)
    # These use "- key: value" format in the metadata section
    if m := re.search(r"^- verified:\s*(true|false)", text, re.MULTILINE | re.IGNORECASE):
        case.verified = m.group(1).lower() == "true"
    if m := re.search(r"^- verification_notes:\s*(.+)", text, re.MULTILINE):
        notes = m.group(1).strip()
        case.verification_notes = notes
    if m := re.search(r"^- hit_count:\s*(\d+)", text, re.MULTILINE):
        case.hit_count = int(m.group(1))
    if m := re.search(r"^- last_used_at:\s*(\S+)", text, re.MULTILINE):
        val = m.group(1).strip()
        if val:
            try:
                case.last_used_at = datetime.fromisoformat(val)
            except ValueError:
                pass
    if m := re.search(r"^- superseded_by:\s*(\S+)", text, re.MULTILINE):
        val = m.group(1).strip()
        if val:
            case.superseded_by = val

    return case
