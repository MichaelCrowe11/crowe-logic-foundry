"""Tests for crowe_synapse_engine.orchestrator — the central coordinator."""

import json
import os
import tempfile
import pytest
from crowe_synapse_engine.orchestrator import Orchestrator


@pytest.fixture
def orch(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    agents_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "agents")
    templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "crowe_synapse_engine", "templates")
    return Orchestrator(db_path=db_path, agents_dir=agents_dir, templates_dir=templates_dir)


class TestSessionManagement:
    def test_start_session(self, orch):
        sid = orch.start_session(thread_id="thread_123")
        assert sid is not None

    def test_end_session(self, orch):
        sid = orch.start_session(thread_id="thread_123")
        orch.end_session(summary="test session")
        session = orch.memory.get_session(sid)
        assert session["summary"] == "test session"

    def test_get_history(self, orch):
        orch.start_session(thread_id="t1")
        orch.end_session(summary="s1")
        orch.start_session(thread_id="t2")
        orch.end_session(summary="s2")
        history = orch.get_history(limit=5)
        assert len(history) == 2


class TestPrepareContext:
    def test_prepare_returns_context(self, orch):
        orch.start_session(thread_id="t1")
        ctx = orch.prepare("read the config file", thread_id="t1")
        assert ctx is not None
        assert "mode" in ctx
        assert ctx["mode"] in ("direct", "pipeline", "delegated")

    def test_prepare_matches_pipeline(self, orch):
        orch.start_session(thread_id="t1")
        ctx = orch.prepare("refactor the main function", thread_id="t1")
        assert ctx["mode"] == "pipeline"
        assert ctx["pipeline_name"] == "refactor"

    def test_prepare_no_pipeline_match(self, orch):
        orch.start_session(thread_id="t1")
        ctx = orch.prepare("what is 2 + 2", thread_id="t1")
        assert ctx["mode"] == "direct"
        assert ctx["pipeline_name"] is None


class TestToolRecording:
    def test_record_execution(self, orch):
        sid = orch.start_session(thread_id="t1")
        orch.record_execution(
            tool_name="read_file",
            arguments='{"path": "/tmp/test"}',
            output="file contents",
            duration_ms=12,
        )
        log = orch.memory.get_tool_executions(session_id=sid)
        assert len(log) == 1
        assert log[0]["tool_name"] == "read_file"


class TestAgentAccess:
    def test_list_agents(self, orch):
        agents = orch.list_agents()
        names = [a.name for a in agents]
        assert "code" in names
        assert "music" in names

    def test_list_pipelines(self, orch):
        pipelines = orch.list_pipelines()
        names = [p.name for p in pipelines]
        assert "refactor" in names


class TestContextInjection:
    def test_build_context_includes_knowledge(self, orch):
        orch.start_session(thread_id="t1")
        orch.memory.set_knowledge("project", "talon", source="user")
        ctx = orch.prepare("help me with the project", thread_id="t1")
        assert "injection" in ctx
        assert "talon" in ctx["injection"]

    def test_build_context_includes_recent_session(self, orch):
        sid1 = orch.start_session(thread_id="t1")
        orch.end_session(summary="Worked on Talon jazz composition")
        orch.start_session(thread_id="t2")
        ctx = orch.prepare("continue where we left off", thread_id="t2")
        assert "injection" in ctx
        assert "jazz" in ctx["injection"].lower() or "talon" in ctx["injection"].lower()
