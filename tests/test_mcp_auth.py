"""Tests for MCP authentication and audit logging — P2-5."""

from __future__ import annotations

import json
from datetime import datetime, timezone


def _reload_mcp(monkeypatch, tmp_path):
    """Reload MCP server module with fresh env and memory dir."""
    monkeypatch.setenv("DEBUG_MIND_MEMORY_DIR", str(tmp_path / "mem"))
    import importlib
    import debug_mind.tools.mcp_server as mod

    importlib.reload(mod)
    # Reset global memory so it uses the right dir
    mod._memory = None
    return mod


class TestMCPAuth:
    """Test MCP token-based authentication."""

    def test_write_rejected_without_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEBUG_MIND_MCP_TOKEN", "secret123")
        mod = _reload_mcp(monkeypatch, tmp_path)

        result = mod.save_bug_case(
            title="test",
            symptoms="s",
            root_cause="rc",
            fix_suggestion="fix",
            auth_token="",
        )
        data = json.loads(result)
        assert data.get("error") == "auth_required"

    def test_write_accepted_with_correct_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEBUG_MIND_MCP_TOKEN", "secret123")
        mod = _reload_mcp(monkeypatch, tmp_path)

        result = mod.save_bug_case(
            title="test",
            symptoms="s",
            root_cause="rc",
            fix_suggestion="fix",
            auth_token="secret123",
        )
        data = json.loads(result)
        assert data.get("saved") is True

    def test_no_token_allows_writes(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEBUG_MIND_MCP_TOKEN", raising=False)
        mod = _reload_mcp(monkeypatch, tmp_path)

        result = mod.save_bug_case(
            title="test",
            symptoms="s",
            root_cause="rc",
            fix_suggestion="fix",
        )
        data = json.loads(result)
        assert data.get("saved") is True

    def test_read_tools_always_work(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEBUG_MIND_MCP_TOKEN", "secret123")
        mod = _reload_mcp(monkeypatch, tmp_path)

        result = mod.search_similar_bugs("test query")
        data = json.loads(result)
        assert "found" in data

    def test_delete_requires_auth(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEBUG_MIND_MCP_TOKEN", "secret123")
        mod = _reload_mcp(monkeypatch, tmp_path)

        # Save with auth first
        save_result = mod.save_bug_case(
            title="del-me",
            symptoms="s",
            root_cause="rc",
            fix_suggestion="fix",
            auth_token="secret123",
        )
        case_id = json.loads(save_result)["case_id"]

        # Delete without auth
        result = mod.delete_bug_case(case_id=case_id, auth_token="")
        data = json.loads(result)
        assert data.get("error") == "auth_required"


class TestAuditLog:
    """Test audit log entries."""

    def _setup_mod(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEBUG_MIND_MCP_TOKEN", raising=False)
        return _reload_mcp(monkeypatch, tmp_path)

    def test_save_creates_audit_entry(self, tmp_path, monkeypatch):
        mod = self._setup_mod(tmp_path, monkeypatch)
        result = mod.save_bug_case(
            title="audit-test",
            symptoms="s",
            root_cause="rc",
            fix_suggestion="fix",
        )
        data = json.loads(result)
        case_id = data["case_id"]

        # Find audit.jsonl from the memory store's actual directory
        mem = mod._get_memory()
        audit_path = mem.memory_dir / "audit.jsonl"
        assert audit_path.exists(), f"audit.jsonl not found at {audit_path}"
        with open(audit_path) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        assert any(e["op"] == "save" and e["case_id"] == case_id for e in entries)

    def test_verify_creates_audit_entry(self, tmp_path, monkeypatch):
        mod = self._setup_mod(tmp_path, monkeypatch)
        result = mod.save_bug_case(
            title="verify-audit",
            symptoms="s",
            root_cause="rc",
            fix_suggestion="fix",
        )
        case_id = json.loads(result)["case_id"]
        mod.verify_bug_case(case_id=case_id, correct=True, notes="looks good")

        mem = mod._get_memory()
        audit_path = mem.memory_dir / "audit.jsonl"
        with open(audit_path) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        ops = [e["op"] for e in entries]
        assert "save" in ops
        assert "verify" in ops

    def test_delete_creates_audit_entry(self, tmp_path, monkeypatch):
        mod = self._setup_mod(tmp_path, monkeypatch)
        result = mod.save_bug_case(
            title="del-audit",
            symptoms="s",
            root_cause="rc",
            fix_suggestion="fix",
        )
        case_id = json.loads(result)["case_id"]
        mod.delete_bug_case(case_id=case_id)

        mem = mod._get_memory()
        audit_path = mem.memory_dir / "audit.jsonl"
        with open(audit_path) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        assert any(e["op"] == "delete" and e["case_id"] == case_id for e in entries)


class TestAuditCLI:
    """Test the debug-mind audit CLI command."""

    def _write_audit(self, audit_path, op, case_id="test123"):
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": "cli",
            "op": op,
            "case_id": case_id,
            "details": {},
        }
        with open(audit_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def test_audit_command_shows_entries(self, tmp_path, monkeypatch):
        # P6-2: audit.jsonl now lives under <memory>/<namespace>/audit.jsonl
        monkeypatch.setenv("DEBUG_MIND_MEMORY_DIR", str(tmp_path / "mem"))

        from click.testing import CliRunner
        from debug_mind.cli import main

        self._write_audit(tmp_path / "mem" / "default" / "audit.jsonl", "delete", "test123")

        runner = CliRunner()
        result = runner.invoke(main, ["audit"])
        assert result.exit_code == 0
        assert "delete" in result.output or "test123" in result.output

    def test_audit_filter_by_op(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEBUG_MIND_MEMORY_DIR", str(tmp_path / "mem"))

        from click.testing import CliRunner
        from debug_mind.cli import main

        audit_path = tmp_path / "mem" / "default" / "audit.jsonl"
        for op in ["save", "delete", "verify"]:
            self._write_audit(audit_path, op, f"case-{op}")

        runner = CliRunner()
        result = runner.invoke(main, ["audit", "--op=delete"])
        assert result.exit_code == 0
        assert "delete" in result.output
