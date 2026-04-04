# tests/test_audit_log.py
"""Tests for tools.audit_log -- CroweLM audit logging."""

import json
import pytest
import tools.audit_log as audit_mod


@pytest.fixture
def logs_dir(tmp_path):
    """Redirect audit logs to a temp directory."""
    old_dir = audit_mod.LOGS_DIR
    audit_mod.LOGS_DIR = str(tmp_path)
    yield tmp_path
    audit_mod.LOGS_DIR = old_dir


class TestLogAction:
    def test_creates_log_file(self, logs_dir):
        audit_mod.log_action("test-agent", "test_action")
        assert (logs_dir / "test-agent.jsonl").exists()

    def test_returns_entry_with_required_fields(self, logs_dir):
        entry = audit_mod.log_action("test-agent", "do_thing", {"key": "val"}, "run-123")
        assert "id" in entry
        assert isinstance(entry["timestamp"], float)
        assert entry["agent_id"] == "test-agent"
        assert entry["action"] == "do_thing"
        assert entry["run_id"] == "run-123"
        assert entry["details"] == {"key": "val"}

    def test_appends_multiple_entries(self, logs_dir):
        audit_mod.log_action("test-agent", "action_1")
        audit_mod.log_action("test-agent", "action_2")
        lines = [l for l in (logs_dir / "test-agent.jsonl").read_text().strip().split("\n") if l]
        assert len(lines) == 2

    def test_default_run_id_is_unknown(self, logs_dir):
        entry = audit_mod.log_action("test-agent", "action")
        assert entry["run_id"] == "unknown"


class TestGetRunLog:
    def test_filters_by_run_id(self, logs_dir):
        audit_mod.log_action("agent-a", "start", run_id="run-1")
        audit_mod.log_action("agent-a", "middle", run_id="run-2")
        audit_mod.log_action("agent-a", "end", run_id="run-1")
        entries = audit_mod.get_run_log("agent-a", "run-1")
        assert len(entries) == 2
        assert all(e["run_id"] == "run-1" for e in entries)

    def test_returns_empty_for_missing_agent(self, logs_dir):
        assert audit_mod.get_run_log("nonexistent", "run-1") == []


class TestGetAgentLog:
    def test_returns_all_entries(self, logs_dir):
        for i in range(5):
            audit_mod.log_action("agent-b", f"action_{i}")
        assert len(audit_mod.get_agent_log("agent-b")) == 5

    def test_respects_limit(self, logs_dir):
        for i in range(10):
            audit_mod.log_action("agent-c", f"action_{i}")
        entries = audit_mod.get_agent_log("agent-c", limit=3)
        assert len(entries) == 3
        assert entries[0]["action"] == "action_7"

    def test_returns_empty_for_missing_agent(self, logs_dir):
        assert audit_mod.get_agent_log("nonexistent") == []


class TestCrowelmAuditLog:
    def test_returns_json_string(self, logs_dir):
        audit_mod.log_action("agent-d", "test")
        result = json.loads(audit_mod.crowelm_audit_log("agent-d"))
        assert result["agent_id"] == "agent-d"
        assert result["count"] == 1
        assert len(result["entries"]) == 1
