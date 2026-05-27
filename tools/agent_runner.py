# tools/agent_runner.py
"""
Secure agent runner for CroweLM pipeline.

Runs agent tasks with isolation:
- Local mode: subprocess with restricted env vars (no .env, API keys, or git creds)
- Docker mode: container with read-only dataset mount and read-write staging
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import uuid

import tools.audit_log as _audit
import tools.staging_pipeline as _staging

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "crowelm-unified"
)

AGENTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "agents", "scripts"
)


def run_agent(agent_id: str, task: str, mode: str = "local") -> dict:
    """Run a pipeline agent task with isolation.

    :param agent_id: Snake-cased agent identifier (must match ``[A-Za-z0-9_-]+``).
        Resolves against ``agents/<agent_id>.yaml`` plus the per-agent
        scripts under ``agents/scripts/``.
    :param task: Free-form task description handed to the agent runner.
    :param mode: Execution mode. ``"local"`` runs the agent in a
        subprocess with stripped credentials. ``"docker"`` runs inside
        a container with read-only dataset mounts and a writable
        staging volume. Default is ``"local"``.
    :return: Dict with ``status`` (``"ok"`` / ``"error"``), ``run_id``,
        ``message``, and any agent-emitted artifacts.
    :rtype: dict
    """
    if not _SAFE_ID.match(agent_id):
        return {"status": "error", "message": f"Invalid agent_id: {agent_id!r}"}

    if mode not in ("local", "docker"):
        return {"status": "error", "message": f"Unknown mode: {mode}", "run_id": None}

    run_id = str(uuid.uuid4())[:8]
    _staging.ensure_staging_dirs()
    _audit.log_action(agent_id, "run_start", {"task": task, "mode": mode}, run_id)

    try:
        if mode == "local":
            result = _run_local(agent_id, task, run_id)
        else:
            result = _run_docker(agent_id, task, run_id)

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
        "HOME": tempfile.gettempdir(),
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
