"""Shared runtime/session helpers for interactive and headless Crowe Logic."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path


_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.environ.get("CROWE_LOGIC_PROJECT_ROOT", _PACKAGE_ROOT)
_RUNTIME_DIR = Path.home() / ".crowe-logic" / "runtime"
_DATASET_MANIFEST_PATH = Path(_PROJECT_ROOT) / "data" / "crowelm-unified" / "DATASET_MANIFEST.json"
_DEFAULT_DATASET_SELECTION = "all"


def _session_path(session_id: str) -> Path:
    raw = (session_id or "default").strip() or "default"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-") or "default"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return _RUNTIME_DIR / f"{safe[:48]}-{digest}.json"


def _default_session_state() -> dict:
    return {
        "steering_instruction": "",
        "dataset_selection": _DEFAULT_DATASET_SELECTION,
        "last_answer_text": "",
        "last_reasoning_text": "",
        "last_model": "",
        "updated_at": 0.0,
    }


def load_session_runtime(session_id: str) -> dict:
    """Return persisted runtime state for the given session id."""
    state = _default_session_state()
    path = _session_path(session_id)
    if not path.exists():
        return state
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return state
    if isinstance(data, dict):
        state.update({key: value for key, value in data.items() if key in state})
    return state


def update_session_runtime(session_id: str, **fields) -> dict:
    """Persist selected runtime fields for the session and return merged state."""
    state = load_session_runtime(session_id)
    for key, value in fields.items():
        if key in state:
            state[key] = value
    state["updated_at"] = time.time()
    _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    _session_path(session_id).write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def load_dataset_manifest() -> dict:
    """Load the CroweLM dataset manifest, returning an empty dict when absent."""
    if not _DATASET_MANIFEST_PATH.exists():
        return {}
    try:
        return json.loads(_DATASET_MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def available_dataset_names() -> list[str]:
    """Return sorted dataset names advertised by the manifest."""
    manifest = load_dataset_manifest()
    acquired = manifest.get("datasets_acquired", {})
    if not isinstance(acquired, dict):
        return []
    return sorted(str(name).strip() for name in acquired if str(name).strip())


def resolve_dataset_selection(selection: str) -> str | None:
    """Resolve a user dataset selector to a canonical dataset name or mode."""
    candidate = (selection or "").strip()
    if not candidate:
        return _DEFAULT_DATASET_SELECTION

    lowered = candidate.lower()
    if lowered in {"off", "none", "disable", "disabled"}:
        return "off"
    if lowered in {"all", "summary", "default"}:
        return _DEFAULT_DATASET_SELECTION

    names = available_dataset_names()
    exact = {name.lower(): name for name in names}
    if lowered in exact:
        return exact[lowered]

    partial = [name for name in names if lowered in name.lower()]
    if len(partial) == 1:
        return partial[0]
    return None


def build_dataset_context(selection: str = _DEFAULT_DATASET_SELECTION) -> str:
    """Build a compact CroweLM dataset context block for prompt injection."""
    if selection == "off":
        return ""

    manifest = load_dataset_manifest()
    if not manifest:
        return ""

    summary = manifest.get("summary", {}) or {}
    acquired = manifest.get("datasets_acquired", {}) or {}
    top_domains = manifest.get("top_domains", {}) or {}

    lines = [
        "CroweLM training corpus is available locally through dataset tools.",
        (
            f"Raw samples: {summary.get('total_raw_samples', 0):,}; "
            f"training entries: {summary.get('crowelm_training_entries', 0):,}; "
            f"size: {summary.get('total_size_gb', 0)} GB."
        ),
    ]

    domains = str(summary.get("domains", "")).strip()
    if domains:
        lines.append(f"Primary domains: {domains}.")

    if top_domains:
        top = ", ".join(
            f"{name} ({count:,})"
            for name, count in list(top_domains.items())[:3]
        )
        if top:
            lines.append(f"Top domains: {top}.")

    if selection not in {"", _DEFAULT_DATASET_SELECTION}:
        desc = acquired.get(selection)
        if desc:
            lines.append(f"Active dataset focus: {selection} — {desc}")
        else:
            lines.append(f"Active dataset focus: {selection}")

    lines.append(
        "Use CroweLM dataset and search tools when task quality depends on corpus-specific facts, examples, or training configuration."
    )
    return "\n".join(lines)


def build_runtime_system_instructions(model_cfg: dict | None = None, *, session_id: str = "") -> str:
    """Compose system instructions with session steering and dataset context."""
    from config.agent_config import build_system_instructions

    parts = [build_system_instructions(model_cfg)]
    if session_id:
        runtime = load_session_runtime(session_id)
    else:
        runtime = _default_session_state()

    steering = str(runtime.get("steering_instruction", "") or "").strip()
    if steering:
        parts.append(
            "## Active Operator Steering\n"
            "The operator has applied persistent direction for this session. "
            "Treat it as a higher-priority execution preference unless the user explicitly changes it.\n"
            f"{steering}"
        )

    dataset_context = build_dataset_context(str(runtime.get("dataset_selection", _DEFAULT_DATASET_SELECTION)))
    if dataset_context:
        parts.append("## CroweLM Dataset Context\n" + dataset_context)

    return "\n\n".join(part for part in parts if part and part.strip())


def format_transcript_markdown(runtime_state: dict) -> str:
    """Render the last stored answer/reasoning into markdown for transcript views."""
    answer = str(runtime_state.get("last_answer_text", "") or "").strip()
    reasoning = str(runtime_state.get("last_reasoning_text", "") or "").strip()
    model = str(runtime_state.get("last_model", "") or "").strip()

    if not answer and not reasoning:
        return "No transcript available for this session yet."

    parts: list[str] = []
    if model:
        parts.append(f"# Last Transcript\n\nModel: {model}")
    else:
        parts.append("# Last Transcript")
    if answer:
        parts.append("## Answer\n\n" + answer)
    if reasoning:
        parts.append("## Full Reasoning\n\n" + reasoning)
    return "\n\n".join(parts)


def handle_local_control_command(command_text: str, *, session_id: str) -> str | None:
    """Handle local slash commands that should not call a model provider."""
    raw = (command_text or "").strip()
    if not raw.startswith("/"):
        return None

    cmd, _, arg = raw.partition(" ")
    cmd = cmd.lower()
    arg = arg.strip()

    if cmd == "/transcript":
        return format_transcript_markdown(load_session_runtime(session_id))

    if cmd == "/steer":
        if not arg:
            current = load_session_runtime(session_id).get("steering_instruction", "")
            if current:
                return f"Active steering:\n\n{current}"
            return "No active steering for this session."
        if arg.lower() in {"clear", "off", "reset", "none"}:
            update_session_runtime(session_id, steering_instruction="")
            return "Cleared session steering."
        update_session_runtime(session_id, steering_instruction=arg)
        return "Updated session steering.\n\n" + arg

    if cmd == "/dataset":
        if not arg:
            runtime = load_session_runtime(session_id)
            current = runtime.get("dataset_selection", _DEFAULT_DATASET_SELECTION)
            names = available_dataset_names()
            if names:
                preview = ", ".join(names[:8])
                suffix = "..." if len(names) > 8 else ""
                return (
                    f"Active dataset context: {current}\n\n"
                    f"Available datasets: {preview}{suffix}\n"
                    "Use `/dataset off`, `/dataset all`, or `/dataset <name>`."
                )
            return f"Active dataset context: {current}"

        resolved = resolve_dataset_selection(arg)
        if resolved is None:
            names = available_dataset_names()
            preview = ", ".join(names[:8]) if names else "none found"
            suffix = "..." if len(names) > 8 else ""
            return (
                f"Unknown dataset selection: {arg}\n\n"
                f"Available datasets: {preview}{suffix}"
            )
        update_session_runtime(session_id, dataset_selection=resolved)
        if resolved == "off":
            return "Disabled injected CroweLM dataset context for this session."
        if resolved == _DEFAULT_DATASET_SELECTION:
            return "Using the default CroweLM dataset summary context for this session."
        return f"Using dataset-focused context for this session: {resolved}"

    return None
