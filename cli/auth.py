"""Crowe ID token lifecycle for the CLI: store, refresh, current access token.

The only module that touches the token store on disk. Browser PKCE login lives in
``login_pkce`` (added in Phase C2); everything else (gateway client, Click
commands) goes through ``current_access_token`` / ``whoami`` / ``logout``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

ISSUER = os.environ.get(
    "CROWE_ID_ISSUER", "https://id.crowelogic.com/realms/crowe"
).rstrip("/")
CLIENT_ID = os.environ.get("CROWE_ID_CLIENT", "crowe-cli")
STORE_PATH = os.path.expanduser("~/.config/crowe-logic/auth.json")
_SKEW = 30  # refresh if the access token expires within this many seconds


class NotLoggedIn(Exception):
    """Raised when no usable Crowe ID session is on disk."""


def save_creds(creds: dict) -> None:
    """Persist creds to STORE_PATH with 0600 perms (owner read/write only)."""
    os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
    fd = os.open(STORE_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(creds, f)
    os.chmod(STORE_PATH, 0o600)


def load_creds() -> dict:
    try:
        with open(STORE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        raise NotLoggedIn("Not signed in. Run `crowe-logic login`.")


def logout() -> None:
    try:
        os.remove(STORE_PATH)
    except FileNotFoundError:
        pass


def whoami() -> dict:
    c = load_creds()
    return {"username": c.get("username"), "crowe_tier": c.get("crowe_tier")}


def _refresh_grant(refresh_token: str) -> dict:
    """Exchange a refresh token for a fresh access token via the OIDC token endpoint."""
    data = urllib.parse.urlencode(
        {
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    ).encode()
    req = urllib.request.Request(
        f"{ISSUER}/protocol/openid-connect/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise NotLoggedIn(f"Refresh failed ({e.code}). Run `crowe-logic login`.")


def current_access_token() -> str:
    """Return a valid access token, refreshing transparently when near expiry."""
    c = load_creds()
    if c.get("expires_at", 0) - _SKEW > time.time():
        return c["access_token"]
    tok = _refresh_grant(c["refresh_token"])
    c["access_token"] = tok["access_token"]
    if tok.get("refresh_token"):
        c["refresh_token"] = tok["refresh_token"]
    c["expires_at"] = time.time() + int(tok.get("expires_in", 300))
    save_creds(c)
    return c["access_token"]
