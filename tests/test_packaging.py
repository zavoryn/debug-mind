"""Tests for package metadata and optional dependency declarations."""

from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
from pathlib import Path


def test_openai_extra_declared():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]

    assert "openai" in extras
    assert any(dep.startswith("openai") for dep in extras["openai"])


def test_embedding_extras_declared():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]

    assert "embeddings" in extras
    assert any(dep.startswith("openai") for dep in extras["embeddings"])
    assert any(dep.startswith("voyageai") for dep in extras["embeddings"])
    assert any(dep.startswith("sentence-transformers") for dep in extras["embeddings"])
