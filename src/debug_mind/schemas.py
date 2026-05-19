"""Core data models for DebugMind — the data contract of the entire system."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class BugStatus(str, Enum):
    REPORTED = "reported"
    DIAGNOSING = "diagnosing"
    ROOT_CAUSE_FOUND = "root_cause_found"
    FIXED = "fixed"
    UNRESOLVED = "unresolved"


class BugCase(BaseModel):
    """A single bug diagnosis case — the unit of memory.

    This is what gets persisted to both the vector store and Markdown files.
    Every field is designed for both human readability and semantic searchability.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = Field(description="Short, searchable title (e.g. 'NPE in UserService.login')")

    # Context
    environment: dict[str, str] = Field(
        default_factory=dict,
        description="Runtime context: language, framework, version, module, etc.",
        examples=[{"language": "Java", "framework": "Spring Boot 3.2", "jdk": "17"}],
    )
    symptoms: str = Field(description="What the user observed: error messages, stack traces, behavior")
    error_log: str = Field(default="", description="Raw error log / stack trace snippet")

    # Diagnosis
    root_cause: str = Field(default="", description="The underlying cause identified")
    diagnosis_steps: list[str] = Field(
        default_factory=list,
        description="Ordered list of diagnostic steps taken",
    )
    fix_suggestion: str = Field(default="", description="Suggested fix or actual commit")
    severity: Severity = Severity.MEDIUM
    status: BugStatus = BugStatus.REPORTED

    # Metadata
    tags: list[str] = Field(default_factory=list, description="Searchable tags: npe, redis, spring-boot...")
    similar_case_ids: list[str] = Field(
        default_factory=list,
        description="IDs of similar past cases found during diagnosis",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Feedback & dedup fields
    verified: bool = Field(default=False, description="Confirmed correct by user/author")
    verification_notes: str = Field(default="", description="Verification or refutation notes")
    hit_count: int = Field(default=0, description="Times this case was retrieved and adopted by agent")
    last_used_at: datetime | None = Field(default=None, description="Last time this case was adopted")
    superseded_by: str | None = Field(default=None, description="ID of the case that replaced this one")

    def to_search_text(self) -> str:
        """Build a single text blob for embedding — includes all searchable content."""
        parts = [
            f"Title: {self.title}",
            f"Symptoms: {self.symptoms}",
            f"Error: {self.error_log}",
            f"Root Cause: {self.root_cause}",
            f"Tags: {', '.join(self.tags)}",
            f"Environment: {', '.join(f'{k}={v}' for k, v in self.environment.items())}",
        ]
        return "\n".join(p for p in parts if p and not p.endswith(": "))


class DiagnosisResult(BaseModel):
    """Output of a single diagnosis run — what the agent produces."""

    case_id: str
    root_cause: str
    confidence: float = Field(
        ge=0, le=1,
        description="1.0 if case was saved to memory, 0.0 if agent failed to save",
    )
    diagnosis_steps: list[str]
    fix_suggestion: str
    similar_cases_found: int = 0
    reasoning: str = Field(default="", description="Agent's chain-of-thought summary")


class SearchResult(BaseModel):
    """A single result from memory search."""

    case: BugCase
    score: float = Field(description="Similarity score [0, 1], higher = more similar")


class MemoryStats(BaseModel):
    """Stats about the memory store."""

    total_cases: int
    by_severity: dict[str, int]
    by_status: dict[str, int]
    top_tags: list[tuple[str, int]]
