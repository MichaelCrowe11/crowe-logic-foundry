"""
Crowe-Synapse Memory Store — SQLite-backed persistent memory.

Stores session history, tool execution logs, pipeline checkpoints,
and project knowledge. Portable across machines via ~/.crowe-logic/memory.db.
"""

import os
import sqlite3
import re
import threading
import uuid
from datetime import datetime, timezone


_MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")
_SQLITE_INCOMPATIBLE_PATTERNS = (
    re.compile(r"\bTIMESTAMPTZ\b", re.IGNORECASE),
    re.compile(r"\bJSONB\b", re.IGNORECASE),
    re.compile(r"\bBIGSERIAL\b", re.IGNORECASE),
    re.compile(r"\bCREATE\s+EXTENSION\b", re.IGNORECASE),
    re.compile(r"\bCREATE\s+OR\s+REPLACE\s+VIEW\b", re.IGNORECASE),
    re.compile(r"\bgen_random_uuid\s*\(", re.IGNORECASE),
    re.compile(r"\bdate_trunc\s*\(", re.IGNORECASE),
    re.compile(r"\bUSING\s+hnsw\b", re.IGNORECASE),
    re.compile(r"\bTEXT\[\]", re.IGNORECASE),
    re.compile(r"::[A-Za-z_][A-Za-z0-9_]*"),
    re.compile(r"\bvector\s*\(", re.IGNORECASE),
)
_CORE_TABLES = {
    "sessions",
    "pipeline_runs",
    "tool_executions",
    "agent_delegations",
    "project_knowledge",
    "checkpoints",
}
_CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    summary TEXT,
    project_context TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    pipeline_name TEXT NOT NULL,
    steps TEXT NOT NULL,
    status TEXT DEFAULT 'running',
    duration_ms INTEGER,
    result TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tool_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(id),
    pipeline_run_id TEXT REFERENCES pipeline_runs(id),
    tool_name TEXT NOT NULL,
    arguments TEXT,
    output TEXT,
    duration_ms INTEGER,
    status TEXT DEFAULT 'success',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_delegations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(id),
    agent_name TEXT NOT NULL,
    task TEXT NOT NULL,
    result TEXT,
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project_knowledge (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    source TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_run_id TEXT REFERENCES pipeline_runs(id),
    step_index INTEGER NOT NULL,
    state_snapshot TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class _LockedCursor:
    """Cursor proxy that acquires the connection's lock for every fetch.

    Returned by ``_LockedConnection.execute`` family members so that code
    like ``store.conn.execute(q).fetchall()`` remains atomic across the
    execute and fetch pair.
    """

    __slots__ = ("_cursor", "_lock")

    def __init__(self, cursor: sqlite3.Cursor, lock: threading.RLock):
        self._cursor = cursor
        self._lock = lock

    def fetchone(self):
        with self._lock:
            return self._cursor.fetchone()

    def fetchall(self):
        with self._lock:
            return self._cursor.fetchall()

    def fetchmany(self, size: int = -1):
        with self._lock:
            if size < 0:
                return self._cursor.fetchmany()
            return self._cursor.fetchmany(size)

    def __iter__(self):
        with self._lock:
            rows = list(self._cursor)
        return iter(rows)

    def close(self):
        with self._lock:
            self._cursor.close()

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount


class _LockedConnection:
    """Thread-safe wrapper around ``sqlite3.Connection``.

    Serializes every call through a single :class:`threading.RLock` so the
    ``MemoryStore`` can be reused across dual-mode worker threads without
    tripping SQLite's ``check_same_thread`` guard or interleaving cursor
    fetches. The wrapper exposes the subset of the Connection API that
    ``MemoryStore`` actually uses.
    """

    __slots__ = ("_conn", "_lock")

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        self._conn = conn
        self._lock = lock

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._conn.row_factory = value

    def execute(self, *args, **kwargs) -> _LockedCursor:
        with self._lock:
            return _LockedCursor(self._conn.execute(*args, **kwargs), self._lock)

    def executemany(self, *args, **kwargs) -> _LockedCursor:
        with self._lock:
            return _LockedCursor(self._conn.executemany(*args, **kwargs), self._lock)

    def executescript(self, *args, **kwargs) -> _LockedCursor:
        with self._lock:
            return _LockedCursor(self._conn.executescript(*args, **kwargs), self._lock)

    def commit(self):
        with self._lock:
            self._conn.commit()

    def rollback(self):
        with self._lock:
            self._conn.rollback()

    def close(self):
        with self._lock:
            self._conn.close()


class MemoryStore:
    def __init__(self, db_path: str = "~/.crowe-logic/memory.db"):
        self.db_path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        # check_same_thread=False lets dual-mode worker threads reuse the
        # shared connection. Correctness is enforced by routing every
        # call through a _LockedConnection proxy that serializes execute
        # and cursor-fetch pairs under one RLock. WAL mode (set below)
        # keeps concurrent readers from blocking each other on the SQLite
        # side.
        raw_conn = sqlite3.connect(self.db_path, check_same_thread=False)
        raw_conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.conn = _LockedConnection(raw_conn, self._lock)
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self._run_migrations()

    def _run_migrations(self):
        self.conn.execute("CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMP)")
        applied = {row[0] for row in self.conn.execute("SELECT name FROM _migrations").fetchall()}
        if os.path.isdir(_MIGRATIONS_DIR):
            for filename in sorted(os.listdir(_MIGRATIONS_DIR)):
                if filename.endswith(".down.sql"):
                    continue
                if filename.endswith(".sql") and filename not in applied:
                    path = os.path.join(_MIGRATIONS_DIR, filename)
                    with open(path) as f:
                        sql = f.read()
                    if not _is_sqlite_compatible(sql):
                        continue
                    self.conn.executescript(sql)
                    self.conn.execute("INSERT INTO _migrations (name, applied_at) VALUES (?, ?)", (filename, _now()))
                    self.conn.commit()
        self._ensure_core_schema()

    def _ensure_core_schema(self):
        """Guarantee the portable Synapse schema even in slim packages."""
        if _CORE_TABLES.issubset(set(self._get_tables())):
            return
        self.conn.executescript(_CORE_SCHEMA)
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


def _is_sqlite_compatible(sql: str) -> bool:
    sql_without_line_comments = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
    return not any(pattern.search(sql_without_line_comments) for pattern in _SQLITE_INCOMPATIBLE_PATTERNS)
