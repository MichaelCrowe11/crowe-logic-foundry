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
