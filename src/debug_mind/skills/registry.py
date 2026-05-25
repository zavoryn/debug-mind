"""Skill registry — pluggable skill packs for the diagnostic agent.

Why this exists:
The original DiagnosticAgent hard-coded `MEMORY_TOOLS + CODEBASE_TOOLS` at
construction time, so adding a new domain (JVM stack analysis, K8s diagnostics,
frontend errors, etc.) required modifying the agent. This module introduces a
`Skill` abstraction so the agent can be composed from named skill packs at
runtime via `DiagnosticAgent(skills=["memory", "jvm"])` or the
`--skills memory,jvm` CLI flag.

Built-in skills are registered eagerly in `get_default_registry()`. External
plugin tools (loaded via DEBUG_MIND_PLUGINS) remain a separate path handled
by `debug_mind.plugins` — that mechanism is not consolidated here on purpose,
to keep this change incremental.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Skill(ABC):
    """A named pack of tools the agent can load.

    Subclasses provide:
    - `name`: identifier used by `--skills` and `load_by_names`
    - `description`: short text shown by `debug-mind` introspection
    - `get_tools(context)`: Anthropic tool schemas this skill contributes
      (may depend on runtime context, e.g. whether a project_path is set)
    - `execute(tool_name, params, context)`: run a tool, return
      (result_dict, side_effect_tag).
    """

    name: str = ""
    description: str = ""

    @abstractmethod
    def get_tools(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Return tool schemas this skill exposes given the runtime context.

        Context keys the agent passes today:
            memory: MemoryStore instance
            project_path: str | None
        """

    @abstractmethod
    def execute(
        self, tool_name: str, params: dict[str, Any], context: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        """Execute one tool call. Return (result, side_effect_tag).

        side_effect_tag mirrors the existing agent contract:
            "search" → result contains list of cases (similar_case_ids tracked)
            "save"   → result contains case_id (saved_case_id tracked)
            None     → no side effect
        """


class SkillRegistry:
    """Holds registered skills, keyed by name."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if not skill.name:
            raise ValueError("Skill must have a non-empty name")
        if skill.name in self._skills:
            raise ValueError(f"Skill already registered: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_all(self) -> list[Skill]:
        return list(self._skills.values())

    def load_by_names(self, names: list[str]) -> list[Skill]:
        """Resolve names to Skill instances. Raise ValueError for unknown skills."""
        loaded: list[Skill] = []
        for n in names:
            n = n.strip()
            if not n:
                continue
            skill = self.get(n)
            if skill is None:
                available = ", ".join(sorted(self._skills.keys()))
                raise ValueError(
                    f"Unknown skill: {n!r}. Available skills: {available}"
                )
            loaded.append(skill)
        return loaded


_default_registry: SkillRegistry | None = None


def get_default_registry() -> SkillRegistry:
    """Return the process-wide default registry, populated with built-in skills.

    Lazy-initialised on first call to avoid import-cycle issues (skills import
    from memory.store / tools.schemas which import other modules).
    """
    global _default_registry
    if _default_registry is None:
        registry = SkillRegistry()
        # Import here to break a potential cycle with builtin.py
        from debug_mind.skills.builtin import (
            CodebaseSkill,
            JvmSkill,
            MemorySkill,
        )

        registry.register(MemorySkill())
        registry.register(CodebaseSkill())
        registry.register(JvmSkill())
        _default_registry = registry
    return _default_registry


def reset_default_registry_for_tests() -> None:
    """Test hook — clears the cached default registry so the next call rebuilds.

    Useful when a test wants to register an extra skill or simulate a clean state.
    """
    global _default_registry
    _default_registry = None
