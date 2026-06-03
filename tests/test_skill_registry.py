"""Tests for the Skill / SkillRegistry abstraction (P6-1)."""

from __future__ import annotations

import pytest

from debug_mind.agent import DiagnosticAgent
from debug_mind.memory.store import MemoryStore
from debug_mind.skills.builtin import CodebaseSkill, JvmSkill, MemorySkill
from debug_mind.skills.registry import (
    Skill,
    SkillRegistry,
    get_default_registry,
)


@pytest.fixture
def memory(tmp_path):
    return MemoryStore(memory_dir=tmp_path / "mem")


# ── Registry mechanics ─────────────────────────────────────────────────────


class _DummySkill(Skill):
    name = "dummy"
    description = "noop"

    def get_tools(self, context):
        return []

    def execute(self, tool_name, params, context):
        return {"ok": True}, None


class TestSkillRegistry:
    def test_register_and_get(self):
        reg = SkillRegistry()
        reg.register(_DummySkill())
        s = reg.get("dummy")
        assert s is not None
        assert s.name == "dummy"

    def test_register_rejects_empty_name(self):
        class Anon(Skill):
            name = ""
            description = "x"

            def get_tools(self, context):
                return []

            def execute(self, tool_name, params, context):
                return {}, None

        reg = SkillRegistry()
        with pytest.raises(ValueError, match="non-empty name"):
            reg.register(Anon())

    def test_register_rejects_duplicate(self):
        reg = SkillRegistry()
        reg.register(_DummySkill())
        with pytest.raises(ValueError, match="already registered"):
            reg.register(_DummySkill())

    def test_load_by_names_unknown(self):
        reg = SkillRegistry()
        reg.register(_DummySkill())
        with pytest.raises(ValueError, match="Unknown skill"):
            reg.load_by_names(["does-not-exist"])

    def test_load_by_names_returns_in_order(self):
        reg = SkillRegistry()
        reg.register(_DummySkill())

        class Other(Skill):
            name = "other"
            description = "other"

            def get_tools(self, context):
                return []

            def execute(self, tool_name, params, context):
                return {}, None

        reg.register(Other())
        loaded = reg.load_by_names(["other", "dummy"])
        assert [s.name for s in loaded] == ["other", "dummy"]


class TestDefaultRegistry:
    def test_has_builtin_skills(self):
        reg = get_default_registry()
        names = {s.name for s in reg.list_all()}
        assert {"memory", "codebase", "jvm"}.issubset(names)


# ── Built-in skill behaviour ───────────────────────────────────────────────


class TestMemorySkill:
    def test_exposes_memory_tools(self, memory):
        skill = MemorySkill()
        tools = skill.get_tools({"memory": memory, "project_path": None})
        names = {t["name"] for t in tools}
        assert {"search_memory", "save_to_memory"} == names

    def test_search_returns_search_tag(self, memory):
        skill = MemorySkill()
        result, tag = skill.execute(
            "search_memory", {"query": "anything"}, {"memory": memory, "project_path": None}
        )
        assert tag == "search"
        assert "cases" in result


class TestCodebaseSkill:
    def test_no_tools_without_project(self, memory):
        skill = CodebaseSkill()
        tools = skill.get_tools({"memory": memory, "project_path": None})
        assert tools == []

    def test_tools_with_project(self, memory, tmp_path):
        skill = CodebaseSkill()
        tools = skill.get_tools({"memory": memory, "project_path": str(tmp_path)})
        names = {t["name"] for t in tools}
        assert {"search_code", "read_file", "list_project_structure", "parse_symbols"} == names

    def test_execute_without_project_returns_error(self, memory):
        skill = CodebaseSkill()
        result, tag = skill.execute(
            "search_code", {"pattern": "x"}, {"memory": memory, "project_path": None}
        )
        assert "error" in result
        assert tag is None


class TestJvmSkill:
    def test_exposes_one_tool(self):
        skill = JvmSkill()
        tools = skill.get_tools({})
        names = [t["name"] for t in tools]
        assert names == ["search_jvm_stack_pattern"]

    def test_matches_npe(self):
        skill = JvmSkill()
        result, tag = skill.execute(
            "search_jvm_stack_pattern",
            {"text": "Caused by: java.lang.NullPointerException at com.example.Foo.bar"},
            {},
        )
        assert tag is None
        assert result["found"] >= 1
        # NPE should be the top hit
        assert result["patterns"][0]["id"] == "npe-basic"

    def test_matches_oom_heap(self):
        skill = JvmSkill()
        result, _ = skill.execute(
            "search_jvm_stack_pattern",
            {"text": "java.lang.OutOfMemoryError: Java heap space"},
            {},
        )
        # OOM heap should outrank generic OOM matches
        top_ids = [p["id"] for p in result["patterns"]]
        assert "oom-heap" in top_ids
        assert result["patterns"][0]["id"] == "oom-heap"

    def test_empty_for_unrelated_text(self):
        skill = JvmSkill()
        result, _ = skill.execute(
            "search_jvm_stack_pattern",
            {"text": "the cat sat on the mat"},
            {},
        )
        assert result["found"] == 0
        assert result["patterns"] == []

    def test_unknown_tool(self):
        skill = JvmSkill()
        result, _ = skill.execute("not_a_tool", {}, {})
        assert "error" in result


# ── Agent integration ──────────────────────────────────────────────────────


class TestAgentSkillLoading:
    def test_default_no_project_loads_only_memory(self, memory):
        agent = DiagnosticAgent(memory=memory, api_key="fake")
        names = {t["name"] for t in agent.tools}
        assert names == {"search_memory", "save_to_memory"}
        assert agent._skill_names == ["memory"]

    def test_default_with_project_loads_memory_and_codebase(self, memory, tmp_path):
        agent = DiagnosticAgent(memory=memory, project_path=str(tmp_path), api_key="fake")
        names = {t["name"] for t in agent.tools}
        assert {"search_memory", "save_to_memory"}.issubset(names)
        assert {"search_code", "read_file"}.issubset(names)
        assert agent._skill_names == ["memory", "codebase", "patch"]

    def test_explicit_skills_bypasses_auto_codebase(self, memory, tmp_path):
        # Even with project_path, explicit ["memory"] must NOT add codebase
        agent = DiagnosticAgent(
            memory=memory,
            project_path=str(tmp_path),
            api_key="fake",
            skills=["memory"],
        )
        names = {t["name"] for t in agent.tools}
        assert names == {"search_memory", "save_to_memory"}

    def test_explicit_skills_memory_jvm(self, memory):
        agent = DiagnosticAgent(memory=memory, api_key="fake", skills=["memory", "jvm"])
        names = {t["name"] for t in agent.tools}
        assert names == {"search_memory", "save_to_memory", "search_jvm_stack_pattern"}

    def test_unknown_skill_raises_value_error(self, memory):
        with pytest.raises(ValueError, match="Unknown skill"):
            DiagnosticAgent(memory=memory, api_key="fake", skills=["nope"])

    def test_execute_tool_dispatches_to_jvm_skill(self, memory):
        agent = DiagnosticAgent(memory=memory, api_key="fake", skills=["memory", "jvm"])
        result, tag = agent._execute_tool(
            "search_jvm_stack_pattern",
            {"text": "java.lang.StackOverflowError"},
        )
        assert tag is None
        assert result["found"] >= 1
        assert result["patterns"][0]["id"] == "stack-overflow"
