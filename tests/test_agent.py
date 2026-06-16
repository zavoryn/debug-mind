"""Tests for the DiagnosticAgent — tool dispatch and message building, no API calls."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from debug_mind.agent import DiagnosticAgent, MEMORY_TOOLS, CODEBASE_TOOLS
from debug_mind.memory.store import MemoryStore
from debug_mind.schemas import BugCase, BugStatus, Severity


def _mock_response(text: str = "", tool_uses: list[dict] | None = None):
    content = []
    if text:
        content.append(SimpleNamespace(type="text", text=text))
    for tool_use in tool_uses or []:
        content.append(
            SimpleNamespace(
                type="tool_use",
                id=tool_use["id"],
                name=tool_use["name"],
                input=tool_use["input"],
            )
        )
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    return SimpleNamespace(content=content, usage=usage, stop_reason="end_turn")


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


class TestToolParamValidation:
    """_validate_tool_params + _execute_tool self-correction behaviour."""

    def test_missing_required_field_returns_error_not_raise(self, agent):
        """LLM omits a required field → structured error dict, no exception."""
        result, side_effect = agent._execute_tool("search_memory", {})
        assert "error" in result
        assert "query" in result["error"]  # tells model which field is missing
        assert "hint" in result            # nudges model to self-correct
        assert side_effect is None

    def test_wrong_type_returns_error_not_raise(self, agent):
        """LLM sends wrong type → structured error dict, no exception."""
        result, side_effect = agent._execute_tool("search_memory", {"query": 999})
        assert "error" in result
        assert "string" in result["error"]

    def test_valid_params_execute_normally(self, agent):
        """Valid params bypass validation and execute the real tool."""
        result, side_effect = agent._execute_tool(
            "search_memory", {"query": "redis connection error"}
        )
        # Should succeed (returns found/cases, not an error)
        assert "error" not in result
        assert "found" in result
        assert side_effect == "search"

    def test_unknown_tool_still_errors(self, agent):
        """Unknown tool name returns error (unchanged behaviour)."""
        result, _ = agent._execute_tool("nonexistent_tool", {})
        assert "error" in result
        assert "Unknown tool" in result["error"]

    def test_validate_tool_params_directly(self):
        """Unit test for _validate_tool_params."""
        from debug_mind.agent import _validate_tool_params

        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
            },
            "required": ["query"],
        }

        # Missing required
        err = _validate_tool_params("search_memory", {}, schema)
        assert err is not None and "query" in err

        # Wrong type
        err = _validate_tool_params("search_memory", {"query": 123}, schema)
        assert err is not None and "string" in err

        # Valid
        assert _validate_tool_params("search_memory", {"query": "test"}, schema) is None

        # No schema = no validation
        assert _validate_tool_params("any", {"x": 1}, {}) is None


class TestPatchAttemptState:
    def test_failed_patch_attempt_is_merged_into_save_to_memory(self, memory, tmp_path):
        agent = DiagnosticAgent(
            memory=memory,
            project_path=str(tmp_path / "project"),
            api_key="fake-key",
        )
        failed_diff = "--- a/calculator.py\n+++ b/calculator.py\n@@\n-return a * b\n+return 0\n"
        failed_output = "AssertionError: expected 2, got 0"
        captured_save_params: dict = {}

        responses = [
            _mock_response(
                tool_uses=[
                    {
                        "id": "patch-1",
                        "name": "apply_and_test",
                        "input": {
                            "file_path": "calculator.py",
                            "patch_diff": failed_diff,
                            "test_command": "pytest test_calculator.py",
                        },
                    }
                ]
            ),
            _mock_response(
                tool_uses=[
                    {
                        "id": "save-1",
                        "name": "save_to_memory",
                        "input": {
                            "title": "calculator divide bug",
                            "symptoms": "divide returns wrong result",
                            "root_cause": "divide uses the wrong arithmetic operator",
                            "fix_suggestion": "use division instead of multiplication",
                        },
                    }
                ]
            ),
            _mock_response(text="final answer"),
        ]

        def fake_execute(name, params):
            if name == "apply_and_test":
                return (
                    {
                        "passed": False,
                        "output": failed_output,
                        "patch_diff": failed_diff,
                        "return_code": 1,
                    },
                    "patch_test",
                )
            if name == "save_to_memory":
                captured_save_params.update(params)
                return {"saved": True, "case_id": "saved-case"}, "save"
            return {"error": f"unexpected tool {name}"}, None

        with patch.object(agent.provider, "create_message", side_effect=responses):
            with patch.object(agent, "_execute_tool", side_effect=fake_execute):
                agent.diagnose("divide returns wrong result")

        attempts = captured_save_params["patch_attempts"]
        assert len(attempts) == 1
        assert attempts[0]["diff"] == failed_diff
        assert attempts[0]["test_output"] == failed_output
        assert attempts[0]["reason"] == "tests failed"

    def test_failed_patch_attempt_is_saved_when_model_never_saves(self, memory, tmp_path):
        agent = DiagnosticAgent(
            memory=memory,
            project_path=str(tmp_path / "project"),
            api_key="fake-key",
        )
        failed_diff = "--- a/service.py\n+++ b/service.py\n@@\n-raise err\n+pass\n"
        failed_output = "FAILED tests/test_service.py::test_error_is_not_swallowed"
        responses = [
            _mock_response(
                tool_uses=[
                    {
                        "id": "patch-1",
                        "name": "apply_and_test",
                        "input": {
                            "file_path": "service.py",
                            "patch_diff": failed_diff,
                            "test_command": "pytest tests/test_service.py",
                        },
                    }
                ]
            ),
            _mock_response(text="The patch failed; no final fix yet."),
        ]

        def fake_execute(name, params):
            if name == "apply_and_test":
                return (
                    {
                        "passed": False,
                        "output": failed_output,
                        "patch_diff": failed_diff,
                        "return_code": 1,
                    },
                    "patch_test",
                )
            return {"error": f"unexpected tool {name}"}, None

        with patch.object(agent.provider, "create_message", side_effect=responses):
            with patch.object(agent, "_execute_tool", side_effect=fake_execute):
                result = agent.diagnose("service swallows errors")

        saved = memory.get(result.case_id)
        assert saved is not None
        assert saved.status == BugStatus.UNRESOLVED
        assert saved.patch_attempts[0]["diff"] == failed_diff
        assert saved.patch_attempts[0]["test_output"] == failed_output
