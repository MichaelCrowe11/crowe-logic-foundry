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
