"""Memory Store — the core of DebugMind's experiential memory.

Hybrid storage: ChromaDB for vector similarity search + Markdown files for
human-readable persistence and git-based knowledge sharing.

Design decisions:
- ChromaDB is embedded (zero infra) and uses a local embedding model by default.
- Every write goes to both ChromaDB and a Markdown file.
- Markdown is the source of truth — ChromaDB can be rebuilt from it.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from datetime import datetime, timezone

import chromadb

from debug_mind.schemas import BugCase, SearchResult, MemoryStats, Severity, BugStatus

COLLECTION_NAME = "bug_cases"
DEFAULT_MEMORY_DIR = Path(os.environ.get("DEBUG_MIND_MEMORY_DIR", "memory"))


class MemoryStore:
    """Persistent bug memory with vector similarity search."""

    def __init__(self, memory_dir: Path | str | None = None):
        self.memory_dir = Path(memory_dir) if memory_dir else DEFAULT_MEMORY_DIR
        self.cases_dir = self.memory_dir / "cases"
        self.cases_dir.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(path=str(self.memory_dir / "chroma"))
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    # ── Write ──────────────────────────────────────────────────────

    def save(self, case: BugCase) -> BugCase:
        """Persist a bug case to both vector store and Markdown file."""
        case.updated_at = datetime.now(timezone.utc)

        self._save_to_vector(case)
        self._save_to_markdown(case)

        return case

    def _save_to_vector(self, case: BugCase) -> None:
        search_text = case.to_search_text()
        metadata = {
            "severity": case.severity.value,
            "status": case.status.value,
            "tags": json.dumps(case.tags),
            "created_at": case.created_at.isoformat(),
        }

        self.collection.upsert(
            ids=[case.id],
            documents=[search_text],
            metadatas=[metadata],
        )

    def _save_to_markdown(self, case: BugCase) -> None:
        md_path = self.cases_dir / f"{case.id}.md"
        content = _case_to_markdown(case)
        md_path.write_text(content, encoding="utf-8")

    # ── Search ─────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5, min_score: float = 0.3) -> list[SearchResult]:
        """Search for similar bug cases by semantic similarity.

        Returns results sorted by similarity score (highest first).
        """
        count = self.collection.count()
        if count == 0:
            return []

        results = self.collection.query(
            query_texts=[query],
            n_results=min(top_k, count),
            include=["documents", "metadatas", "distances"],
        )

        search_results = []
        if not results["ids"] or not results["ids"][0]:
            return search_results

        for i, case_id in enumerate(results["ids"][0]):
            distance = results["distances"][0][i]
            score = 1 - distance  # cosine distance → similarity

            if score < min_score:
                continue

            md_path = self.cases_dir / f"{case_id}.md"
            if not md_path.exists():
                continue

            case = _markdown_to_case(md_path)
            if case:
                search_results.append(SearchResult(case=case, score=round(score, 4)))

        search_results.sort(key=lambda r: r.score, reverse=True)
        return search_results

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
        md_files = sorted(self.cases_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
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

        for c in cases:
            by_severity[c.severity.value] = by_severity.get(c.severity.value, 0) + 1
            by_status[c.status.value] = by_status.get(c.status.value, 0) + 1
            for t in c.tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1

        top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        return MemoryStats(
            total_cases=len(cases),
            by_severity=by_severity,
            by_status=by_status,
            top_tags=top_tags,
        )

    # ── Delete ────────────────────────────────────────────────────

    def delete(self, case_id: str) -> bool:
        """Delete a bug case from both vector store and Markdown."""
        md_path = self.cases_dir / f"{case_id}.md"
        if not md_path.exists():
            return False
        md_path.unlink()
        try:
            self.collection.delete(ids=[case_id])
        except Exception:
            pass
        return True

    # ── Rebuild ────────────────────────────────────────────────────

    def rebuild_index(self) -> int:
        """Rebuild the vector index from all Markdown files. Returns count of indexed cases."""
        md_files = list(self.cases_dir.glob("*.md"))
        for md_path in md_files:
            case = _markdown_to_case(md_path)
            if case:
                self._save_to_vector(case)
        return len(md_files)


# ── Markdown ↔ BugCase serialization ───────────────────────────────

def _case_to_markdown(case: BugCase) -> str:
    """Serialize a BugCase to a human-readable Markdown file."""
    env_lines = "\n".join(f"- {k}: {v}" for k, v in case.environment.items())
    steps_lines = "\n".join(f"{i+1}. {s}" for i, s in enumerate(case.diagnosis_steps))
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
    case.error_log = error_section.replace("```\n", "").replace("\n```", "").replace("(no log provided)", "")

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

    return case
