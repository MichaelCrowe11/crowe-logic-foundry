# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio — proprietary, private repository.

"""
Studio routing tool — routes captured clips into any registered tenant
pipeline without hardcoding tenants in the agent.

Reads config/studio_tenants.yaml as the source of truth. Adding a new
pipeline is a YAML edit: no code changes needed, and the studio agent
can discover it via list_tenants on the next call.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TENANTS_PATH = Path(os.environ.get(
    "STUDIO_TENANTS_PATH",
    str(REPO_ROOT / "config" / "studio_tenants.yaml"),
))


def _load_registry() -> dict:
    if not TENANTS_PATH.exists():
        return {"tenants": []}
    with TENANTS_PATH.open() as f:
        return yaml.safe_load(f) or {"tenants": []}


def _find_tenant(name: str) -> Optional[dict]:
    reg = _load_registry()
    for t in reg.get("tenants", []):
        if t.get("name") == name:
            return t
    return None


def _resolve_raw_dir(tenant: dict) -> Path:
    raw = tenant["raw_dir"]
    p = Path(raw)
    if not p.is_absolute():
        p = Path(tenant["root"]) / raw
    return p


def _timestamp_id() -> str:
    return time.strftime("iphone-%Y%m%d-%H%M%S")


def list_tenants() -> str:
    """
    Enumerate every content pipeline the studio agent can route to.

    Reads config/studio_tenants.yaml. Return includes each tenant's label,
    target platforms, default capture specs, and notes so the agent can
    pick a destination intelligently.

    :return: JSON array of tenant summaries.
    :rtype: str
    """
    try:
        reg = _load_registry()
        out = []
        for t in reg.get("tenants", []):
            out.append({
                "name": t.get("name"),
                "label": t.get("label"),
                "platforms": t.get("platforms", []),
                "default_specs": t.get("default_specs", {}),
                "notes": t.get("notes", "").strip(),
                "raw_dir": str(_resolve_raw_dir(t)),
                "has_ingest_cmd": bool(t.get("ingest_cmd")),
            })
        return json.dumps(out)
    except Exception as e:
        return json.dumps({"error": str(e)})


def get_tenant(name: str) -> str:
    """
    Fetch the full config for a single tenant, including the exact
    ingest command that will be executed after a clip drop.

    :param name: Tenant identifier (e.g. "toxicteetv", "southwest-mushrooms").
    :return: JSON object with the tenant config, or error if unknown.
    :rtype: str
    """
    try:
        t = _find_tenant(name)
        if not t:
            return json.dumps({"error": f"Unknown tenant: {name}"})
        resolved = dict(t)
        resolved["raw_dir_absolute"] = str(_resolve_raw_dir(t))
        return json.dumps(resolved)
    except Exception as e:
        return json.dumps({"error": str(e)})


def route_clip_to_tenant(
    clip_path: str,
    tenant: str,
    session_id: str = "",
    move: bool = False,
) -> str:
    """
    Drop a captured clip into the tenant's raw inbox and trigger its
    ingest command. Generic: works for any tenant declared in
    config/studio_tenants.yaml.

    The clip is placed under <raw_dir>/<session_id>/<filename> so the
    tenant pipeline can group multiple clips from one session.

    :param clip_path: Absolute path to the source clip on disk.
    :param tenant: Tenant name from the registry.
    :param session_id: Session folder name. Auto-generated if empty.
    :param move: If true, move the clip; default copies it so the
        original capture stays at $CAPTURE_ROOT/out for reference.
    :return: JSON with {tenant, session_id, session_dir, dest_path,
        bytes, ingest, manifest_id}.
    :rtype: str
    """
    try:
        src = Path(clip_path).expanduser().resolve()
        if not src.exists():
            return json.dumps({"error": f"Source not found: {src}"})

        t = _find_tenant(tenant)
        if not t:
            return json.dumps({"error": f"Unknown tenant: {tenant}"})

        raw_dir = _resolve_raw_dir(t)
        sid = session_id or _timestamp_id()
        session_dir = raw_dir / sid
        session_dir.mkdir(parents=True, exist_ok=True)

        dest = session_dir / src.name
        if dest.exists():
            dest = session_dir / f"{src.stem}-{int(time.time())}{src.suffix}"

        if move:
            shutil.move(str(src), str(dest))
        else:
            shutil.copy2(str(src), str(dest))

        dest_size = dest.stat().st_size

        ingest_info = None
        ingest_cmd = t.get("ingest_cmd")
        if ingest_cmd:
            substitutions = {
                "clip_path": str(dest),
                "session_dir": str(session_dir),
                "session_id": sid,
                "tenant_root": t["root"],
            }
            resolved_cmd = [str(arg).format(**substitutions) for arg in ingest_cmd]
            proc = subprocess.run(
                resolved_cmd,
                cwd=t["root"],
                capture_output=True,
                text=True,
                timeout=300,
            )
            ingest_info = {
                "cmd": resolved_cmd,
                "exit": proc.returncode,
                "stdout_tail": (proc.stdout or "")[-800:],
                "stderr_tail": (proc.stderr or "")[-400:],
            }

        manifest_id = None
        manifests_dir = t.get("manifests_dir")
        if manifests_dir:
            mdir = Path(manifests_dir)
            if mdir.exists():
                for mf in sorted(mdir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
                    try:
                        m = json.loads(mf.read_text())
                    except Exception:
                        continue
                    if m.get("id") == sid:
                        manifest_id = m["id"]
                        break
                    srcs = m.get("source_files") or []
                    if any(sid in str(s) for s in srcs):
                        manifest_id = m.get("id")
                        break

        return json.dumps({
            "tenant": tenant,
            "session_id": sid,
            "session_dir": str(session_dir),
            "dest_path": str(dest),
            "bytes": dest_size,
            "moved": bool(move),
            "ingest": ingest_info,
            "manifest_id": manifest_id,
        })
    except subprocess.TimeoutExpired as e:
        return json.dumps({"error": "Tenant ingest timed out", "detail": str(e)})
    except Exception as e:
        return json.dumps({"error": str(e)})


def tenant_inbox_peek(tenant: str, limit: int = 10) -> str:
    """
    Show the most recent sessions in a tenant's raw inbox. Useful after
    a capture to confirm delivery, or before one to see what's already
    staged.

    :param tenant: Tenant name.
    :param limit: Max number of sessions to list.
    :return: JSON array of {session_id, files, total_bytes, mtime}.
    :rtype: str
    """
    try:
        t = _find_tenant(tenant)
        if not t:
            return json.dumps({"error": f"Unknown tenant: {tenant}"})
        raw = _resolve_raw_dir(t)
        if not raw.exists():
            return json.dumps([])
        sessions = []
        for entry in sorted(raw.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            if not entry.is_dir():
                continue
            files = []
            total = 0
            for f in entry.iterdir():
                if f.is_file():
                    size = f.stat().st_size
                    total += size
                    files.append({"name": f.name, "bytes": size})
            sessions.append({
                "session_id": entry.name,
                "files": files,
                "total_bytes": total,
                "mtime": entry.stat().st_mtime,
            })
        return json.dumps(sessions)
    except Exception as e:
        return json.dumps({"error": str(e)})
