"""Crowe ID token lifecycle for the CLI: store, refresh, current access token.

The only module that touches the token store on disk. Browser PKCE login lives in
``login_pkce`` (added in Phase C2); everything else (gateway client, Click
commands) goes through ``current_access_token`` / ``whoami`` / ``logout``.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

ISSUER = os.environ.get(
    "CROWE_ID_ISSUER", "https://id.crowelogic.com/realms/crowe"
).rstrip("/")
CLIENT_ID = os.environ.get("CROWE_ID_CLIENT", "crowe-cli")
STORE_PATH = os.path.expanduser("~/.config/crowe-logic/auth.json")
_SKEW = 30  # refresh if the access token expires within this many seconds

# The realm registers exactly one loopback redirect (http://localhost:8765/*),
# so the listener and the authorize request must both use this port.
REDIRECT_PORT = int(os.environ.get("CROWE_ID_REDIRECT_PORT", "8765"))
_LOGIN_TIMEOUT = 180  # seconds to wait for the browser round-trip


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


# ── Browser PKCE login ────────────────────────────────────────────────────────


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _s256_challenge(verifier: str) -> str:
    """PKCE S256: base64url(SHA256(verifier)), unpadded (RFC 7636)."""
    return _b64url(hashlib.sha256(verifier.encode()).digest())


def _decode_claims(access_token: str) -> dict:
    """Decode a JWT payload WITHOUT verifying — local read of our own fresh token."""
    payload = access_token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def _token_exchange(code: str, verifier: str, redirect: str) -> dict:
    data = urllib.parse.urlencode(
        {
            "client_id": CLIENT_ID,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect,
            "code_verifier": verifier,
        }
    ).encode()
    req = urllib.request.Request(
        f"{ISSUER}/protocol/openid-connect/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _build_creds_from_exchange(code: str, verifier: str, redirect: str) -> dict:
    """Exchange an auth code for tokens and shape them into a creds record."""
    tok = _token_exchange(code, verifier, redirect)
    claims = _decode_claims(tok["access_token"])
    return {
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token", ""),
        "expires_at": time.time() + int(tok.get("expires_in", 300)),
        "username": claims.get("preferred_username"),
        "crowe_tier": claims.get("crowe_tier"),
        "id_issuer": ISSUER,
    }


def _capture_via_listener(auth_url: str, port: int, open_browser: bool) -> str | None:
    """Bind a one-shot loopback listener, open the browser, return the callback URL.

    This is the primary RFC 8252 capture path: any browser that lands on
    http://localhost:<port>/callback?... is captured directly. Returns None on
    timeout so the caller can fall back to the Safari address-bar reader.
    """
    captured: dict[str, str] = {}

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            captured["url"] = f"http://localhost:{port}{self.path}"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body>Signed in to Crowe ID. You can close this tab."
                b"</body></html>"
            )

        def log_message(self, *_a):  # silence the default stderr logging
            pass

    try:
        srv = http.server.HTTPServer(("127.0.0.1", port), _H)
    except OSError:
        return None  # port busy -> let the Safari fallback try
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    if open_browser:
        webbrowser.open(auth_url)
    t.join(timeout=_LOGIN_TIMEOUT)
    srv.server_close()
    return captured.get("url")


_SAFARI_READ = (
    'tell application "Safari" to repeat with w in windows\n'
    "  repeat with t in tabs of w\n"
    '    if URL of t starts with "{prefix}" then return URL of t\n'
    "  end repeat\n"
    "end repeat\n"
    'return ""'
)


def _capture_via_safari(redirect_prefix: str) -> str | None:
    """macOS fallback: read the callback URL straight from a Safari tab's address bar."""
    script = _SAFARI_READ.format(prefix=redirect_prefix)
    try:
        r = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=20
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    out = (r.stdout or "").strip()
    return out or None


def login_pkce(open_browser: bool = True) -> dict:
    """Run browser PKCE login, persist creds, and return a whoami dict.

    Loopback listener is the primary capture; the Safari address-bar reader is a
    fallback for when the listener port is busy or the redirect didn't reach it.
    """
    verifier = _b64url(secrets.token_bytes(40))
    challenge = _s256_challenge(verifier)
    state = secrets.token_urlsafe(16)
    redirect = f"http://localhost:{REDIRECT_PORT}/callback"
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope": "openid",
        "redirect_uri": redirect,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{ISSUER}/protocol/openid-connect/auth?" + urllib.parse.urlencode(
        params
    )

    redirect_url = _capture_via_listener(auth_url, REDIRECT_PORT, open_browser)
    if not redirect_url:
        redirect_url = _capture_via_safari(f"http://localhost:{REDIRECT_PORT}/callback")
    if not redirect_url:
        raise RuntimeError("Timed out waiting for the browser sign-in redirect.")

    q = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_url).query)
    if "error" in q:
        raise RuntimeError(f"Sign-in error: {q['error'][0]}")
    if q.get("state", [""])[0] != state:
        raise RuntimeError("State mismatch (possible CSRF); aborting.")
    code = q.get("code", [""])[0]
    if not code:
        raise RuntimeError("No authorization code returned.")

    creds = _build_creds_from_exchange(code, verifier, redirect)
    save_creds(creds)
    return {"username": creds["username"], "crowe_tier": creds["crowe_tier"]}
