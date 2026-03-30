"""Tests for crowe_synapse.memory — SQLite memory store."""

import os
import tempfile
import pytest
from crowe_synapse.memory import MemoryStore


@pytest.fixture
def store():
    """Create a temporary in-memory store for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        s = MemoryStore(db_path=db_path)
        yield s
        s.close()


class TestSessionLifecycle:
    def test_start_session_creates_record(self, store):
        sid = store.start_session(thread_id="thread_abc")
        assert sid is not None
        session = store.get_session(sid)
        assert session["thread_id"] == "thread_abc"
        assert session["ended_at"] is None

    def test_end_session_sets_summary(self, store):
        sid = store.start_session(thread_id="thread_abc")
        store.end_session(sid, summary="Worked on music composition")
        session = store.get_session(sid)
        assert session["summary"] == "Worked on music composition"
        assert session["ended_at"] is not None

    def test_get_recent_sessions(self, store):
        store.start_session(thread_id="t1")
        store.start_session(thread_id="t2")
        store.start_session(thread_id="t3")
        recent = store.get_recent_sessions(limit=2)
        assert len(recent) == 2
        assert recent[0]["thread_id"] == "t3"


class TestToolExecutionLog:
    def test_record_tool_execution(self, store):
        sid = store.start_session(thread_id="thread_abc")
        store.record_tool_execution(
            session_id=sid,
            tool_name="grep_search",
            arguments='{"pattern": "def main"}',
            output='{"matches": []}',
            duration_ms=45,
            status="success",
        )
        log = store.get_tool_executions(session_id=sid)
        assert len(log) == 1
        assert log[0]["tool_name"] == "grep_search"
        assert log[0]["duration_ms"] == 45

    def test_record_tool_execution_without_session(self, store):
        store.record_tool_execution(
            session_id=None,
            tool_name="read_file",
            arguments='{"path": "/tmp/x"}',
            output="contents",
            duration_ms=10,
        )
        log = store.get_tool_executions(session_id=None)
        assert len(log) == 1


class TestProjectKnowledge:
    def test_set_and_get_knowledge(self, store):
        store.set_knowledge("last_project", "/Users/crowelogic/Projects/talon", source="user")
        val = store.get_knowledge("last_project")
        assert val == "/Users/crowelogic/Projects/talon"

    def test_update_knowledge_overwrites(self, store):
        store.set_knowledge("mood", "focused", source="agent")
        store.set_knowledge("mood", "creative", source="agent")
        assert store.get_knowledge("mood") == "creative"

    def test_get_all_knowledge(self, store):
        store.set_knowledge("k1", "v1")
        store.set_knowledge("k2", "v2")
        all_k = store.get_all_knowledge()
        assert len(all_k) == 2


class TestPipelineCheckpoints:
    def test_save_and_load_checkpoint(self, store):
        sid = store.start_session(thread_id="t1")
        run_id = store.start_pipeline_run(session_id=sid, pipeline_name="refactor", steps='["grep","read","edit"]')
        store.save_checkpoint(pipeline_run_id=run_id, step_index=1, state_snapshot='{"found": "main.py"}')
        cp = store.get_latest_checkpoint(pipeline_run_id=run_id)
        assert cp["step_index"] == 1
        assert '"found"' in cp["state_snapshot"]

    def test_complete_pipeline_run(self, store):
        sid = store.start_session(thread_id="t1")
        run_id = store.start_pipeline_run(session_id=sid, pipeline_name="research", steps='["search","browse"]')
        store.complete_pipeline_run(run_id, status="completed", result="Found 5 articles")
        run = store.get_pipeline_run(run_id)
        assert run["status"] == "completed"
        assert run["result"] == "Found 5 articles"


class TestMigration:
    def test_fresh_db_has_all_tables(self, store):
        tables = store._get_tables()
        expected = {"sessions", "pipeline_runs", "tool_executions", "agent_delegations", "project_knowledge", "checkpoints"}
        assert expected.issubset(set(tables))
