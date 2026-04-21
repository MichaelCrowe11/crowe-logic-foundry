#!/usr/bin/env python3
"""
Issue a Crowe Logic tester API key (`cl_<hex>`) and wire it up to a workspace
so the holder can immediately hit the `/api/gateway/chat` surface.

Two modes:

  • Local (default) — seeds a persistent SQLite database used by
    control_plane/preview.py. Works offline, no Postgres required.
        python scripts/issue_tester_key.py --label tester-dev --plan lab

  • Remote (--remote) — POSTs to a live Control Plane at
    $CROWE_CONTROL_PLANE_URL using an admin bearer token
    $CROWE_ADMIN_TOKEN and a target $CROWE_ADMIN_WORKSPACE_ID.
        python scripts/issue_tester_key.py --remote --label tester-dev

In both modes the raw key is printed exactly once. Hand it to the tester;
they drop it into:

    export CROWE_LOGIC_KEY=cl_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

and call the gateway with:

    curl -H "Authorization: Bearer $CROWE_LOGIC_KEY" \\
         -H "Content-Type: application/json" \\
         -d '{"model":"gpt-5.4","messages":[{"role":"user","content":"hi"}]}' \\
         $CROWE_CONTROL_PLANE_URL/api/gateway/chat
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "control_plane_preview.sqlite"

VALID_PLANS = {"developer", "studio", "lab", "enterprise"}
DEFAULT_SCOPES = ["chat", "vision", "agents"]


# ─── Local SQLite mode ──────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, display_name TEXT,
    password_hash TEXT, role TEXT DEFAULT 'researcher', avatar_url TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
    owner_id TEXT NOT NULL, stripe_customer_id TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL, name TEXT NOT NULL,
    slug TEXT NOT NULL, ws_type TEXT DEFAULT 'personal',
    plan_id TEXT DEFAULT 'developer', stripe_subscription_id TEXT,
    status TEXT DEFAULT 'active', settings TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
    key_hash TEXT NOT NULL, key_prefix TEXT NOT NULL,
    label TEXT DEFAULT 'default',
    scopes TEXT DEFAULT '["chat","vision","agents"]',
    last_used_at TEXT, expires_at TEXT, revoked INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def _issue_local(db_path: Path, email: str, label: str, plan: str,
                 scopes: list[str]) -> dict:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)

    # Upsert tester user
    user = conn.execute(
        "SELECT id FROM users WHERE email = ?", (email,)
    ).fetchone()
    if user:
        user_id = user[0]
    else:
        user_id = secrets.token_hex(16)
        conn.execute(
            "INSERT INTO users (id, email, display_name, role) "
            "VALUES (?, ?, ?, 'tester')",
            (user_id, email, email.split("@")[0]),
        )

    # Upsert personal org + workspace
    org_row = conn.execute(
        "SELECT id FROM organizations WHERE owner_id = ?", (user_id,)
    ).fetchone()
    if org_row:
        org_id = org_row[0]
    else:
        org_id = secrets.token_hex(16)
        conn.execute(
            "INSERT INTO organizations (id, name, slug, owner_id) "
            "VALUES (?, ?, ?, ?)",
            (org_id, f"{email} org", f"org-{user_id[:8]}", user_id),
        )

    ws_row = conn.execute(
        "SELECT id, plan_id FROM workspaces WHERE org_id = ? LIMIT 1",
        (org_id,),
    ).fetchone()
    if ws_row:
        workspace_id = ws_row[0]
        if ws_row[1] != plan:
            conn.execute(
                "UPDATE workspaces SET plan_id = ? WHERE id = ?",
                (plan, workspace_id),
            )
    else:
        workspace_id = secrets.token_hex(16)
        conn.execute(
            "INSERT INTO workspaces (id, org_id, name, slug, plan_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (workspace_id, org_id, "Tester Workspace",
             f"ws-{user_id[:8]}", plan),
        )

    # Mint key
    raw_key = f"cl_{secrets.token_hex(24)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:11]
    key_id = secrets.token_hex(16)
    conn.execute(
        "INSERT INTO api_keys (id, workspace_id, user_id, key_hash, "
        "key_prefix, label, scopes) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (key_id, workspace_id, user_id, key_hash, key_prefix, label,
         json.dumps(scopes)),
    )
    conn.commit()
    conn.close()

    return {
        "mode": "local",
        "db": str(db_path),
        "workspace_id": workspace_id,
        "user_id": user_id,
        "key_id": key_id,
        "key_prefix": key_prefix,
        "key": raw_key,
        "plan": plan,
        "scopes": scopes,
    }


# ─── Remote Control Plane mode ──────────────────────────────────────────

def _issue_remote(label: str, scopes: list[str]) -> dict:
    import httpx

    base_url = os.environ.get("CROWE_CONTROL_PLANE_URL", "").rstrip("/")
    admin_token = os.environ.get("CROWE_ADMIN_TOKEN", "")
    workspace_id = os.environ.get("CROWE_ADMIN_WORKSPACE_ID", "")

    missing = [n for n, v in [
        ("CROWE_CONTROL_PLANE_URL", base_url),
        ("CROWE_ADMIN_TOKEN", admin_token),
        ("CROWE_ADMIN_WORKSPACE_ID", workspace_id),
    ] if not v]
    if missing:
        raise SystemExit(
            "Remote mode requires env vars: " + ", ".join(missing)
        )

    url = f"{base_url}/api/workspaces/{workspace_id}/keys"
    r = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json",
        },
        json={"label": label, "scopes": scopes},
        timeout=30.0,
    )
    if r.status_code >= 300:
        raise SystemExit(f"Remote issue failed [{r.status_code}]: {r.text}")
    data = r.json()
    return {
        "mode": "remote",
        "control_plane": base_url,
        "workspace_id": workspace_id,
        "key_id": data["id"],
        "key_prefix": data["key_prefix"],
        "key": data["key"],
        "label": data.get("label", label),
        "scopes": data.get("scopes", scopes),
    }


# ─── CLI ────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description="Issue a Crowe Logic tester key.")
    p.add_argument("--remote", action="store_true",
                   help="Mint against live Control Plane instead of local SQLite.")
    p.add_argument("--label", default="tester-dev",
                   help="Human label for the key (default: tester-dev).")
    p.add_argument("--plan", default="lab", choices=sorted(VALID_PLANS),
                   help="Workspace plan tier (local mode only). Default: lab.")
    p.add_argument("--email", default="tester@crowelogic.com",
                   help="Tester email (local mode only).")
    p.add_argument("--scopes", default=",".join(DEFAULT_SCOPES),
                   help="Comma-separated scopes. Default: chat,vision,agents.")
    p.add_argument("--db", default=str(DEFAULT_DB),
                   help="SQLite path for local mode.")
    p.add_argument("--json", action="store_true",
                   help="Emit machine-readable JSON instead of pretty output.")
    args = p.parse_args()

    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]

    if args.remote:
        result = _issue_remote(args.label, scopes)
    else:
        result = _issue_local(Path(args.db), args.email, args.label,
                              args.plan, scopes)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print()
    print("  ◆ Crowe Logic — tester key issued")
    print("  ─────────────────────────────────────────────")
    print(f"  mode         : {result['mode']}")
    if result["mode"] == "local":
        print(f"  sqlite db    : {result['db']}")
    else:
        print(f"  control plane: {result['control_plane']}")
    print(f"  workspace_id : {result['workspace_id']}")
    print(f"  key_id       : {result['key_id']}")
    print(f"  key_prefix   : {result['key_prefix']}")
    print(f"  scopes       : {', '.join(result['scopes'])}")
    if result["mode"] == "local":
        print(f"  plan         : {result['plan']}")
    print()
    print("  ⚠  RAW KEY (shown once — copy it now):")
    print(f"     {result['key']}")
    print()
    print("  The tester should set:")
    print(f"     export CROWE_LOGIC_KEY={result['key']}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
