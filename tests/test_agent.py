"""Tests for the DiagnosticAgent — tool dispatch and message building, no API calls."""

import pytest

from debug_mind.agent import DiagnosticAgent, MEMORY_TOOLS, CODEBASE_TOOLS
from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import BugCase, Severity


@pytest.fixture
def memory(tmp_path):
    store = MemoryStore(memory_dir=tmp_path / "mem")
    return store


@pytest.fixture
def agent(memory):
    return DiagnosticAgent(memory=memory, api_key="fake-key")


@pytest.fixture
def agent_with_project(memory, tmp_path):
    return DiagnosticAgent(
        memory=memory, project_path=str(tmp_path / "project"), api_key="fake-key"
    )


class TestToolDefinitions:
    def test_memory_tools_structure(self):
        for tool in MEMORY_TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert tool["input_schema"]["type"] == "object"

    def test_codebase_tools_structure(self):
        for tool in CODEBASE_TOOLS:
            assert "name" in tool
            assert "input_schema" in tool

    def test_agent_has_memory_tools(self, agent):
        tool_names = [t["name"] for t in agent.tools]
        assert "search_memory" in tool_names
        assert "save_to_memory" in tool_names

    def test_agent_without_project_lacks_codebase_tools(self, agent):
        tool_names = [t["name"] for t in agent.tools]
        assert "search_code" not in tool_names

    def test_agent_with_project_has_codebase_tools(self, agent_with_project):
        tool_names = [t["name"] for t in agent_with_project.tools]
        assert "search_code" in tool_names
        assert "read_file" in tool_names
        assert "list_project_structure" in tool_names


class TestToolDispatch:
    def test_search_memory(self, agent):
        result, tag = agent._execute_tool("search_memory", {"query": "NPE"})
        assert tag == "search"
        assert "found" in result
        assert "cases" in result
        assert result["found"] == 0

    def test_search_memory_with_existing_case(self, agent, memory):
        case = BugCase(
            title="NPE in login",
            symptoms="500 error",
            root_cause="null pointer",
            fix_suggestion="add null check",
            severity=Severity.HIGH,
            tags=["npe"],
        )
        memory.save(case)

        result, tag = agent._execute_tool("search_memory", {"query": "NPE login"})
        assert tag == "search"
        assert result["found"] >= 1

    def test_save_to_memory(self, agent):
        result, tag = agent._execute_tool(
            "save_to_memory",
            {
                "title": "Test Bug",
                "symptoms": "crash",
                "root_cause": "bad code",
                "fix_suggestion": "fix it",
                "severity": "high",
                "tags": ["test"],
            },
        )
        assert tag == "save"
        assert result["saved"] is True
        assert "case_id" in result

    def test_save_to_memory_stores_in_memory(self, agent, memory):
        result, _ = agent._execute_tool(
            "save_to_memory",
            {
                "title": "Persisted Bug",
                "symptoms": "error",
                "root_cause": "typo",
                "fix_suggestion": "fix typo",
            },
        )
        case_id = result["case_id"]
        retrieved = memory.get(case_id)
        assert retrieved is not None
        assert retrieved.title == "Persisted Bug"

    def test_search_code_no_project(self, agent):
        result, tag = agent._execute_tool("search_code", {"pattern": "test"})
        assert result.get("error")
        assert tag is None

    def test_read_file_no_project(self, agent):
        result, tag = agent._execute_tool("read_file", {"file_path": "main.py"})
        assert result.get("error")
        assert tag is None

    def test_unknown_tool(self, agent):
        result, tag = agent._execute_tool("nonexistent_tool", {})
        assert "error" in result
        assert tag is None


class TestBuildUserMessage:
    def test_basic_message(self, agent):
        msg = agent._build_user_message("NPE on login", "", None)
        assert "NPE on login" in msg
        assert "no log provided" in msg
        assert "not specified" in msg

    def test_with_log(self, agent):
        msg = agent._build_user_message("crash", "NullPointerException at line 42", None)
        assert "NullPointerException at line 42" in msg

    def test_with_environment(self, agent):
        msg = agent._build_user_message("bug", "", {"java": "17", "framework": "Spring Boot"})
        assert "java: 17" in msg
        assert "framework: Spring Boot" in msg

    def test_with_project_path(self, agent_with_project):
        msg = agent_with_project._build_user_message("bug", "", None)
        assert "Project Path" in msg

    def test_without_project_path(self, agent):
        msg = agent._build_user_message("bug", "", None)
        assert "Project Path" not in msg
