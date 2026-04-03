# CroweLM Agent Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the secure execution, staging pipeline, and audit logging infrastructure for the CroweLM agent-driven data pipeline (Sub-Project 1 of 3).

**Architecture:** Three new modules in `tools/`: `audit_log.py` (structured JSONL logging), `staging_pipeline.py` (item routing through pending/approved/review/rejected with tiered gate), and `agent_runner.py` (secure task execution via subprocess or Docker). A GitHub Actions workflow dispatches pipeline runs in CI. All modules follow the existing pattern: functions return JSON strings, tests patch module-level directory constants to use `tmp_path`.

**Tech Stack:** Python 3.12, pytest, Docker, GitHub Actions

---

## File Structure

| File | Responsibility |
|------|---------------|
| `tools/audit_log.py` | Structured JSONL logging for all agent actions |
| `tools/staging_pipeline.py` | Stage items, apply tiered quality gate, promote approved to curated |
| `tools/agent_runner.py` | Run agents in subprocess (local) or Docker (production) with env isolation |
| `tests/test_audit_log.py` | Audit logger tests |
| `tests/test_staging_pipeline.py` | Staging pipeline + tiered gate tests (boundary values) |
| `tests/test_agent_runner.py` | Agent runner tests (subprocess, Docker mock, env isolation) |
| `tests/contracts/__init__.py` | Contract test package marker |
| `tests/contracts/test_contracts.py` | Structural invariants: staging dirs, quality scores, log fields, JSONL schema |
| `.github/workflows/crowelm-pipeline.yml` | GitHub Actions workflow for production pipeline runs |
| `tools/__init__.py` | Register new agent-facing functions in `user_functions` |
| `.env.example` | Add `CROWELM_STAGING_DIR`, `CROWELM_AUTO_APPROVE_THRESHOLD`, `CROWELM_REVIEW_THRESHOLD` |

---

### Task 1: Audit Logger

**Files:**
- Create: `tools/audit_log.py`
- Create: `tests/test_audit_log.py`

- [ ] **Step 1: Write the test file**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/crowelogic/Projects/crowe-logic-foundry && .venv/bin/pytest tests/test_audit_log.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.audit_log'`

- [ ] **Step 3: Write the implementation**

```python
# tools/audit_log.py
"""
Audit logger for CroweLM agent pipeline.

Structured JSONL logs in data/crowelm-unified/logs/.
Every agent action is logged for forensic reconstruction.
"""

import json
import os
import time
import uuid

LOGS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "crowelm-unified", "logs"
)


def log_action(agent_id: str, action: str, details: dict = None, run_id: str = None) -> dict:
    """Log a single agent action to the audit log.

    Returns the log entry dict with id, timestamp, agent_id, action, run_id, details.
    """
    os.makedirs(LOGS_DIR, exist_ok=True)
    entry = {
        "id": str(uuid.uuid4())[:8],
        "timestamp": time.time(),
        "agent_id": agent_id,
        "action": action,
        "run_id": run_id or "unknown",
        "details": details or {},
    }
    log_file = os.path.join(LOGS_DIR, f"{agent_id}.jsonl")
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def get_run_log(agent_id: str, run_id: str) -> list:
    """Get all log entries for a specific agent run."""
    log_file = os.path.join(LOGS_DIR, f"{agent_id}.jsonl")
    if not os.path.exists(log_file):
        return []
    entries = []
    with open(log_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("run_id") == run_id:
                entries.append(entry)
    return entries


def get_agent_log(agent_id: str, limit: int = 100) -> list:
    """Get the most recent log entries for an agent."""
    log_file = os.path.join(LOGS_DIR, f"{agent_id}.jsonl")
    if not os.path.exists(log_file):
        return []
    entries = []
    with open(log_file) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries[-limit:]


def crowelm_audit_log(agent_id: str, limit: int = 100) -> str:
    """
    View audit log for a CroweLM pipeline agent.

    :param agent_id: Agent identifier.
    :param limit: Max entries to return.
    :return: JSON with log entries.
    :rtype: str
    """
    try:
        entries = get_agent_log(agent_id, limit)
        return json.dumps({"agent_id": agent_id, "entries": entries, "count": len(entries)})
    except Exception as e:
        return json.dumps({"error": str(e)})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/crowelogic/Projects/crowe-logic-foundry && .venv/bin/pytest tests/test_audit_log.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/crowelogic/Projects/crowe-logic-foundry
git add tools/audit_log.py tests/test_audit_log.py
git commit -m "feat(crowelm): add audit logger for pipeline tracing"
```

---

### Task 2: Staging Pipeline

**Files:**
- Create: `tools/staging_pipeline.py`
- Create: `tests/test_staging_pipeline.py`

**Context:** This module imports `tools.audit_log` (built in Task 1). Tests patch both `STAGING_DIR` and `audit_log.LOGS_DIR` to temp directories.

- [ ] **Step 1: Write the test file**

```python
# tests/test_staging_pipeline.py
"""Tests for tools.staging_pipeline -- CroweLM staging and tiered gate."""

import json
import os
import pytest
import tools.staging_pipeline as staging_mod
import tools.audit_log as audit_mod


@pytest.fixture
def staging_env(tmp_path):
    """Redirect staging and logs to temp directories."""
    old_staging = staging_mod.STAGING_DIR
    old_logs = audit_mod.LOGS_DIR
    staging_mod.STAGING_DIR = str(tmp_path / "staging")
    audit_mod.LOGS_DIR = str(tmp_path / "logs")
    yield tmp_path
    staging_mod.STAGING_DIR = old_staging
    audit_mod.LOGS_DIR = old_logs


class TestEnsureStagingDirs:
    def test_creates_all_subdirs(self, staging_env):
        staging_mod.ensure_staging_dirs()
        staging = staging_env / "staging"
        for subdir in ["pending", "approved", "review", "rejected"]:
            assert (staging / subdir).is_dir()


class TestStageItem:
    def test_creates_pending_file(self, staging_env):
        item = staging_mod.stage_item("gen-agent", {"instruction": "test", "response": "resp"})
        pending = staging_env / "staging" / "pending" / f"{item['id']}.jsonl"
        assert pending.exists()

    def test_returns_metadata(self, staging_env):
        item = staging_mod.stage_item("gen-agent", {"instruction": "q"}, run_id="run-1")
        assert "id" in item
        assert isinstance(item["timestamp"], float)
        assert item["agent_id"] == "gen-agent"
        assert item["run_id"] == "run-1"
        assert item["data"]["instruction"] == "q"


class TestApplyGate:
    def test_auto_approve_above_085(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        result = staging_mod.apply_gate(staged["id"], 0.9)
        assert result["destination"] == "approved"
        assert not (staging_env / "staging" / "pending" / f"{staged['id']}.jsonl").exists()
        assert (staging_env / "staging" / "approved" / f"{staged['id']}.jsonl").exists()

    def test_review_between_05_and_085(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        result = staging_mod.apply_gate(staged["id"], 0.7)
        assert result["destination"] == "review"
        assert (staging_env / "staging" / "review" / f"{staged['id']}.jsonl").exists()

    def test_reject_below_05(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        result = staging_mod.apply_gate(staged["id"], 0.3)
        assert result["destination"] == "rejected"
        assert (staging_env / "staging" / "rejected" / f"{staged['id']}.jsonl").exists()

    def test_boundary_085_is_approved(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        assert staging_mod.apply_gate(staged["id"], 0.85)["destination"] == "approved"

    def test_boundary_050_is_review(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        assert staging_mod.apply_gate(staged["id"], 0.5)["destination"] == "review"

    def test_boundary_049_is_rejected(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        assert staging_mod.apply_gate(staged["id"], 0.49)["destination"] == "rejected"

    def test_boundary_084_is_review(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        assert staging_mod.apply_gate(staged["id"], 0.84)["destination"] == "review"

    def test_approved_item_has_quality_score(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        staging_mod.apply_gate(staged["id"], 0.9)
        path = staging_env / "staging" / "approved" / f"{staged['id']}.jsonl"
        item = json.loads(path.read_text().strip())
        assert item["quality_score"] == 0.9

    def test_missing_item_returns_error(self, staging_env):
        staging_mod.ensure_staging_dirs()
        result = staging_mod.apply_gate("nonexistent", 0.9)
        assert "error" in result


class TestListStaged:
    def test_lists_pending_items(self, staging_env):
        staging_mod.stage_item("agent", {"instruction": "q1"})
        staging_mod.stage_item("agent", {"instruction": "q2"})
        assert len(staging_mod.list_staged("pending")) == 2

    def test_invalid_status_returns_empty(self, staging_env):
        assert staging_mod.list_staged("invalid") == []


class TestPromoteApproved:
    def test_moves_to_curated(self, staging_env):
        staged = staging_mod.stage_item("agent", {
            "instruction": "How to grow shiitake?",
            "response": "Use hardwood sawdust blocks.",
            "category": "mycology",
        })
        staging_mod.apply_gate(staged["id"], 0.9)
        result = staging_mod.promote_approved()
        assert result["promoted"] == 1
        curated = staging_env / "curated" / "mycology.jsonl"
        assert curated.exists()
        example = json.loads(curated.read_text().strip())
        assert example["instruction"] == "How to grow shiitake?"
        assert example["quality_score"] == 0.9

    def test_removes_from_approved_after_promote(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        staging_mod.apply_gate(staged["id"], 0.9)
        staging_mod.promote_approved()
        assert len(list((staging_env / "staging" / "approved").iterdir())) == 0

    def test_returns_zero_when_empty(self, staging_env):
        staging_mod.ensure_staging_dirs()
        assert staging_mod.promote_approved()["promoted"] == 0


class TestCrowelmWrappers:
    def test_list_staging_returns_json(self, staging_env):
        staging_mod.stage_item("agent", {"instruction": "q"})
        result = json.loads(staging_mod.crowelm_list_staging("pending"))
        assert result["count"] == 1

    def test_promote_returns_json(self, staging_env):
        staging_mod.ensure_staging_dirs()
        result = json.loads(staging_mod.crowelm_promote_approved())
        assert result["promoted"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/crowelogic/Projects/crowe-logic-foundry && .venv/bin/pytest tests/test_staging_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.staging_pipeline'`

- [ ] **Step 3: Write the implementation**

```python
# tools/staging_pipeline.py
"""
Staging pipeline for CroweLM agent-generated data.

All agent writes go through staging:
  pending -> evaluation -> approved | review | rejected

Agents never write directly to curated data.
"""

import json
import os
import time
import uuid

import tools.audit_log as _audit

STAGING_DIR = os.environ.get(
    "CROWELM_STAGING_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "data", "crowelm-unified", "staging")
)

AUTO_APPROVE_THRESHOLD = float(os.environ.get("CROWELM_AUTO_APPROVE_THRESHOLD", "0.85"))
REVIEW_THRESHOLD = float(os.environ.get("CROWELM_REVIEW_THRESHOLD", "0.5"))

SUBDIRS = ["pending", "approved", "review", "rejected"]


def ensure_staging_dirs():
    """Create staging subdirectories if they don't exist."""
    for subdir in SUBDIRS:
        os.makedirs(os.path.join(STAGING_DIR, subdir), exist_ok=True)


def stage_item(agent_id: str, item: dict, run_id: str = None) -> dict:
    """Write an item to staging/pending/ with metadata."""
    ensure_staging_dirs()
    item_id = str(uuid.uuid4())[:8]
    staged = {
        "id": item_id,
        "timestamp": time.time(),
        "agent_id": agent_id,
        "run_id": run_id or "unknown",
        "confidence": item.get("confidence"),
        "data": item,
    }
    pending_file = os.path.join(STAGING_DIR, "pending", f"{item_id}.jsonl")
    with open(pending_file, "w") as f:
        f.write(json.dumps(staged) + "\n")
    _audit.log_action(agent_id, "stage_item", {"item_id": item_id}, run_id)
    return staged


def apply_gate(item_id: str, score: float) -> dict:
    """Apply tiered gate to a pending item based on quality score.

    >= 0.85 -> approved
    >= 0.50 -> review
    <  0.50 -> rejected
    """
    pending_path = os.path.join(STAGING_DIR, "pending", f"{item_id}.jsonl")
    if not os.path.exists(pending_path):
        return {"error": f"Item {item_id} not found in pending"}
    with open(pending_path) as f:
        item = json.loads(f.read().strip())
    item["quality_score"] = score
    if score >= AUTO_APPROVE_THRESHOLD:
        destination = "approved"
    elif score >= REVIEW_THRESHOLD:
        destination = "review"
    else:
        destination = "rejected"
    item["gate_result"] = destination
    dest_path = os.path.join(STAGING_DIR, destination, f"{item_id}.jsonl")
    with open(dest_path, "w") as f:
        f.write(json.dumps(item) + "\n")
    os.remove(pending_path)
    _audit.log_action(
        item.get("agent_id", "unknown"),
        "gate_decision",
        {"item_id": item_id, "score": score, "destination": destination},
        item.get("run_id"),
    )
    return {"item_id": item_id, "score": score, "destination": destination}


def list_staged(status: str = "pending") -> list:
    """List items in a staging subdirectory."""
    if status not in SUBDIRS:
        return []
    subdir = os.path.join(STAGING_DIR, status)
    if not os.path.exists(subdir):
        return []
    items = []
    for filename in sorted(os.listdir(subdir)):
        if filename.endswith(".jsonl"):
            with open(os.path.join(subdir, filename)) as f:
                items.append(json.loads(f.read().strip()))
    return items


def promote_approved() -> dict:
    """Move approved items to the curated dataset."""
    approved_dir = os.path.join(STAGING_DIR, "approved")
    if not os.path.exists(approved_dir):
        return {"promoted": 0}
    curated_dir = os.path.join(os.path.dirname(STAGING_DIR), "curated")
    os.makedirs(curated_dir, exist_ok=True)
    promoted = 0
    for filename in sorted(os.listdir(approved_dir)):
        if not filename.endswith(".jsonl"):
            continue
        with open(os.path.join(approved_dir, filename)) as f:
            item = json.loads(f.read().strip())
        data = item.get("data", {})
        category = data.get("category", "general")
        example = {
            "id": item["id"],
            "instruction": data.get("instruction", ""),
            "response": data.get("response", ""),
            "category": category,
            "persona": data.get("persona"),
            "quality_score": item.get("quality_score"),
            "source_agent": item.get("agent_id"),
        }
        curated_file = os.path.join(curated_dir, f"{category}.jsonl")
        with open(curated_file, "a") as f:
            f.write(json.dumps(example) + "\n")
        os.remove(os.path.join(approved_dir, filename))
        promoted += 1
    return {"promoted": promoted}


# Agent-facing wrappers (return JSON strings for Azure AI Agent Service)

def crowelm_list_staging(status: str = "pending") -> str:
    """
    List items in CroweLM staging pipeline.

    :param status: Staging status — pending, approved, review, or rejected.
    :return: JSON with staged items.
    :rtype: str
    """
    try:
        items = list_staged(status)
        return json.dumps({"status": status, "items": items, "count": len(items)})
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowelm_promote_approved() -> str:
    """
    Move approved staging items to the curated dataset.

    :return: JSON with promotion count.
    :rtype: str
    """
    try:
        return json.dumps(promote_approved())
    except Exception as e:
        return json.dumps({"error": str(e)})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/crowelogic/Projects/crowe-logic-foundry && .venv/bin/pytest tests/test_staging_pipeline.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/crowelogic/Projects/crowe-logic-foundry
git add tools/staging_pipeline.py tests/test_staging_pipeline.py
git commit -m "feat(crowelm): add staging pipeline with tiered gate"
```

---

### Task 3: Agent Runner

**Files:**
- Create: `tools/agent_runner.py`
- Create: `tests/test_agent_runner.py`

**Context:** Imports `tools.audit_log` and `tools.staging_pipeline` (Tasks 1-2). Uses `import tools.audit_log as _audit` and `import tools.staging_pipeline as _staging` so test patches to module-level vars propagate correctly. Tests create temporary agent scripts and mock Docker via `unittest.mock.patch`.

- [ ] **Step 1: Write the test file**

```python
# tests/test_agent_runner.py
"""Tests for tools.agent_runner -- CroweLM secure agent execution."""

import json
import os
import pytest
from unittest.mock import patch, MagicMock
import tools.agent_runner as runner_mod
import tools.audit_log as audit_mod
import tools.staging_pipeline as staging_mod


@pytest.fixture
def runner_env(tmp_path):
    """Set up isolated environment for agent runner tests."""
    old_data = runner_mod.DATA_DIR
    old_agents = runner_mod.AGENTS_DIR
    old_staging = staging_mod.STAGING_DIR
    old_logs = audit_mod.LOGS_DIR

    runner_mod.DATA_DIR = str(tmp_path / "data")
    runner_mod.AGENTS_DIR = str(tmp_path / "agents")
    staging_mod.STAGING_DIR = str(tmp_path / "staging")
    audit_mod.LOGS_DIR = str(tmp_path / "logs")

    os.makedirs(tmp_path / "data")
    os.makedirs(tmp_path / "agents")

    yield tmp_path

    runner_mod.DATA_DIR = old_data
    runner_mod.AGENTS_DIR = old_agents
    staging_mod.STAGING_DIR = old_staging
    audit_mod.LOGS_DIR = old_logs


def _write_agent_script(runner_env, agent_id="test_agent", code=None):
    """Write a minimal test agent script."""
    if code is None:
        code = (
            "import json, os\n"
            "print(json.dumps({\n"
            '    "status": "complete",\n'
            '    "items_staged": 0,\n'
            '    "agent_id": os.environ.get("CROWELM_AGENT_ID", ""),\n'
            '    "run_id": os.environ.get("CROWELM_RUN_ID", ""),\n'
            "}))\n"
        )
    script = runner_env / "agents" / f"{agent_id}.py"
    script.write_text(code)


class TestRunAgent:
    def test_returns_run_id_and_agent_id(self, runner_env):
        _write_agent_script(runner_env)
        result = runner_mod.run_agent("test_agent", "do something")
        assert "run_id" in result
        assert result["agent_id"] == "test_agent"

    def test_script_not_found(self, runner_env):
        result = runner_mod.run_agent("nonexistent", "task")
        assert result["status"] == "error"
        assert "not found" in result["message"]

    def test_logs_start_and_complete(self, runner_env):
        _write_agent_script(runner_env)
        result = runner_mod.run_agent("test_agent", "task")
        entries = audit_mod.get_run_log("test_agent", result["run_id"])
        actions = [e["action"] for e in entries]
        assert "run_start" in actions
        assert "run_complete" in actions

    def test_unknown_mode_returns_error(self, runner_env):
        result = runner_mod.run_agent("x", "task", mode="quantum")
        assert "error" in result


class TestRunLocal:
    def test_restricted_env_hides_secrets(self, runner_env):
        code = (
            "import json, os\n"
            "print(json.dumps({\n"
            '    "has_openrouter": "OPENROUTER_API_KEY" in os.environ,\n'
            '    "has_project": "PROJECT_ENDPOINT" in os.environ,\n'
            '    "has_agent_id": "CROWELM_AGENT_ID" in os.environ,\n'
            "}))\n"
        )
        _write_agent_script(runner_env, code=code)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-secret", "PROJECT_ENDPOINT": "https://x"}):
            result = runner_mod.run_agent("test_agent", "check env")
        output = result.get("output", {})
        assert output.get("has_openrouter") is False
        assert output.get("has_project") is False
        assert output.get("has_agent_id") is True

    def test_agent_receives_task_in_env(self, runner_env):
        code = (
            "import json, os\n"
            'print(json.dumps({"task": os.environ.get("CROWELM_TASK", "")}))\n'
        )
        _write_agent_script(runner_env, code=code)
        result = runner_mod.run_agent("test_agent", "grow shiitake")
        assert result["output"]["task"] == "grow shiitake"


class TestRunDocker:
    def test_docker_command_has_isolation_flags(self, runner_env):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"status": "complete", "items_staged": 0}',
                stderr="",
            )
            runner_mod.run_agent("test_agent", "task", mode="docker")

            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "docker"
            assert "--rm" in cmd
            v_indices = [i for i, a in enumerate(cmd) if a == "-v"]
            mounts = [cmd[i + 1] for i in v_indices]
            assert any(m.endswith(":ro") for m in mounts), "Data must be read-only"
            assert any(m.endswith(":rw") for m in mounts), "Staging must be read-write"


class TestCrowelmRunAgent:
    def test_returns_json_string(self, runner_env):
        _write_agent_script(runner_env)
        result = json.loads(runner_mod.crowelm_run_agent("test_agent", "task"))
        assert "run_id" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/crowelogic/Projects/crowe-logic-foundry && .venv/bin/pytest tests/test_agent_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.agent_runner'`

- [ ] **Step 3: Write the implementation**

```python
# tools/agent_runner.py
"""
Secure agent runner for CroweLM pipeline.

Runs agent tasks with isolation:
- Local mode: subprocess with restricted env vars (no .env, API keys, or git creds)
- Docker mode: container with read-only dataset mount and read-write staging
"""

import json
import os
import subprocess
import sys
import uuid

import tools.audit_log as _audit
import tools.staging_pipeline as _staging

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "crowelm-unified"
)

AGENTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "agents", "scripts"
)


def run_agent(agent_id: str, task: str, mode: str = "local") -> dict:
    """Run a pipeline agent task with isolation."""
    run_id = str(uuid.uuid4())[:8]
    _staging.ensure_staging_dirs()
    _audit.log_action(agent_id, "run_start", {"task": task, "mode": mode}, run_id)

    try:
        if mode == "local":
            result = _run_local(agent_id, task, run_id)
        elif mode == "docker":
            result = _run_docker(agent_id, task, run_id)
        else:
            return {"error": f"Unknown mode: {mode}", "run_id": run_id}

        _audit.log_action(agent_id, "run_complete", {
            "status": result.get("status"),
            "items_staged": result.get("items_staged", 0),
        }, run_id)
        result["run_id"] = run_id
        result["agent_id"] = agent_id
        return result
    except Exception as e:
        _audit.log_action(agent_id, "run_error", {"error": str(e)}, run_id)
        return {"error": str(e), "run_id": run_id}


def _run_local(agent_id, task, run_id):
    """Run agent as subprocess with restricted environment."""
    safe_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "CROWELM_DATA_DIR": DATA_DIR,
        "CROWELM_STAGING_DIR": _staging.STAGING_DIR,
        "CROWELM_AGENT_ID": agent_id,
        "CROWELM_RUN_ID": run_id,
        "CROWELM_TASK": task,
    }
    script_path = os.path.join(AGENTS_DIR, f"{agent_id}.py")
    if not os.path.exists(script_path):
        return {"status": "error", "message": f"Agent script not found: {script_path}"}

    try:
        proc = subprocess.run(
            [sys.executable, script_path],
            env=safe_env,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Agent timed out after 300s"}

    output = _parse_agent_output(proc.stdout)

    if proc.returncode != 0:
        return {
            "status": "error",
            "message": proc.stderr[-500:] if proc.stderr else "Non-zero exit",
            "output": output,
        }
    return {
        "status": "complete",
        "output": output,
        "items_staged": output.get("items_staged", 0),
    }


def _run_docker(agent_id, task, run_id):
    """Run agent in Docker container with isolation."""
    container_name = f"crowelm-{agent_id}-{run_id}"
    cmd = [
        "docker", "run",
        "--name", container_name,
        "--rm",
        "-v", f"{DATA_DIR}:/data:ro",
        "-v", f"{_staging.STAGING_DIR}:/staging:rw",
        "-e", f"CROWELM_AGENT_ID={agent_id}",
        "-e", f"CROWELM_RUN_ID={run_id}",
        "-e", f"CROWELM_TASK={task}",
        "-e", "CROWELM_DATA_DIR=/data",
        "-e", "CROWELM_STAGING_DIR=/staging",
        "crowelm-agent:latest",
        "python", f"/app/agents/scripts/{agent_id}.py",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", container_name], capture_output=True)
        return {"status": "error", "message": "Container timed out after 600s"}

    output = _parse_agent_output(proc.stdout)

    if proc.returncode != 0:
        return {
            "status": "error",
            "message": proc.stderr[-500:] if proc.stderr else "Container failed",
            "output": output,
        }
    return {
        "status": "complete",
        "output": output,
        "items_staged": output.get("items_staged", 0),
    }


def _parse_agent_output(stdout):
    """Parse the last JSON line from agent stdout."""
    if not stdout:
        return {}
    for line in reversed(stdout.strip().split("\n")):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {}


def crowelm_run_agent(agent_id: str, task: str, mode: str = "local") -> str:
    """
    Run a CroweLM pipeline agent with secure isolation.

    :param agent_id: Agent name (matches agents/scripts/{agent_id}.py).
    :param task: Task description for the agent.
    :param mode: Execution mode — "local" (subprocess) or "docker" (container).
    :return: JSON with run_id, status, and output.
    :rtype: str
    """
    try:
        return json.dumps(run_agent(agent_id, task, mode))
    except Exception as e:
        return json.dumps({"error": str(e)})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/crowelogic/Projects/crowe-logic-foundry && .venv/bin/pytest tests/test_agent_runner.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/crowelogic/Projects/crowe-logic-foundry
git add tools/agent_runner.py tests/test_agent_runner.py
git commit -m "feat(crowelm): add secure agent runner with subprocess and docker modes"
```

---

### Task 4: Contract Tests

**Files:**
- Create: `tests/contracts/__init__.py`
- Create: `tests/contracts/test_contracts.py`

**Context:** Human-maintained invariants that agents cannot modify. These verify structural guarantees across all pipeline operations.

- [ ] **Step 1: Create the contract test package and test file**

```python
# tests/contracts/__init__.py
```

```python
# tests/contracts/test_contracts.py
"""
Contract tests for CroweLM pipeline infrastructure.

Human-maintained only. Agents cannot modify these.
Verify structural invariants that must hold across all pipeline operations.
"""

import json
import pytest
import tools.staging_pipeline as staging_mod
import tools.audit_log as audit_mod


@pytest.fixture
def pipeline_env(tmp_path):
    """Isolated environment for contract tests."""
    old_staging = staging_mod.STAGING_DIR
    old_logs = audit_mod.LOGS_DIR
    staging_mod.STAGING_DIR = str(tmp_path / "staging")
    audit_mod.LOGS_DIR = str(tmp_path / "logs")
    yield tmp_path
    staging_mod.STAGING_DIR = old_staging
    audit_mod.LOGS_DIR = old_logs


class TestStagingStructureContracts:
    """Staging directory structure is always preserved."""

    def test_ensure_creates_all_four_subdirs(self, pipeline_env):
        staging_mod.ensure_staging_dirs()
        staging = pipeline_env / "staging"
        assert (staging / "pending").is_dir()
        assert (staging / "approved").is_dir()
        assert (staging / "review").is_dir()
        assert (staging / "rejected").is_dir()

    def test_stage_item_only_writes_to_pending(self, pipeline_env):
        staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        staging = pipeline_env / "staging"
        assert len(list((staging / "pending").iterdir())) == 1
        assert len(list((staging / "approved").iterdir())) == 0
        assert len(list((staging / "review").iterdir())) == 0
        assert len(list((staging / "rejected").iterdir())) == 0


class TestApprovedItemContracts:
    """Approved examples always have a quality score attached."""

    def test_approved_item_has_quality_score(self, pipeline_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        staging_mod.apply_gate(staged["id"], 0.9)
        path = pipeline_env / "staging" / "approved" / f"{staged['id']}.jsonl"
        item = json.loads(path.read_text().strip())
        assert "quality_score" in item
        assert isinstance(item["quality_score"], (int, float))

    def test_review_item_has_quality_score(self, pipeline_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        staging_mod.apply_gate(staged["id"], 0.7)
        path = pipeline_env / "staging" / "review" / f"{staged['id']}.jsonl"
        item = json.loads(path.read_text().strip())
        assert "quality_score" in item


class TestAuditLogContracts:
    """Audit logs always have agent_id, timestamp, action fields."""

    def test_log_entry_has_required_fields(self, pipeline_env):
        entry = audit_mod.log_action("agent-x", "test_action")
        assert "agent_id" in entry
        assert "timestamp" in entry
        assert "action" in entry
        assert isinstance(entry["timestamp"], float)

    def test_persisted_entries_are_valid_jsonl(self, pipeline_env):
        audit_mod.log_action("agent-y", "action_1")
        audit_mod.log_action("agent-y", "action_2")
        log_file = pipeline_env / "logs" / "agent-y.jsonl"
        for line in log_file.read_text().strip().split("\n"):
            entry = json.loads(line)
            assert "agent_id" in entry
            assert "timestamp" in entry
            assert "action" in entry


class TestDatasetSchemaContracts:
    """Dataset JSONL schema: instruction and response required."""

    def test_promoted_example_has_instruction_and_response(self, pipeline_env):
        staged = staging_mod.stage_item("agent", {
            "instruction": "How to grow oyster mushrooms?",
            "response": "Start with straw substrate.",
            "category": "mycology",
        })
        staging_mod.apply_gate(staged["id"], 0.9)
        staging_mod.promote_approved()
        curated = pipeline_env / "curated" / "mycology.jsonl"
        example = json.loads(curated.read_text().strip())
        assert "instruction" in example
        assert "response" in example
        assert len(example["instruction"]) > 0
        assert len(example["response"]) > 0
```

- [ ] **Step 2: Run contract tests**

Run: `cd /Users/crowelogic/Projects/crowe-logic-foundry && .venv/bin/pytest tests/contracts/ -v`
Expected: 7 passed

- [ ] **Step 3: Commit**

```bash
cd /Users/crowelogic/Projects/crowe-logic-foundry
git add tests/contracts/__init__.py tests/contracts/test_contracts.py
git commit -m "test(crowelm): add contract tests for pipeline invariants"
```

---

### Task 5: GitHub Actions Workflow

**Files:**
- Create: `.github/workflows/crowelm-pipeline.yml`

- [ ] **Step 1: Write the workflow file**

```yaml
# .github/workflows/crowelm-pipeline.yml
name: CroweLM Pipeline

on:
  workflow_dispatch:
    inputs:
      agent:
        description: 'Agent to run (e.g. crowelm_gen_mycology)'
        required: true
        type: string
      task:
        description: 'Task description for the agent'
        required: true
        type: string

jobs:
  run-agent:
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Create staging directories
        run: mkdir -p data/crowelm-unified/staging/{pending,approved,review,rejected} data/crowelm-unified/logs data/crowelm-unified/reports

      - name: Run pipeline agent
        run: |
          python -c "
          from tools.agent_runner import run_agent
          import json, sys
          result = run_agent('${{ inputs.agent }}', '''${{ inputs.task }}''', mode='local')
          print(json.dumps(result, indent=2))
          if result.get('status') == 'error':
              sys.exit(1)
          "

      - name: Promote approved examples
        run: |
          python -c "
          from tools.staging_pipeline import promote_approved
          import json
          result = promote_approved()
          print(json.dumps(result, indent=2))
          "

      - name: Create PR with approved examples
        if: success()
        run: |
          git config user.name "CroweLM Pipeline"
          git config user.email "crowelm@crowelogic.com"
          BRANCH="crowelm/batch-$(date +%Y%m%d-%H%M%S)"
          git checkout -b "$BRANCH"
          git add data/crowelm-unified/curated/ data/crowelm-unified/logs/ data/crowelm-unified/reports/ || true
          if git diff --cached --quiet; then
            echo "No new examples to commit"
          else
            git commit -m "feat(crowelm): approved training examples from pipeline run"
            git push origin "$BRANCH"
            gh pr create \
              --title "CroweLM: New approved training examples" \
              --body "Automated pipeline run for agent: ${{ inputs.agent }}. Review examples before merging." \
              --base main
          fi
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

- [ ] **Step 2: Commit**

```bash
cd /Users/crowelogic/Projects/crowe-logic-foundry
git add .github/workflows/crowelm-pipeline.yml
git commit -m "ci(crowelm): add GitHub Actions pipeline workflow"
```

---

### Task 6: Integration

**Files:**
- Modify: `tools/__init__.py`
- Modify: `.env.example`

- [ ] **Step 1: Register new tools in `tools/__init__.py`**

Add after the existing CroweLM imports (line 46):

```python
# CroweLM pipeline infrastructure
from tools.audit_log import crowelm_audit_log
from tools.staging_pipeline import crowelm_list_staging, crowelm_promote_approved
from tools.agent_runner import crowelm_run_agent
```

Add to the `user_functions` set after the existing CroweLM entries (after line 96):

```python
    # CroweLM pipeline infrastructure
    crowelm_audit_log, crowelm_list_staging, crowelm_promote_approved, crowelm_run_agent,
```

- [ ] **Step 2: Add new environment variables to `.env.example`**

Append to the end of `.env.example`:

```
# CroweLM Agent Pipeline
CROWELM_STAGING_DIR=data/crowelm-unified/staging
CROWELM_AUTO_APPROVE_THRESHOLD=0.85
CROWELM_REVIEW_THRESHOLD=0.5
```

- [ ] **Step 3: Create agents/scripts directory**

```bash
mkdir -p /Users/crowelogic/Projects/crowe-logic-foundry/agents/scripts
touch /Users/crowelogic/Projects/crowe-logic-foundry/agents/scripts/.gitkeep
```

- [ ] **Step 4: Run full test suite to verify nothing broke**

Run: `cd /Users/crowelogic/Projects/crowe-logic-foundry && .venv/bin/pytest tests/test_audit_log.py tests/test_staging_pipeline.py tests/test_agent_runner.py tests/contracts/ -v`
Expected: All tests pass (35+ tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/crowelogic/Projects/crowe-logic-foundry
git add tools/__init__.py .env.example agents/scripts/.gitkeep
git commit -m "feat(crowelm): register pipeline tools and update config"
```
