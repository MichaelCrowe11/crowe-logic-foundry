#!/usr/bin/env python3
"""
Bootstrap an admin JWT against a running Crowe Logic Control Plane.

Registers the admin user if they don't exist (auto-creates personal org +
workspace), or logs in if they do. Prints the access token and the
workspace id, so `scripts/issue_tester_key.py --remote` can be pointed at
them via env vars:

    export CROWE_CONTROL_PLANE_URL=https://foundry.crowelogic.com
    export CROWE_ADMIN_TOKEN=$(python scripts/bootstrap_admin.py --quiet-token)
    export CROWE_ADMIN_WORKSPACE_ID=$(python scripts/bootstrap_admin.py --quiet-workspace)

For normal interactive use just run it with no flags — it will print a
ready-to-paste `export` block.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx


def _base_url() -> str:
    url = os.environ.get("CROWE_CONTROL_PLANE_URL", "http://127.0.0.1:8001")
    return url.rstrip("/")


def _login_or_register(base: str, email: str, password: str,
                       display_name: str) -> dict:
    # Try login first; fall back to register.
    r = httpx.post(f"{base}/api/auth/login",
                   json={"email": email, "password": password}, timeout=20)
    if r.status_code == 200:
        return r.json()
    if r.status_code == 401:
        r = httpx.post(f"{base}/api/auth/register", json={
            "email": email, "password": password,
            "display_name": display_name,
        }, timeout=20)
        if r.status_code >= 300:
            raise SystemExit(f"Register failed [{r.status_code}]: {r.text}")
        return r.json()
    raise SystemExit(f"Login failed [{r.status_code}]: {r.text}")


def _find_workspace(base: str, token: str) -> str:
    hdr = {"Authorization": f"Bearer {token}"}
    r = httpx.get(f"{base}/api/workspaces", headers=hdr, timeout=20)
    if r.status_code != 200:
        raise SystemExit(
            f"Could not list workspaces [{r.status_code}]: {r.text}"
        )
    items = r.json()
    if not items:
        raise SystemExit("No workspaces found for admin user.")
    return items[0]["id"]


def main() -> int:
    p = argparse.ArgumentParser(description="Bootstrap admin JWT.")
    p.add_argument("--email", default=os.environ.get(
        "CROWE_ADMIN_EMAIL", "admin@crowelogic.com"))
    p.add_argument("--password", default=os.environ.get(
        "CROWE_ADMIN_PASSWORD", "changeme-please-1234"))
    p.add_argument("--display-name", default="Crowe Admin")
    p.add_argument("--quiet-token", action="store_true",
                   help="Print only the access token.")
    p.add_argument("--quiet-workspace", action="store_true",
                   help="Print only the default workspace id.")
    p.add_argument("--save", action="store_true",
                   help="Append CROWE_ADMIN_* lines to .env.local.")
    args = p.parse_args()

    base = _base_url()
    creds = _login_or_register(base, args.email, args.password,
                               args.display_name)
    token = creds["access_token"]
    ws_id = _find_workspace(base, token)

    if args.quiet_token:
        print(token)
        return 0
    if args.quiet_workspace:
        print(ws_id)
        return 0

    print()
    print("  ◆ Crowe Logic — admin bootstrap complete")
    print("  ─────────────────────────────────────────────")
    print(f"  control plane  : {base}")
    print(f"  admin email    : {args.email}")
    print(f"  user_id        : {creds.get('user_id')}")
    print(f"  workspace_id   : {ws_id}")
    print()
    print("  Paste these into your shell:")
    print(f"     export CROWE_CONTROL_PLANE_URL={base}")
    print(f"     export CROWE_ADMIN_TOKEN={token}")
    print(f"     export CROWE_ADMIN_WORKSPACE_ID={ws_id}")
    print()

    if args.save:
        env_path = Path(__file__).resolve().parent.parent / ".env.local"
        lines = [
            f"CROWE_CONTROL_PLANE_URL={base}",
            f"CROWE_ADMIN_TOKEN={token}",
            f"CROWE_ADMIN_WORKSPACE_ID={ws_id}",
            "",
        ]
        existing = env_path.read_text() if env_path.exists() else ""
        keep = [ln for ln in existing.splitlines()
                if not ln.startswith(("CROWE_CONTROL_PLANE_URL=",
                                      "CROWE_ADMIN_TOKEN=",
                                      "CROWE_ADMIN_WORKSPACE_ID="))]
        env_path.write_text("\n".join(keep + lines))
        print(f"  ✓ saved to {env_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
