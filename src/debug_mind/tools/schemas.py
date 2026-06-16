"""Tool schema definitions — single source of truth for agent and MCP.

Both agent.py (Anthropic tool use) and mcp_server.py (FastMCP) import
their tool definitions from here to prevent schema drift.
"""

from __future__ import annotations

MEMORY_TOOLS = [
    {
        "name": "search_memory",
        "description": "Search past bug cases in the experiential memory. ALWAYS call this first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Bug symptoms, error, or keywords",
                    "minLength": 1,
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max results (default 5)",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "save_to_memory",
        "description": "Save a diagnosed bug case to memory. Call AFTER completing diagnosis.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short searchable title",
                    "minLength": 1,
                },
                "symptoms": {
                    "type": "string",
                    "description": "What was observed",
                    "minLength": 1,
                },
                "error_log": {"type": "string", "description": "Raw error log or stack trace"},
                "root_cause": {
                    "type": "string",
                    "description": "The identified root cause",
                    "minLength": 1,
                },
                "fix_suggestion": {
                    "type": "string",
                    "description": "How to fix it",
                    "minLength": 1,
                },
                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Searchable tags",
                },
                "environment": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Environment context",
                },
                "diagnosis_steps": {"type": "array", "items": {"type": "string"}},
                "similar_case_ids": {"type": "array", "items": {"type": "string"}},
                "patch_attempts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                    "description": (
                        "Failed patch attempts to remember as dead ends. "
                        "Each item should include diff, test_output, and reason."
                    ),
                },
            },
            "required": ["title", "symptoms", "root_cause", "fix_suggestion"],
        },
    },
]

CODEBASE_TOOLS = [
    {
        "name": "search_code",
        "description": "Search the project source code for a pattern. Returns file:line:content matches.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Search pattern (supports regex)",
                    "minLength": 1,
                },
                "file_type": {
                    "type": "string",
                    "description": "File extension filter (e.g. 'java', 'py')",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a source file from the project. Use after search_code to inspect specific code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path from project root",
                    "minLength": 1,
                },
                "start_line": {
                    "type": "integer",
                    "description": "Start line (0-based, default 0)",
                    "default": 0,
                    "minimum": 0,
                },
                "end_line": {
                    "type": "integer",
                    "description": "End line (default 100)",
                    "default": 100,
                    "minimum": 1,
                    "maximum": 2000,
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "list_project_structure",
        "description": "Get the directory tree of the project. Use to understand project layout.",
        "input_schema": {
            "type": "object",
            "properties": {
                "depth": {
                    "type": "integer",
                    "description": "Directory depth (default 3)",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 8,
                },
            },
        },
    },
    {
        "name": "parse_symbols",
        "description": "Parse a source file and list its top-level symbols (functions, classes). Requires tree-sitter.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to the source file from project root",
                    "minLength": 1,
                },
            },
            "required": ["file_path"],
        },
    },
]


def get_tool_names() -> set[str]:
    """Return the set of all tool names across both categories."""
    return {t["name"] for t in MEMORY_TOOLS + CODEBASE_TOOLS}
