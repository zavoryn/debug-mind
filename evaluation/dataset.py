"""Benchmark dataset models — defines the structure of evaluation test cases.

Each BenchmarkCase describes a bug scenario that the memory store should be
able to recall relevant seed cases for. Paired with seed_cases (markdown files
in evaluation/seed_cases/), this forms the evaluation dataset.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

CASES_DIR = Path(__file__).parent / "cases"


class BenchmarkCase(BaseModel):
    """A single evaluation test case — a bug scenario to test memory recall against."""

    id: str = Field(description="Unique identifier, matches the YAML filename")
    bug_description: str = Field(description="Bug report text (the search query)")
    error_log: str = Field(default="", description="Raw error log / stack trace snippet")
    environment: dict[str, str] = Field(default_factory=dict)
    seed_case_ids: list[str] = Field(
        default_factory=list,
        description="Seed case IDs that should be injected into memory before evaluation",
    )
    expected_root_cause_keywords: list[str] = Field(
        description="Keywords that a correct recall result's root_cause should contain (lowercase)",
    )
    expected_fix_keywords: list[str] = Field(
        description="Keywords that a correct recall result's fix_suggestion should contain (lowercase)",
    )
    expected_top_hit_id: str | None = Field(
        default=None,
        description="If seed cases are loaded, the expected #1 hit's case ID",
    )
    source: str | None = Field(default=None, description="Public URL to original bug report")
    synthetic: bool = Field(default=False, description="Whether this is a hand-crafted case")
    language: str | None = Field(default=None, description="Primary programming language")
    category: str | None = Field(
        default=None, description="Error category (npe, oom, deadlock, etc.)"
    )


def load_all_cases(cases_dir: Path | None = None) -> list[BenchmarkCase]:
    """Load all benchmark cases from YAML files in the cases directory."""
    directory = cases_dir or CASES_DIR
    cases = []
    for yaml_file in sorted(directory.glob("*.yaml")):
        data = _load_yaml(yaml_file)
        cases.append(BenchmarkCase(**data))
    return cases


def load_case(case_id: str, cases_dir: Path | None = None) -> BenchmarkCase | None:
    """Load a single benchmark case by ID (matches filename without extension)."""
    directory = cases_dir or CASES_DIR
    yaml_file = directory / f"{case_id}.yaml"
    if not yaml_file.exists():
        return None
    data = _load_yaml(yaml_file)
    return BenchmarkCase(**data)


def _load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid benchmark case file: {path}")
    data.setdefault("id", path.stem)
    return data
