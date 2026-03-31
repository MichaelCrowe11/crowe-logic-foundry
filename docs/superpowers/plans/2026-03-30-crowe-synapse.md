# Crowe-Synapse Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the crowe-synapse orchestration framework — pipeline engine, multi-agent coordination, SQLite memory, and pluggable quantum decision layer — as an additive layer on top of the existing Crowe Logic CLI.

**Architecture:** A new `crowe_synapse/` Python package sits between the CLI and Azure AI Foundry. The orchestrator manages session memory (SQLite), routes tasks to sub-agents (YAML configs), executes pipelines (step chaining with state), and offers pluggable quantum decision points (Synapse-Lang). The CLI gets minimal changes — lazy-loaded orchestrator with pre/post hooks.

**Tech Stack:** Python 3.12, SQLite (stdlib), PyYAML, Azure AI Agents SDK, Synapse-Lang (optional), Qubit-Flow (optional)

**Spec:** `docs/superpowers/specs/2026-03-30-crowe-synapse-design.md`

---

## File Map

### New Files

| File | Responsibility |
|------|---------------|
| `crowe_synapse/__init__.py` | Public API — exports Orchestrator, Pipeline, AgentRegistry, MemoryStore |
| `crowe_synapse/memory.py` | SQLite memory store — sessions, tool logs, project knowledge, checkpoints |
| `crowe_synapse/pipeline.py` | Pipeline engine — step execution, state passing, retries, template loading |
| `crowe_synapse/agent_registry.py` | Load YAML agent definitions, resolve tool subsets, manage sub-agent context |
| `crowe_synapse/orchestrator.py` | Router + dispatcher + context manager — ties all components together |
| `crowe_synapse/quantum_bridge.py` | DecisionPoint interface, Synapse-Lang/Qubit-Flow evaluation, graceful degradation |
| `crowe_synapse/templates/refactor.yaml` | Pipeline template: search > read > edit > diff > commit |
| `crowe_synapse/templates/research.yaml` | Pipeline template: search > browse > summarize |
| `crowe_synapse/templates/compose.yaml` | Pipeline template: chords > melody > drums > bass |
| `agents/code.yaml` | Sub-agent: code editing specialist |
| `agents/research.yaml` | Sub-agent: web research specialist |
| `agents/music.yaml` | Sub-agent: Talon composition specialist |
| `agents/quantum.yaml` | Sub-agent: quantum circuit specialist |
| `agents/cultivation.yaml` | Sub-agent: mycology knowledge specialist |
| `migrations/001_initial.sql` | SQLite schema — all 6 tables |
| `tests/__init__.py` | Test package init |
| `tests/test_memory.py` | Memory store tests |
| `tests/test_pipeline.py` | Pipeline engine tests |
| `tests/test_agent_registry.py` | Agent registry tests |
| `tests/test_orchestrator.py` | Orchestrator integration tests |
| `tests/test_quantum_bridge.py` | Quantum bridge tests |

### Modified Files

| File | Change |
|------|--------|
| `pyproject.toml` | Add pyyaml dependency, add crowe_synapse to packages, add agents/ and migrations/ to package-data |
| `cli/crowe_logic.py` | Add orchestrator hooks (lazy load, pre/post message, session lifecycle), 4 new CLI commands |

---

### Task 1: Project Setup — Dependencies and Migration Schema

**Files:**
- Create: `migrations/001_initial.sql`
- Create: `tests/__init__.py`
- Create: `crowe_synapse/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Install PyYAML and create test infrastructure**

Run:
```bash
cd /Users/crowelogic/Projects/crowe-logic-foundry
.venv/bin/pip install pyyaml pytest
mkdir -p tests crowe_synapse migrations agents crowe_synapse/templates
touch tests/__init__.py
```

- [ ] **Step 2: Add pyyaml to pyproject.toml and update package includes**

In `pyproject.toml`, add `"pyyaml>=6.0"` to the dependencies list. Update the setuptools packages.find include to add `"crowe_synapse*"`. Add package-data entries for agents and migrations:

```toml
[tool.setuptools.packages.find]
include = ["cli*", "tools*", "config*", "scripts*", "crowe_synapse*"]

[tool.setuptools.package-data]
cli = ["icon.png", "icons/*.icns"]
crowe_synapse = ["templates/*.yaml"]
```

- [ ] **Step 3: Write the SQLite migration**

Create `migrations/001_initial.sql`:

```sql
-- Crowe-Synapse Memory Store — Initial Schema

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
```

- [ ] **Step 4: Create the package init stub**

Create `crowe_synapse/__init__.py`:

```python
"""
Crowe-Synapse — Orchestration Framework for Crowe Logic

Pipeline engine, multi-agent coordination, persistent memory,
and pluggable quantum decision-making.
"""

__version__ = "0.1.0"
```

(Exports will be added as each module is built.)

- [ ] **Step 5: Commit**

```bash
git add migrations/ tests/__init__.py crowe_synapse/__init__.py pyproject.toml
git commit -m "feat: scaffold crowe-synapse package, migration schema, test infra"
```

---

### Task 2: Memory Store

**Files:**
- Create: `crowe_synapse/memory.py`
- Create: `tests/test_memory.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_memory.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_memory.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crowe_synapse.memory'`

- [ ] **Step 3: Implement the memory store**

Create `crowe_synapse/memory.py`:

```python
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

    # ── Sessions ─────────────────────────────────────────────

    def start_session(self, thread_id: str, project_context: str = "") -> str:
        sid = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO sessions (id, thread_id, project_context) VALUES (?, ?, ?)",
            (sid, thread_id, project_context),
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

    # ── Tool Executions ──────────────────────────────────────

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

    # ── Pipeline Runs ────────────────────────────────────────

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

    # ── Checkpoints ──────────────────────────────────────────

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

    # ── Project Knowledge ────────────────────────────────────

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

    # ── Agent Delegations ────────────────────────────────────

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_memory.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add crowe_synapse/memory.py tests/test_memory.py
git commit -m "feat: implement SQLite memory store with full test coverage"
```

---

### Task 3: Pipeline Engine

**Files:**
- Create: `crowe_synapse/pipeline.py`
- Create: `crowe_synapse/templates/refactor.yaml`
- Create: `crowe_synapse/templates/research.yaml`
- Create: `crowe_synapse/templates/compose.yaml`
- Create: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pipeline.py`:

```python
"""Tests for crowe_synapse.pipeline — step execution engine."""

import json
import os
import tempfile
import pytest
from crowe_synapse.pipeline import PipelineEngine, PipelineStep, PipelineRun, PipelineTemplate


@pytest.fixture
def engine(tmp_path):
    templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "crowe_synapse", "templates")
    return PipelineEngine(templates_dir=templates_dir)


def echo_tool(text: str = "") -> str:
    return json.dumps({"echoed": text})


def fail_tool() -> str:
    raise RuntimeError("tool failed")


def greet_tool(name: str = "world") -> str:
    return json.dumps({"greeting": f"hello {name}"})


class TestPipelineStep:
    def test_step_executes_tool(self):
        step = PipelineStep(tool_name="echo_tool", input_args={"text": "ping"})
        tool_map = {"echo_tool": echo_tool}
        result = step.execute(tool_map, context={})
        assert result.status == "success"
        assert "ping" in result.output

    def test_step_captures_failure(self):
        step = PipelineStep(tool_name="fail_tool", input_args={})
        tool_map = {"fail_tool": fail_tool}
        result = step.execute(tool_map, context={})
        assert result.status == "failed"
        assert "tool failed" in result.output

    def test_step_retries_on_failure(self):
        call_count = 0
        def flaky_tool() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient")
            return json.dumps({"ok": True})

        step = PipelineStep(tool_name="flaky", input_args={}, max_retries=3)
        tool_map = {"flaky": flaky_tool}
        result = step.execute(tool_map, context={})
        assert result.status == "success"
        assert call_count == 3


class TestPipelineRun:
    def test_run_executes_steps_in_order(self):
        steps = [
            PipelineStep(tool_name="echo_tool", input_args={"text": "first"}),
            PipelineStep(tool_name="greet_tool", input_args={"name": "crowe"}),
        ]
        tool_map = {"echo_tool": echo_tool, "greet_tool": greet_tool}
        run = PipelineRun(name="test", steps=steps)
        run.execute(tool_map)
        assert run.status == "completed"
        assert len(run.results) == 2
        assert "first" in run.results[0].output
        assert "crowe" in run.results[1].output

    def test_run_stops_on_failure(self):
        steps = [
            PipelineStep(tool_name="echo_tool", input_args={"text": "ok"}),
            PipelineStep(tool_name="fail_tool", input_args={}),
            PipelineStep(tool_name="echo_tool", input_args={"text": "never"}),
        ]
        tool_map = {"echo_tool": echo_tool, "fail_tool": fail_tool}
        run = PipelineRun(name="test", steps=steps)
        run.execute(tool_map)
        assert run.status == "failed"
        assert len(run.results) == 2  # third step never ran

    def test_run_passes_state_between_steps(self):
        steps = [
            PipelineStep(tool_name="echo_tool", input_args={"text": "data"}),
            PipelineStep(tool_name="greet_tool", input_args={"name": "{previous.echoed}"}),
        ]
        tool_map = {"echo_tool": echo_tool, "greet_tool": greet_tool}
        run = PipelineRun(name="test", steps=steps)
        run.execute(tool_map)
        assert run.status == "completed"
        assert "data" in run.results[1].output


class TestPipelineTemplate:
    def test_load_template_from_yaml(self, engine):
        templates = engine.list_templates()
        names = [t.name for t in templates]
        assert "refactor" in names
        assert "research" in names
        assert "compose" in names

    def test_template_has_trigger(self, engine):
        t = engine.get_template("refactor")
        assert t is not None
        assert t.trigger is not None

    def test_match_template_by_input(self, engine):
        match = engine.match_template("refactor the main function")
        assert match is not None
        assert match.name == "refactor"

    def test_no_match_returns_none(self, engine):
        match = engine.match_template("what is the weather today")
        assert match is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crowe_synapse.pipeline'`

- [ ] **Step 3: Create pipeline templates**

Create `crowe_synapse/templates/refactor.yaml`:

```yaml
name: refactor
description: "Search, read, edit, and commit code changes"
trigger: "refactor|rename|extract method|clean up"
steps:
  - tool: grep_search
    input_from: task.target
  - tool: read_file
    input_from: previous.file
  - tool: edit_file
    input_from: task.changes
  - tool: git_diff
  - tool: git_commit
    input_from: task.message
```

Create `crowe_synapse/templates/research.yaml`:

```yaml
name: research
description: "Search the web and summarize findings"
trigger: "research|look up|find out|search for"
steps:
  - tool: web_search
    input_from: task.query
  - tool: browse_url
    input_from: previous.url
```

Create `crowe_synapse/templates/compose.yaml`:

```yaml
name: compose
description: "Generate a full multi-track composition"
trigger: "compose|write music|generate.*track|create.*song"
steps:
  - tool: talon_generate_chords
    input_from: task.params
  - tool: talon_generate_melody
    input_from: task.params
  - tool: talon_generate_drums
    input_from: task.params
```

- [ ] **Step 4: Implement the pipeline engine**

Create `crowe_synapse/pipeline.py`:

```python
"""
Crowe-Synapse Pipeline Engine — step execution with state passing.

Supports two modes:
- Agent-directed: model decides each step, engine tracks state and retries
- Framework-directed: registered templates run without model round-trips
"""

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field

import yaml


@dataclass
class StepResult:
    tool_name: str
    output: str
    status: str  # "success" or "failed"
    duration_ms: int = 0
    attempt: int = 1


@dataclass
class PipelineStep:
    tool_name: str
    input_args: dict = field(default_factory=dict)
    max_retries: int = 1
    validator: str | None = None

    def execute(self, tool_map: dict, context: dict) -> StepResult:
        resolved_args = _resolve_args(self.input_args, context)
        func = tool_map.get(self.tool_name)
        if not func:
            return StepResult(
                tool_name=self.tool_name,
                output=json.dumps({"error": f"Unknown tool: {self.tool_name}"}),
                status="failed",
            )

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            start = time.monotonic()
            try:
                result = func(**resolved_args)
                output = str(result) if result is not None else ""
                duration = int((time.monotonic() - start) * 1000)
                return StepResult(
                    tool_name=self.tool_name,
                    output=output,
                    status="success",
                    duration_ms=duration,
                    attempt=attempt,
                )
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    time.sleep(0.1 * attempt)

        duration = int((time.monotonic() - start) * 1000)
        return StepResult(
            tool_name=self.tool_name,
            output=json.dumps({"error": str(last_error)}),
            status="failed",
            duration_ms=duration,
            attempt=self.max_retries,
        )


@dataclass
class PipelineRun:
    name: str
    steps: list[PipelineStep]
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "pending"
    results: list[StepResult] = field(default_factory=list)

    def execute(self, tool_map: dict):
        self.status = "running"
        context = {}
        for i, step in enumerate(self.steps):
            result = step.execute(tool_map, context)
            self.results.append(result)

            if result.status == "success":
                try:
                    parsed = json.loads(result.output)
                    context["previous"] = parsed
                except (json.JSONDecodeError, TypeError):
                    context["previous"] = {"raw": result.output}
                context[f"step_{i}"] = context["previous"]
            else:
                self.status = "failed"
                return

        self.status = "completed"


@dataclass
class PipelineTemplate:
    name: str
    description: str
    trigger: str | None
    steps: list[dict]

    def matches(self, text: str) -> bool:
        if not self.trigger:
            return False
        return bool(re.search(self.trigger, text, re.IGNORECASE))

    def to_pipeline_run(self) -> PipelineRun:
        steps = []
        for s in self.steps:
            steps.append(PipelineStep(
                tool_name=s["tool"],
                input_args=s.get("input_args", {}),
                max_retries=s.get("max_retries", 1),
            ))
        return PipelineRun(name=self.name, steps=steps)


class PipelineEngine:
    def __init__(self, templates_dir: str = ""):
        self._templates: list[PipelineTemplate] = []
        if templates_dir and os.path.isdir(templates_dir):
            self._load_templates(templates_dir)

    def _load_templates(self, templates_dir: str):
        for filename in sorted(os.listdir(templates_dir)):
            if filename.endswith((".yaml", ".yml")):
                path = os.path.join(templates_dir, filename)
                with open(path) as f:
                    data = yaml.safe_load(f)
                if data:
                    self._templates.append(PipelineTemplate(
                        name=data.get("name", filename),
                        description=data.get("description", ""),
                        trigger=data.get("trigger"),
                        steps=data.get("steps", []),
                    ))

    def list_templates(self) -> list[PipelineTemplate]:
        return list(self._templates)

    def get_template(self, name: str) -> PipelineTemplate | None:
        for t in self._templates:
            if t.name == name:
                return t
        return None

    def match_template(self, text: str) -> PipelineTemplate | None:
        for t in self._templates:
            if t.matches(text):
                return t
        return None


def _resolve_args(args: dict, context: dict) -> dict:
    """Replace {previous.key} and {step_N.key} placeholders with context values."""
    resolved = {}
    for k, v in args.items():
        if isinstance(v, str) and "{" in v:
            for ctx_key, ctx_val in context.items():
                if isinstance(ctx_val, dict):
                    for inner_key, inner_val in ctx_val.items():
                        placeholder = f"{{{ctx_key}.{inner_key}}}"
                        if placeholder in v:
                            v = v.replace(placeholder, str(inner_val))
            resolved[k] = v
        else:
            resolved[k] = v
    return resolved
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: All 10 tests PASS

- [ ] **Step 6: Commit**

```bash
git add crowe_synapse/pipeline.py crowe_synapse/templates/ tests/test_pipeline.py
git commit -m "feat: implement pipeline engine with templates and state passing"
```

---

### Task 4: Agent Registry

**Files:**
- Create: `crowe_synapse/agent_registry.py`
- Create: `agents/code.yaml`
- Create: `agents/research.yaml`
- Create: `agents/music.yaml`
- Create: `agents/quantum.yaml`
- Create: `agents/cultivation.yaml`
- Create: `tests/test_agent_registry.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agent_registry.py`:

```python
"""Tests for crowe_synapse.agent_registry — YAML agent loading."""

import os
import pytest
from crowe_synapse.agent_registry import AgentRegistry, AgentConfig


@pytest.fixture
def registry():
    agents_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "agents")
    return AgentRegistry(agents_dir=agents_dir)


class TestAgentLoading:
    def test_loads_all_agents(self, registry):
        agents = registry.list_agents()
        names = [a.name for a in agents]
        assert "code" in names
        assert "music" in names
        assert "research" in names
        assert "quantum" in names
        assert "cultivation" in names

    def test_get_agent_by_name(self, registry):
        agent = registry.get_agent("music")
        assert agent is not None
        assert agent.name == "music"
        assert "talon_*" in agent.tools

    def test_get_unknown_agent_returns_none(self, registry):
        assert registry.get_agent("nonexistent") is None


class TestAgentConfig:
    def test_agent_has_prompt_override(self, registry):
        agent = registry.get_agent("music")
        assert agent.prompt_override is not None
        assert len(agent.prompt_override) > 10

    def test_agent_has_description(self, registry):
        agent = registry.get_agent("code")
        assert agent.description is not None

    def test_agent_tools_are_list(self, registry):
        agent = registry.get_agent("research")
        assert isinstance(agent.tools, list)
        assert len(agent.tools) > 0


class TestToolResolution:
    def test_resolve_glob_pattern(self, registry):
        available_tools = {"talon_generate_chords", "talon_generate_drums", "talon_generate_melody",
                           "talon_quantum_melody", "read_file", "write_file", "execute_shell"}
        agent = registry.get_agent("music")
        resolved = registry.resolve_tools(agent, available_tools)
        assert "talon_generate_chords" in resolved
        assert "talon_generate_drums" in resolved
        assert "read_file" in resolved
        assert "write_file" not in resolved  # not in music agent's tools list

    def test_resolve_exact_names(self, registry):
        available_tools = {"web_search", "browse_url", "grep_search", "read_file", "execute_shell"}
        agent = registry.get_agent("research")
        resolved = registry.resolve_tools(agent, available_tools)
        assert "web_search" in resolved
        assert "browse_url" in resolved
        assert "execute_shell" not in resolved
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_agent_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create agent YAML definitions**

Create `agents/code.yaml`:

```yaml
name: code
description: "Code editing, refactoring, and debugging specialist"
model: gpt-oss120-120b
tools:
  - read_file
  - write_file
  - edit_file
  - list_directory
  - execute_shell
  - grep_search
  - git_status
  - git_diff
  - git_log
  - git_commit
prompt_override: |
  You are the code specialist within Crowe Logic.
  You edit, refactor, debug, and write production-quality code.
  You use filesystem and git tools to navigate and modify codebases.
  You never use emojis. Output is clean and professional.
pipelines:
  - refactor.yaml
```

Create `agents/research.yaml`:

```yaml
name: research
description: "Web research and information gathering specialist"
model: gpt-oss120-120b
tools:
  - web_search
  - browse_url
  - grep_search
  - read_file
prompt_override: |
  You are the research specialist within Crowe Logic.
  You search the web, read pages, and synthesize information.
  You provide concise, sourced summaries.
  You never use emojis. Output is clean and professional.
pipelines:
  - research.yaml
```

Create `agents/music.yaml`:

```yaml
name: music
description: "Talon composition specialist — generates MIDI, orchestrates tracks, analyzes audio"
model: gpt-oss120-120b
tools:
  - talon_*
  - read_file
  - execute_shell
prompt_override: |
  You are the music specialist within Crowe Logic.
  You compose using the Talon Music Engine.
  You think in terms of key, tempo, groove, and emotion.
  Available grooves: tight, loose, swing, funk, prog, floyd, devastating.
  Available emotions: grief, rage, bliss, anxiety, nostalgia, awe, longing,
  triumph, dread, serenity, wonder, melancholy, fury, tenderness, desolation,
  ecstasy, suspense, ethereal.
  You never use emojis. Output is clean and professional.
pipelines:
  - compose.yaml
quantum_evaluator: melody_path
```

Create `agents/quantum.yaml`:

```yaml
name: quantum
description: "Quantum circuit design and evaluation specialist"
model: gpt-oss120-120b
tools:
  - run_quantum_circuit
  - synapse_evaluate
  - qubit_flow_execute
  - execute_shell
prompt_override: |
  You are the quantum computing specialist within Crowe Logic.
  You design and execute quantum circuits using Qiskit, Cirq, PennyLane,
  Synapse-Lang, and Qubit-Flow. You explain quantum concepts clearly.
  You never use emojis. Output is clean and professional.
```

Create `agents/cultivation.yaml`:

```yaml
name: cultivation
description: "Mycology knowledge and growing protocol specialist"
model: gpt-oss120-120b
tools:
  - web_search
  - browse_url
  - read_file
  - write_file
prompt_override: |
  You are the cultivation specialist within Crowe Logic.
  You have deep knowledge of mycology, mushroom cultivation, substrate
  preparation, environmental controls, and commercial growing operations.
  You reference The Mushroom Grower methodology when applicable.
  You never use emojis. Output is clean and professional.
```

- [ ] **Step 4: Implement the agent registry**

Create `crowe_synapse/agent_registry.py`:

```python
"""
Crowe-Synapse Agent Registry — load and manage YAML-defined sub-agents.

Each agent is a persona: a system prompt override, a tool subset, and
optional pipeline templates. Agents reuse the same model (gpt-oss120-120b)
with different instructions.
"""

import fnmatch
import os
from dataclasses import dataclass, field

import yaml


@dataclass
class AgentConfig:
    name: str
    description: str = ""
    model: str = "gpt-oss120-120b"
    tools: list[str] = field(default_factory=list)
    prompt_override: str = ""
    pipelines: list[str] = field(default_factory=list)
    quantum_evaluator: str | None = None


class AgentRegistry:
    def __init__(self, agents_dir: str = ""):
        self._agents: dict[str, AgentConfig] = {}
        if agents_dir and os.path.isdir(agents_dir):
            self._load_agents(agents_dir)

    def _load_agents(self, agents_dir: str):
        for filename in sorted(os.listdir(agents_dir)):
            if filename.endswith((".yaml", ".yml")):
                path = os.path.join(agents_dir, filename)
                with open(path) as f:
                    data = yaml.safe_load(f)
                if data and "name" in data:
                    agent = AgentConfig(
                        name=data["name"],
                        description=data.get("description", ""),
                        model=data.get("model", "gpt-oss120-120b"),
                        tools=data.get("tools", []),
                        prompt_override=data.get("prompt_override", ""),
                        pipelines=data.get("pipelines", []),
                        quantum_evaluator=data.get("quantum_evaluator"),
                    )
                    self._agents[agent.name] = agent

    def list_agents(self) -> list[AgentConfig]:
        return list(self._agents.values())

    def get_agent(self, name: str) -> AgentConfig | None:
        return self._agents.get(name)

    def resolve_tools(self, agent: AgentConfig, available_tools: set[str]) -> set[str]:
        """Resolve tool patterns (including globs like 'talon_*') against available tools."""
        resolved = set()
        for pattern in agent.tools:
            if "*" in pattern or "?" in pattern:
                for tool_name in available_tools:
                    if fnmatch.fnmatch(tool_name, pattern):
                        resolved.add(tool_name)
            elif pattern in available_tools:
                resolved.add(pattern)
        return resolved
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_agent_registry.py -v`
Expected: All 8 tests PASS

- [ ] **Step 6: Commit**

```bash
git add crowe_synapse/agent_registry.py agents/ tests/test_agent_registry.py
git commit -m "feat: implement agent registry with YAML definitions and tool resolution"
```

---

### Task 5: Quantum Bridge

**Files:**
- Create: `crowe_synapse/quantum_bridge.py`
- Create: `tests/test_quantum_bridge.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_quantum_bridge.py`:

```python
"""Tests for crowe_synapse.quantum_bridge — pluggable quantum decisions."""

import pytest
from crowe_synapse.quantum_bridge import DecisionPoint, QuantumBridge


@pytest.fixture
def bridge():
    return QuantumBridge()


class TestDecisionPoint:
    def test_classical_default_when_no_evaluator(self, bridge):
        dp = DecisionPoint(
            name="test_route",
            candidates=["code", "music", "research"],
            classical_default="code",
            quantum_evaluator=None,
        )
        result = bridge.decide(dp)
        assert result == "code"

    def test_classical_default_when_quantum_unavailable(self, bridge):
        dp = DecisionPoint(
            name="test_route",
            candidates=["code", "music"],
            classical_default="music",
            quantum_evaluator="synapse.route(candidates, tension=0.5)",
        )
        # Even with an evaluator string, if synapse-lang isn't importable
        # in the test environment, it should fall back to classical
        result = bridge.decide(dp)
        assert result in dp.candidates

    def test_decide_returns_valid_candidate(self, bridge):
        dp = DecisionPoint(
            name="test",
            candidates=["a", "b", "c"],
            classical_default="b",
        )
        result = bridge.decide(dp)
        assert result in dp.candidates


class TestQuantumAvailability:
    def test_quantum_available_is_bool(self, bridge):
        assert isinstance(bridge.quantum_available, bool)

    def test_bridge_reports_status(self, bridge):
        status = bridge.status()
        assert "available" in status
        assert isinstance(status["available"], bool)
        assert "synapse_lang" in status
        assert "qubit_flow" in status


class TestWeightedDecision:
    def test_weighted_classical_selection(self, bridge):
        dp = DecisionPoint(
            name="weighted",
            candidates=["a", "b", "c"],
            classical_default="a",
            weights={"a": 0.7, "b": 0.2, "c": 0.1},
        )
        # With classical fallback using weights, result should still be valid
        results = {bridge.decide(dp) for _ in range(20)}
        assert results.issubset({"a", "b", "c"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_quantum_bridge.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the quantum bridge**

Create `crowe_synapse/quantum_bridge.py`:

```python
"""
Crowe-Synapse Quantum Bridge — pluggable decision points.

Any routing decision, pipeline step, or parameter selection can optionally
flow through Synapse-Lang or Qubit-Flow quantum evaluation. When quantum
packages aren't installed, all decisions use classical defaults. Zero overhead
when quantum isn't active.
"""

import random
from dataclasses import dataclass, field


# Check quantum availability once at import time
_synapse_available = False
_qubit_flow_available = False

try:
    from synapse_lang import SynapseLang
    _synapse_available = True
except ImportError:
    pass

try:
    from qubit_flow_lang import QubitFlowInterpreter
    _qubit_flow_available = True
except ImportError:
    pass


@dataclass
class DecisionPoint:
    name: str
    candidates: list[str]
    classical_default: str
    quantum_evaluator: str | None = None
    weights: dict[str, float] = field(default_factory=dict)


class QuantumBridge:
    def __init__(self):
        self._synapse = SynapseLang() if _synapse_available else None
        self._qubit_flow = QubitFlowInterpreter() if _qubit_flow_available else None

    @property
    def quantum_available(self) -> bool:
        return _synapse_available or _qubit_flow_available

    def status(self) -> dict:
        return {
            "available": self.quantum_available,
            "synapse_lang": _synapse_available,
            "qubit_flow": _qubit_flow_available,
        }

    def decide(self, dp: DecisionPoint) -> str:
        """Evaluate a decision point. Uses quantum if available, classical otherwise."""
        # Try quantum evaluation first
        if dp.quantum_evaluator and self._synapse:
            try:
                result = self._quantum_evaluate(dp)
                if result in dp.candidates:
                    return result
            except Exception:
                pass  # fall through to classical

        # Classical: use weights if provided, otherwise return default
        if dp.weights:
            return self._weighted_choice(dp)
        return dp.classical_default

    def _quantum_evaluate(self, dp: DecisionPoint) -> str:
        """Run a Synapse-Lang expression to pick a candidate."""
        result = self._synapse.evaluate(dp.quantum_evaluator)
        return str(result)

    def _weighted_choice(self, dp: DecisionPoint) -> str:
        """Weighted random selection from candidates using provided weights."""
        candidates = []
        weights = []
        for c in dp.candidates:
            candidates.append(c)
            weights.append(dp.weights.get(c, 0.0))
        total = sum(weights)
        if total == 0:
            return dp.classical_default
        return random.choices(candidates, weights=weights, k=1)[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_quantum_bridge.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add crowe_synapse/quantum_bridge.py tests/test_quantum_bridge.py
git commit -m "feat: implement quantum bridge with pluggable decision points"
```

---

### Task 6: Orchestrator

**Files:**
- Create: `crowe_synapse/orchestrator.py`
- Create: `tests/test_orchestrator.py`
- Modify: `crowe_synapse/__init__.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orchestrator.py`:

```python
"""Tests for crowe_synapse.orchestrator — the central coordinator."""

import json
import os
import tempfile
import pytest
from crowe_synapse.orchestrator import Orchestrator


@pytest.fixture
def orch(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    agents_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "agents")
    templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "crowe_synapse", "templates")
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_orchestrator.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the orchestrator**

Create `crowe_synapse/orchestrator.py`:

```python
"""
Crowe-Synapse Orchestrator — the central coordinator.

Routes tasks, manages sessions, dispatches pipelines and agents,
injects context from memory into the model's system prompt.
"""

import os
from crowe_synapse.memory import MemoryStore
from crowe_synapse.pipeline import PipelineEngine
from crowe_synapse.agent_registry import AgentRegistry
from crowe_synapse.quantum_bridge import QuantumBridge, DecisionPoint


class Orchestrator:
    def __init__(self, db_path: str = "~/.crowe-logic/memory.db",
                 agents_dir: str = "", templates_dir: str = ""):
        self.memory = MemoryStore(db_path=db_path)
        self.pipeline_engine = PipelineEngine(templates_dir=templates_dir)
        self.agent_registry = AgentRegistry(agents_dir=agents_dir)
        self.quantum = QuantumBridge()
        self._current_session_id: str | None = None

    # ── Session Lifecycle ────────────────────────────────────

    def start_session(self, thread_id: str) -> str:
        context = self._build_project_context()
        self._current_session_id = self.memory.start_session(
            thread_id=thread_id, project_context=context
        )
        return self._current_session_id

    def end_session(self, summary: str = ""):
        if self._current_session_id:
            self.memory.end_session(self._current_session_id, summary=summary)
            self._current_session_id = None

    def get_history(self, limit: int = 10) -> list[dict]:
        return self.memory.get_recent_sessions(limit=limit)

    # ── Pre-Message Preparation ──────────────────────────────

    def prepare(self, user_input: str, thread_id: str) -> dict:
        """Analyze user input and prepare execution context."""
        # Check for pipeline template match
        template = self.pipeline_engine.match_template(user_input)
        if template:
            return {
                "mode": "pipeline",
                "pipeline_name": template.name,
                "template": template,
                "injection": self._build_context_injection(),
            }

        # Check for agent delegation (future: quantum-enhanced routing)
        agent = self._route_to_agent(user_input)
        if agent:
            return {
                "mode": "delegated",
                "agent_name": agent.name,
                "agent": agent,
                "pipeline_name": None,
                "injection": self._build_context_injection(),
            }

        # Default: direct execution
        return {
            "mode": "direct",
            "pipeline_name": None,
            "injection": self._build_context_injection(),
        }

    # ── Post-Execution Recording ─────────────────────────────

    def record_execution(self, tool_name: str, arguments: str = "",
                         output: str = "", duration_ms: int = 0,
                         status: str = "success"):
        self.memory.record_tool_execution(
            session_id=self._current_session_id,
            tool_name=tool_name,
            arguments=arguments,
            output=output,
            duration_ms=duration_ms,
            status=status,
        )

    # ── Listing ──────────────────────────────────────────────

    def list_agents(self):
        return self.agent_registry.list_agents()

    def list_pipelines(self):
        return self.pipeline_engine.list_templates()

    # ── Internal ─────────────────────────────────────────────

    def _route_to_agent(self, user_input: str):
        """Simple keyword-based agent routing. Returns AgentConfig or None."""
        text = user_input.lower()
        agents = self.agent_registry.list_agents()
        if not agents:
            return None

        # Build candidate list for potential quantum routing
        candidates = [a.name for a in agents]

        # Classical routing: keyword matching
        keyword_map = {
            "music": ["compose", "music", "melody", "chord", "drum", "talon", "midi", "song", "track"],
            "quantum": ["quantum", "qubit", "circuit", "synapse", "superposition", "entangle"],
            "cultivation": ["mushroom", "substrate", "mycelium", "fruiting", "spawn", "cultivation", "growing"],
            "research": ["research", "look up", "find out", "search for", "investigate"],
            "code": ["refactor", "debug", "function", "class", "import", "compile", "test"],
        }

        best_agent = None
        best_score = 0
        for agent_name, keywords in keyword_map.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > best_score:
                best_score = score
                best_agent = agent_name

        if best_score == 0:
            return None

        return self.agent_registry.get_agent(best_agent)

    def _build_context_injection(self) -> str:
        """Build context string to inject into system prompt from memory."""
        parts = []

        # Recent session summaries
        sessions = self.memory.get_recent_sessions(limit=3)
        for s in sessions:
            if s.get("summary") and s["id"] != self._current_session_id:
                parts.append(f"Previous session: {s['summary']}")

        # Project knowledge
        knowledge = self.memory.get_all_knowledge()
        for k in knowledge[:10]:
            parts.append(f"{k['key']}: {k['value']}")

        return "\n".join(parts) if parts else ""

    def _build_project_context(self) -> str:
        """Build initial project context for session start."""
        knowledge = self.memory.get_all_knowledge()
        if knowledge:
            return "; ".join(f"{k['key']}={k['value']}" for k in knowledge[:5])
        return ""
```

- [ ] **Step 4: Update crowe_synapse/__init__.py with exports**

Replace the contents of `crowe_synapse/__init__.py`:

```python
"""
Crowe-Synapse — Orchestration Framework for Crowe Logic

Pipeline engine, multi-agent coordination, persistent memory,
and pluggable quantum decision-making.
"""

__version__ = "0.1.0"

from crowe_synapse.orchestrator import Orchestrator
from crowe_synapse.pipeline import PipelineEngine, PipelineStep, PipelineRun, PipelineTemplate
from crowe_synapse.agent_registry import AgentRegistry, AgentConfig
from crowe_synapse.memory import MemoryStore
from crowe_synapse.quantum_bridge import QuantumBridge, DecisionPoint
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_orchestrator.py -v`
Expected: All 9 tests PASS

- [ ] **Step 6: Run full test suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: All 33 tests PASS (10 memory + 10 pipeline + 8 agent + 6 quantum + 9 orchestrator - some overlap but approximately this count)

- [ ] **Step 7: Commit**

```bash
git add crowe_synapse/orchestrator.py crowe_synapse/__init__.py tests/test_orchestrator.py
git commit -m "feat: implement orchestrator with routing, context injection, and memory integration"
```

---

### Task 7: CLI Integration

**Files:**
- Modify: `cli/crowe_logic.py`

- [ ] **Step 1: Add the lazy-loaded orchestrator to cli/crowe_logic.py**

After the existing `_get_tool_map()` function (around line 130), add:

```python
_orchestrator = None

def _get_orchestrator():
    """Lazy-loaded Crowe-Synapse orchestrator."""
    global _orchestrator
    if _orchestrator is None:
        from crowe_synapse import Orchestrator
        _orchestrator = Orchestrator(
            db_path=os.path.expanduser("~/.crowe-logic/memory.db"),
            agents_dir=os.path.join(PROJECT_ROOT, "agents"),
            templates_dir=os.path.join(PROJECT_ROOT, "crowe_synapse", "templates"),
        )
    return _orchestrator
```

- [ ] **Step 2: Wire orchestrator into the chat() function**

In `chat()`, after `thread = client.threads.create()` and before `show_welcome()`, add session start:

```python
    orch = _get_orchestrator()
    session_id = orch.start_session(thread_id=thread.id)
```

In the `while True` loop, before the `stream_response()` call, add context preparation:

```python
            ctx = orch.prepare(user_input, thread_id=thread.id)
```

After the `stream_response()` call succeeds (inside the retry loop, after `last_error = None; break`), the tool recording will be handled in stream_response itself (next step).

In the `except (EOFError, KeyboardInterrupt)` handler, before the goodbye message, add:

```python
            orch.end_session(summary="Session ended by user")
```

- [ ] **Step 3: Wire tool execution recording into stream_response()**

In `stream_response()`, after Phase 2 executes a tool call (after `_stop_spinner()` on line 273), add timing and recording:

```python
                _get_orchestrator().record_execution(
                    tool_name=tc.function.name,
                    arguments=tc.function.arguments,
                    output=result[:10000],
                    duration_ms=int((time.monotonic() - _tool_start) * 1000),
                )
```

And before the tool execution (line 271), capture the start time:

```python
                _tool_start = time.monotonic()
```

- [ ] **Step 4: Add four new CLI commands**

After the existing `tools` command (around line 530), add:

```python
@main.command()
def agents():
    """List registered sub-agents."""
    orch = _get_orchestrator()
    agent_list = orch.list_agents()
    if not agent_list:
        console.print("  [dim]No agents configured[/dim]")
        return
    table = Table(
        title="Sub-Agents",
        box=box.ROUNDED,
        border_style="#bfa669",
        title_style="bold #bfa669",
        header_style="bold white",
        padding=(0, 1),
    )
    table.add_column("Agent", style="#bfa669", min_width=14)
    table.add_column("Description", style="white")
    table.add_column("Tools", style="dim")
    for a in agent_list:
        tools_str = ", ".join(a.tools[:4])
        if len(a.tools) > 4:
            tools_str += f" +{len(a.tools) - 4}"
        table.add_row(a.name, a.description, tools_str)
    console.print()
    console.print(table)
    console.print()


@main.command()
def pipelines():
    """List available pipeline templates."""
    orch = _get_orchestrator()
    pipe_list = orch.list_pipelines()
    if not pipe_list:
        console.print("  [dim]No pipelines configured[/dim]")
        return
    table = Table(
        title="Pipeline Templates",
        box=box.ROUNDED,
        border_style="#bfa669",
        title_style="bold #bfa669",
        header_style="bold white",
        padding=(0, 1),
    )
    table.add_column("Pipeline", style="#bfa669", min_width=14)
    table.add_column("Description", style="white")
    table.add_column("Trigger", style="dim")
    for p in pipe_list:
        table.add_row(p.name, p.description, p.trigger or "")
    console.print()
    console.print(table)
    console.print()


@main.command()
@click.option("--limit", default=10, help="Number of sessions to show")
def history(limit: int):
    """Show recent chat sessions."""
    orch = _get_orchestrator()
    sessions = orch.get_history(limit=limit)
    if not sessions:
        console.print("  [dim]No session history yet[/dim]")
        return
    table = Table(
        title="Session History",
        box=box.ROUNDED,
        border_style="#bfa669",
        title_style="bold #bfa669",
        header_style="bold white",
        padding=(0, 1),
    )
    table.add_column("Started", style="#bfa669", min_width=20)
    table.add_column("Thread", style="dim", max_width=20)
    table.add_column("Summary", style="white")
    for s in sessions:
        started = s.get("started_at", "")[:19]
        thread = s.get("thread_id", "")[:16] + "..."
        summary = (s.get("summary") or "[dim]no summary[/dim]")[:60]
        table.add_row(started, thread, summary)
    console.print()
    console.print(table)
    console.print()


@main.command()
def resume():
    """Resume the last chat session with context."""
    orch = _get_orchestrator()
    sessions = orch.get_history(limit=1)
    if not sessions:
        console.print("  [dim]No previous sessions to resume[/dim]")
        return
    last = sessions[0]
    thread_id = last["thread_id"]
    console.print(f"  [#bfa669]Resuming session:[/#bfa669] {last.get('summary', 'no summary')}")
    console.print(f"  [dim]Thread: {thread_id}[/dim]")
    # Start a new session linked to the previous context
    agent_id = get_agent_id()
    client = get_client()
    orch.start_session(thread_id=thread_id)
    # Proceed to chat loop using the existing thread
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.formatted_text import HTML
    history_file = os.path.join(PROJECT_ROOT, ".chat_history")
    session = PromptSession(history=FileHistory(history_file))
    prompt_html = HTML('<style fg="#bfa669">\u276f </style>')
    favicon = get_favicon()
    while True:
        try:
            user_input = session.prompt(prompt_html, multiline=False)
        except (EOFError, KeyboardInterrupt):
            orch.end_session(summary="Resumed session ended by user")
            console.print("\n  [bold #bfa669]Goodbye.[/bold #bfa669]\n")
            break
        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            orch.end_session(summary="Resumed session ended by user")
            console.print("  [bold #bfa669]Goodbye.[/bold #bfa669]\n")
            break
        try:
            _cancel_active_runs(client, thread_id)
            client.messages.create(thread_id=thread_id, role="user", content=user_input)
            console.print()
            sys.stdout.write(f"  {favicon} ")
            sys.stdout.flush()
            console.print("[bold #bfa669]crowe-logic[/bold #bfa669]")
            stream_response(client, thread_id, agent_id)
            console.print(f"  [dim #bfa669]{'─' * min(60, console.width)}[/dim #bfa669]")
        except Exception as e:
            _render_error(str(e))
```

- [ ] **Step 5: Verify syntax compiles**

Run: `.venv/bin/python -c "import py_compile; py_compile.compile('cli/crowe_logic.py', doraise=True); print('OK')"`
Expected: `OK`

- [ ] **Step 6: Run full test suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add cli/crowe_logic.py
git commit -m "feat: integrate crowe-synapse orchestrator into CLI with 4 new commands"
```

---

### Task 8: End-to-End Verification and Push

- [ ] **Step 1: Run all tests one final time**

Run: `.venv/bin/pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 2: Verify the CLI boots without errors**

Run: `.venv/bin/python -c "from crowe_synapse import Orchestrator, PipelineEngine, AgentRegistry, MemoryStore, QuantumBridge; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 3: Verify CLI commands**

Run: `.venv/bin/python -m cli.crowe_logic agents`
Expected: Table showing 5 agents (code, research, music, quantum, cultivation)

Run: `.venv/bin/python -m cli.crowe_logic pipelines`
Expected: Table showing 3 pipelines (refactor, research, compose)

- [ ] **Step 4: Push all changes**

```bash
git push origin main
```

- [ ] **Step 5: Update memory file**

Update the project memory at `/Users/crowelogic/.claude/projects/-Users-crowelogic/memory/project_crowe_logic_foundry.md` to reflect Phase 2 completion.
