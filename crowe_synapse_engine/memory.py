"""
Crowe-Synapse Memory Store — SQLite-backed persistent memory.

Stores session history, tool execution logs, pipeline checkpoints,
and project knowledge. Portable across machines via ~/.crowe-logic/memory.db.
"""

import os
import sqlite3
import uuid
from datetime import datetime, timezone


_MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")


class MemoryStore:
    def __init__(self, db_path: str = "~/.crowe-logic/memory.db"):
        self.db_path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._run_migrations()

    def _run_migrations(self):
        self.conn.execute("CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMP)")
        applied = {row[0] for row in self.conn.execute("SELECT name FROM _migrations").fetchall()}
        if not os.path.isdir(_MIGRATIONS_DIR):
            return
        for filename in sorted(os.listdir(_MIGRATIONS_DIR)):
            if filename.endswith(".sql") and filename not in applied:
                path = os.path.join(_MIGRATIONS_DIR, filename)
                with open(path) as f:
                    self.conn.executescript(f.read())
                self.conn.execute("INSERT INTO _migrations (name, applied_at) VALUES (?, ?)", (filename, _now()))
                self.conn.commit()

    def _get_tables(self) -> list[str]:
        rows = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return [r[0] for r in rows]

    # -- Sessions --

    def start_session(self, thread_id: str, project_context: str = "") -> str:
        sid = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO sessions (id, thread_id, project_context, started_at) VALUES (?, ?, ?, ?)",
            (sid, thread_id, project_context, _now()),
        )
        self.conn.commit()
        return sid

    def end_session(self, session_id: str, summary: str = ""):
        self.conn.execute(
            "UPDATE sessions SET ended_at = ?, summary = ? WHERE id = ?",
            (_now(), summary, session_id),
        )
        self.conn.commit()

    def get_session(self, session_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def get_recent_sessions(self, limit: int = 10) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Tool Executions --

    def record_tool_execution(self, session_id: str | None, tool_name: str,
                              arguments: str = "", output: str = "",
                              duration_ms: int = 0, status: str = "success",
                              pipeline_run_id: str | None = None):
        self.conn.execute(
            "INSERT INTO tool_executions (session_id, pipeline_run_id, tool_name, arguments, output, duration_ms, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, pipeline_run_id, tool_name, arguments, output, duration_ms, status),
        )
        self.conn.commit()

    def get_tool_executions(self, session_id: str | None = None, limit: int = 100) -> list[dict]:
        if session_id:
            rows = self.conn.execute(
                "SELECT * FROM tool_executions WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM tool_executions WHERE session_id IS NULL ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- Pipeline Runs --

    def start_pipeline_run(self, session_id: str, pipeline_name: str, steps: str) -> str:
        run_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO pipeline_runs (id, session_id, pipeline_name, steps) VALUES (?, ?, ?, ?)",
            (run_id, session_id, pipeline_name, steps),
        )
        self.conn.commit()
        return run_id

    def complete_pipeline_run(self, run_id: str, status: str = "completed", result: str = ""):
        self.conn.execute(
            "UPDATE pipeline_runs SET status = ?, result = ?, duration_ms = 0 WHERE id = ?",
            (status, result, run_id),
        )
        self.conn.commit()

    def get_pipeline_run(self, run_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    # -- Checkpoints --

    def save_checkpoint(self, pipeline_run_id: str, step_index: int, state_snapshot: str):
        self.conn.execute(
            "INSERT INTO checkpoints (pipeline_run_id, step_index, state_snapshot) VALUES (?, ?, ?)",
            (pipeline_run_id, step_index, state_snapshot),
        )
        self.conn.commit()

    def get_latest_checkpoint(self, pipeline_run_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM checkpoints WHERE pipeline_run_id = ? ORDER BY step_index DESC LIMIT 1",
            (pipeline_run_id,),
        ).fetchone()
        return dict(row) if row else None

    # -- Project Knowledge --

    def set_knowledge(self, key: str, value: str, source: str = "agent"):
        self.conn.execute(
            "INSERT OR REPLACE INTO project_knowledge (key, value, source, updated_at) VALUES (?, ?, ?, ?)",
            (key, value, source, _now()),
        )
        self.conn.commit()

    def get_knowledge(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM project_knowledge WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def get_all_knowledge(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM project_knowledge ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]

    # -- Agent Delegations --

    def record_delegation(self, session_id: str, agent_name: str, task: str,
                          result: str = "", duration_ms: int = 0):
        self.conn.execute(
            "INSERT INTO agent_delegations (session_id, agent_name, task, result, duration_ms) VALUES (?, ?, ?, ?, ?)",
            (session_id, agent_name, task, result, duration_ms),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
