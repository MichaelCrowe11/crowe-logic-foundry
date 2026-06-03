# Crowe ID CLI Sign-In (Phases B+C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `crowe-logic login` sign a user in against Crowe ID (browser PKCE) and route all model execution through `control_plane`'s `/api/gateway/chat`, so the client holds no provider keys and the fallback cascade is impossible.

**Architecture:** Gateway gains Crowe ID OIDC bearer auth alongside API keys (new `control_plane/oidc.py`, `_resolve_api_key` -> `_resolve_principal`). CLI gains `cli/auth.py` (token store + PKCE login + refresh) and `cli/gateway_client.py` (bearer-authed chat), plus `login`/`logout`/`whoami` commands and a turn-loop branch that calls the gateway when signed in.

**Tech Stack:** Python 3.11 (foundry `.venv`), FastAPI, PyJWT + cryptography (RS256/JWKS via `jwt.PyJWKClient`), httpx, Click, pytest. Spec: `docs/superpowers/specs/2026-06-01-crowe-id-cli-signin-design.md`.

**Run tests with:** `cd ~/Projects/crowe-logic-foundry && .venv/bin/pytest <path> -v` (the `.zshrc` PATH hook does not fire in non-interactive shells; call `.venv/bin/python`/`.venv/bin/pytest` directly).

**Live facts (verified):** issuer `https://id.crowelogic.com/realms/crowe`; public client `crowe-cli` (PKCE S256, redirects `http://localhost:8765/callback` + `9275`); owner `michael@crowelogic.com` `crowe_tier=enterprise`. Gateway router prefix `/api/gateway`; access dependency `_resolve_api_key`; plan ids `personal|pro|team|enterprise`; `plan_rank`/`canonical_plan_id` in `control_plane/plans.py`; `MODEL_PLAN_ACCESS` default `enterprise`.

---

## Phase B: Gateway accepts Crowe ID tokens

### Task B1: `control_plane/oidc.py` (token verification + tier mapping)

**Files:**
- Create: `control_plane/oidc.py`
- Test: `tests/test_oidc.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_oidc.py
import datetime, jwt, pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from control_plane import oidc

ISSUER = "https://id.crowelogic.com/realms/crowe"

@pytest.fixture
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key

def _token(key, claims, kid="testkid"):
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})

def _patch_signingkey(monkeypatch, key):
    class _SK:  # mimics jwt.PyJWK signing key
        def __init__(self, k): self.key = k
    monkeypatch.setattr(oidc, "_signing_key_for",
                        lambda token, issuer: _SK(key.public_key()))

def test_tier_to_plan_table():
    assert oidc.tier_to_plan("free") == "personal"
    assert oidc.tier_to_plan("pro") == "pro"
    assert oidc.tier_to_plan("studio") == "team"
    assert oidc.tier_to_plan("enterprise") == "enterprise"
    assert oidc.tier_to_plan(None) == "personal"
    assert oidc.tier_to_plan("bogus") == "personal"

def test_verify_valid_token(monkeypatch, keypair):
    _patch_signingkey(monkeypatch, keypair)
    now = datetime.datetime.now(datetime.timezone.utc)
    tok = _token(keypair, {"iss": ISSUER, "sub": "abc",
                           "exp": now + datetime.timedelta(minutes=5),
                           "crowe_tier": "enterprise",
                           "preferred_username": "michael@crowelogic.com"})
    claims = oidc.verify_token(tok, ISSUER)
    assert claims["crowe_tier"] == "enterprise"
    assert claims["preferred_username"] == "michael@crowelogic.com"

def test_verify_expired_token(monkeypatch, keypair):
    _patch_signingkey(monkeypatch, keypair)
    now = datetime.datetime.now(datetime.timezone.utc)
    tok = _token(keypair, {"iss": ISSUER, "sub": "abc",
                           "exp": now - datetime.timedelta(minutes=1)})
    with pytest.raises(jwt.ExpiredSignatureError):
        oidc.verify_token(tok, ISSUER)

def test_verify_wrong_issuer(monkeypatch, keypair):
    _patch_signingkey(monkeypatch, keypair)
    now = datetime.datetime.now(datetime.timezone.utc)
    tok = _token(keypair, {"iss": "https://evil.example/realms/x", "sub": "abc",
                           "exp": now + datetime.timedelta(minutes=5)})
    with pytest.raises(jwt.InvalidIssuerError):
        oidc.verify_token(tok, ISSUER)
```

- [ ] **Step 2: Run, verify it fails**

Run: `.venv/bin/pytest tests/test_oidc.py -v`
Expected: FAIL (`ModuleNotFoundError: control_plane.oidc`).

- [ ] **Step 3: Implement `control_plane/oidc.py`**

```python
"""Crowe ID (Keycloak) OIDC token verification for the gateway.

Verifies RS256 access tokens against the issuer's JWKS (fetched + cached by
PyJWT's PyJWKClient, which refreshes on an unknown kid) and maps Crowe ID tiers
to gateway plan ids. Pure-ish: the only side effect is the cached JWKS fetch.
"""
from __future__ import annotations

import os
import jwt
from jwt import PyJWKClient

CROWE_ID_ISSUER = os.environ.get(
    "CROWE_ID_ISSUER", "https://id.crowelogic.com/realms/crowe"
)
CROWE_ID_AUDIENCE = os.environ.get("CROWE_ID_AUDIENCE") or None

_TIER_PLAN = {"free": "personal", "pro": "pro", "studio": "team", "enterprise": "enterprise"}

_jwks_clients: dict[str, PyJWKClient] = {}


def tier_to_plan(crowe_tier: str | None) -> str:
    """Map a Crowe ID tier to a gateway plan id. Unknown/missing -> least privilege."""
    return _TIER_PLAN.get((crowe_tier or "").lower(), "personal")


def _signing_key_for(token: str, issuer: str):
    """Return the PyJWK signing key matching the token's kid (cached, refresh on miss)."""
    uri = f"{issuer.rstrip('/')}/protocol/openid-connect/certs"
    client = _jwks_clients.get(uri)
    if client is None:
        client = PyJWKClient(uri)
        _jwks_clients[uri] = client
    return client.get_signing_key_from_jwt(token)


def verify_token(token: str, issuer: str = CROWE_ID_ISSUER, audience: str | None = CROWE_ID_AUDIENCE) -> dict:
    """Verify an RS256 access token and return its claims. Raises jwt.* on failure."""
    signing_key = _signing_key_for(token, issuer)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=issuer,
        audience=audience,
        options={"verify_aud": audience is not None},
    )


def looks_like_jwt(token: str) -> bool:
    """Cheap structural check: a JWT has three dot-separated segments."""
    return token.count(".") == 2 and all(token.split("."))
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest tests/test_oidc.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add control_plane/oidc.py tests/test_oidc.py
git commit -m "feat(gateway): Crowe ID OIDC token verification + tier->plan mapping"
```

### Task B2: `_resolve_principal` accepts tokens (gateway.py)

**Files:**
- Modify: `control_plane/gateway.py` (rename `_resolve_api_key` -> `_resolve_principal`; add token branch; update `Depends` refs)
- Test: `tests/test_gateway_principal.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_gateway_principal.py
import datetime, jwt, pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from control_plane import gateway, oidc

ISSUER = "https://id.crowelogic.com/realms/crowe"

@pytest.fixture
def key(): return rsa.generate_private_key(public_exponent=65537, key_size=2048)

def _bearer(key, tier):
    now = datetime.datetime.now(datetime.timezone.utc)
    return jwt.encode({"iss": ISSUER, "sub": "kc-sub-1",
                       "exp": now + datetime.timedelta(minutes=5),
                       "crowe_tier": tier, "preferred_username": "michael@crowelogic.com"},
                      key, algorithm="RS256", headers={"kid": "k"})

@pytest.mark.asyncio
async def test_token_principal_resolves_plan(monkeypatch, key):
    class _SK:
        def __init__(self, k): self.key = k
    monkeypatch.setattr(oidc, "_signing_key_for", lambda t, i: _SK(key.public_key()))
    tok = _bearer(key, "enterprise")
    info = await gateway._resolve_principal(authorization=f"Bearer {tok}", x_api_key=None, db=None)
    assert info["principal"] == "crowe-id"
    assert info["plan_id"] == "enterprise"
    assert info["workspace_id"] == "kc-sub-1"
    assert info["subject"] == "michael@crowelogic.com"
```

- [ ] **Step 2: Run, verify it fails**

Run: `.venv/bin/pytest tests/test_gateway_principal.py -v`
Expected: FAIL (`AttributeError: _resolve_principal` or name error).

- [ ] **Step 3: Implement the rename + token branch**

In `control_plane/gateway.py`, add the import near the top:
```python
from control_plane import oidc
```
Rename `async def _resolve_api_key(` to `async def _resolve_principal(` and insert the token branch at the very start of the body (before `raw_key = None`):

```python
    # ── Crowe ID bearer token path (alternative to API keys) ──
    if authorization and authorization.startswith("Bearer "):
        candidate = authorization[7:]
        if oidc.looks_like_jwt(candidate) and not is_supported_api_key(candidate):
            try:
                claims = oidc.verify_token(candidate)
            except Exception as exc:
                raise HTTPException(status_code=401, detail=f"Invalid Crowe ID token: {exc}")
            return {
                "plan_id": oidc.tier_to_plan(claims.get("crowe_tier")),
                "workspace_id": claims["sub"],
                "user_id": claims["sub"],
                "principal": "crowe-id",
                "subject": claims.get("preferred_username"),
            }
```

Then update the two `Depends(_resolve_api_key)` references (`gateway_chat` and the `/chat/stream` handler) to `Depends(_resolve_principal)`.

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest tests/test_gateway_principal.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add control_plane/gateway.py tests/test_gateway_principal.py
git commit -m "feat(gateway): accept Crowe ID bearer tokens via _resolve_principal"
```

### Task B3: guard workspace-scoped metering for token principals

**Files:**
- Modify: `control_plane/gateway.py` (`gateway_chat` and the `/chat/stream` handler)
- Test: extend `tests/test_gateway_principal.py`

- [ ] **Step 1: Write failing test (access check works, metering skipped for crowe-id)**

Append to `tests/test_gateway_principal.py`:
```python
def test_crowe_id_principal_skips_metering_branch():
    # The budget/usage block must be guarded so a crowe-id principal (no workspaces row)
    # does not hit the DB. We assert the guard helper exists and returns True for crowe-id.
    from control_plane import gateway
    assert gateway._is_metered({"principal": "api-key"}) is True
    assert gateway._is_metered({"principal": "crowe-id"}) is False
    assert gateway._is_metered({}) is True  # default: api-key path is metered
```

- [ ] **Step 2: Run, verify it fails**

Run: `.venv/bin/pytest tests/test_gateway_principal.py::test_crowe_id_principal_skips_metering_branch -v`
Expected: FAIL (`AttributeError: _is_metered`).

- [ ] **Step 3: Implement the guard**

In `control_plane/gateway.py`, add near the other helpers:
```python
def _is_metered(key_info: dict) -> bool:
    """Workspace-scoped budget/usage only applies to real workspace principals (API keys)."""
    return key_info.get("principal") != "crowe-id"
```
In `gateway_chat`, wrap the token-budget block and the usage-insert block so they only run when `_is_metered(key_info)`. Concretely, change:
```python
    if budget != -1:  # not unlimited
```
to be reached only inside `if _is_metered(key_info):`, and guard the `plan = await db.fetchrow("SELECT * FROM plans ...)` lookup the same way (move the whole `now/month_start/plan/budget/used_row` block under `if _is_metered(key_info):`). Likewise wrap the `if token_count > 0:` usage INSERT in `if _is_metered(key_info):`. The plan-access check (`required_plan` / `plan_rank`) stays unguarded so tier->plan is always enforced. Apply the same guards in the `/chat/stream` handler.

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest tests/test_gateway_principal.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add control_plane/gateway.py tests/test_gateway_principal.py
git commit -m "feat(gateway): skip workspace metering for Crowe ID token principals"
```

---

## Phase C: CLI sign-in + gateway routing

### Task C1: `cli/auth.py` token store + refresh

**Files:**
- Create: `cli/auth.py`
- Test: `tests/test_cli_auth.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cli_auth.py
import json, os, time, stat, pytest
from cli import auth

def test_store_roundtrip_mode_0600(tmp_path, monkeypatch):
    p = tmp_path / "auth.json"
    monkeypatch.setattr(auth, "STORE_PATH", str(p))
    auth.save_creds({"access_token": "a", "refresh_token": "r",
                     "expires_at": time.time() + 300, "username": "u", "crowe_tier": "enterprise"})
    assert (os.stat(p).st_mode & 0o777) == 0o600
    got = auth.load_creds()
    assert got["username"] == "u" and got["crowe_tier"] == "enterprise"

def test_not_logged_in_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "STORE_PATH", str(tmp_path / "nope.json"))
    with pytest.raises(auth.NotLoggedIn):
        auth.current_access_token()

def test_refreshes_when_near_expiry(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "STORE_PATH", str(tmp_path / "auth.json"))
    auth.save_creds({"access_token": "old", "refresh_token": "r",
                     "expires_at": time.time() + 5, "username": "u", "crowe_tier": "enterprise"})
    def fake_refresh(refresh_token):
        assert refresh_token == "r"
        return {"access_token": "new", "refresh_token": "r2", "expires_in": 300}
    monkeypatch.setattr(auth, "_refresh_grant", fake_refresh)
    assert auth.current_access_token() == "new"
    assert auth.load_creds()["refresh_token"] == "r2"

def test_whoami(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "STORE_PATH", str(tmp_path / "auth.json"))
    auth.save_creds({"access_token": "a", "refresh_token": "r",
                     "expires_at": time.time() + 300, "username": "u", "crowe_tier": "pro"})
    who = auth.whoami()
    assert who == {"username": "u", "crowe_tier": "pro"} or who["username"] == "u"
```

- [ ] **Step 2: Run, verify it fails**

Run: `.venv/bin/pytest tests/test_cli_auth.py -v`
Expected: FAIL (`ModuleNotFoundError: cli.auth`).

- [ ] **Step 3: Implement `cli/auth.py`**

```python
"""Crowe ID token lifecycle for the CLI: store, refresh, current access token.

The only module that touches the token store on disk. Browser PKCE login lives in
`login_pkce`; everything else (gateway client, Click commands) goes through
`current_access_token` / `whoami` / `logout`.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error

ISSUER = os.environ.get("CROWE_ID_ISSUER", "https://id.crowelogic.com/realms/crowe").rstrip("/")
CLIENT_ID = os.environ.get("CROWE_ID_CLIENT", "crowe-cli")
STORE_PATH = os.path.expanduser("~/.config/crowe-logic/auth.json")
_SKEW = 30  # refresh if the token expires within this many seconds


class NotLoggedIn(Exception):
    pass


def save_creds(creds: dict) -> None:
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
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID, "grant_type": "refresh_token", "refresh_token": refresh_token,
    }).encode()
    req = urllib.request.Request(
        f"{ISSUER}/protocol/openid-connect/token", data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise NotLoggedIn(f"Refresh failed ({e.code}). Run `crowe-logic login`.")


def current_access_token() -> str:
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
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest tests/test_cli_auth.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add cli/auth.py tests/test_cli_auth.py
git commit -m "feat(cli): Crowe ID token store + refresh (cli/auth.py)"
```

### Task C2: `login_pkce` (browser PKCE via Safari capture + localhost fallback)

**Files:**
- Modify: `cli/auth.py` (add `login_pkce` + PKCE helpers reused from the verified `~/Projects/crowe-id/infra/login_smoke.py`)
- Test: `tests/test_cli_auth.py` (parsing only; the browser leg is exercised manually in C6)

- [ ] **Step 1: Write failing test for code-exchange parsing**

Append to `tests/test_cli_auth.py`:
```python
def test_exchange_parses_tokens(monkeypatch):
    monkeypatch.setattr(auth, "_token_exchange",
        lambda code, verifier, redirect: {"access_token": "A", "refresh_token": "R",
                                          "expires_in": 300})
    monkeypatch.setattr(auth, "_decode_claims",
        lambda t: {"preferred_username": "u@x", "crowe_tier": "enterprise"})
    creds = auth._build_creds_from_exchange("code123", "verifier", "http://localhost:8765/callback")
    assert creds["username"] == "u@x" and creds["crowe_tier"] == "enterprise"
    assert creds["access_token"] == "A" and creds["refresh_token"] == "R"
    assert creds["expires_at"] > 0
```

- [ ] **Step 2: Run, verify it fails**

Run: `.venv/bin/pytest tests/test_cli_auth.py::test_exchange_parses_tokens -v`
Expected: FAIL (`AttributeError: _build_creds_from_exchange`).

- [ ] **Step 3: Implement `login_pkce` + helpers**

Add to `cli/auth.py` (imports `base64, hashlib, secrets, subprocess, time, http.server, threading, webbrowser` at top as needed):
```python
import base64, hashlib, secrets, subprocess, time, webbrowser
from pathlib import Path

REDIRECT_PORTS = [8765, 9275]
_SAFARI_CAPTURE = os.path.expanduser("~/Projects/crowe-id/infra/safari-capture-redirect.applescript")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _decode_claims(access_token: str) -> dict:
    p = access_token.split(".")[1]; p += "=" * (-len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p))


def _token_exchange(code: str, verifier: str, redirect: str) -> dict:
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID, "grant_type": "authorization_code",
        "code": code, "redirect_uri": redirect, "code_verifier": verifier}).encode()
    req = urllib.request.Request(f"{ISSUER}/protocol/openid-connect/token", data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _build_creds_from_exchange(code: str, verifier: str, redirect: str) -> dict:
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


def _capture_via_safari(auth_url: str, redirect: str, timeout: int = 160) -> str | None:
    if not Path(_SAFARI_CAPTURE).exists():
        return None
    r = subprocess.run(["osascript", _SAFARI_CAPTURE, auth_url, redirect, str(timeout)],
                       capture_output=True, text=True)
    out = (r.stdout or "").strip()
    return out[3:] if out.startswith("OK ") else None


def login_pkce(open_browser: bool = True) -> dict:
    """Run browser PKCE login, persist creds, return whoami dict."""
    verifier = _b64url(secrets.token_bytes(40))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(16)
    port = REDIRECT_PORTS[0]
    redirect = f"http://localhost:{port}/callback"
    params = {"client_id": CLIENT_ID, "response_type": "code", "scope": "openid",
              "redirect_uri": redirect, "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    auth_url = f"{ISSUER}/protocol/openid-connect/auth?" + urllib.parse.urlencode(params)

    redirect_url = _capture_via_safari(auth_url, redirect)
    if redirect_url is None:
        # fallback: open browser + run a one-shot localhost listener
        redirect_url = _capture_via_listener(auth_url, port, open_browser)

    q = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_url).query)
    if "error" in q:
        raise RuntimeError(f"Sign-in error: {q['error'][0]}")
    if q.get("state", [""])[0] != state:
        raise RuntimeError("State mismatch (CSRF); aborting.")
    code = q.get("code", [""])[0]
    if not code:
        raise RuntimeError("No authorization code returned.")
    creds = _build_creds_from_exchange(code, verifier, redirect)
    save_creds(creds)
    return {"username": creds["username"], "crowe_tier": creds["crowe_tier"]}


def _capture_via_listener(auth_url: str, port: int, open_browser: bool) -> str:
    import http.server, threading
    captured = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            captured["url"] = f"http://localhost:{port}{self.path}"
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab.")
        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    t = threading.Thread(target=srv.handle_request, daemon=True); t.start()
    if open_browser:
        webbrowser.open(auth_url)
    t.join(timeout=160); srv.server_close()
    if "url" not in captured:
        raise RuntimeError("Timed out waiting for the browser redirect.")
    return captured["url"]
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest tests/test_cli_auth.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add cli/auth.py tests/test_cli_auth.py
git commit -m "feat(cli): browser PKCE login (Safari capture + localhost fallback)"
```

### Task C3: `cli/gateway_client.py`

**Files:**
- Create: `cli/gateway_client.py`
- Test: `tests/test_gateway_client.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gateway_client.py
import pytest
from cli import gateway_client as gc

class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status; self._p = payload or {}
    def json(self): return self._p

def test_happy_path_attaches_bearer(monkeypatch):
    seen = {}
    def fake_post(url, json, headers, timeout):
        seen["auth"] = headers.get("Authorization"); seen["url"] = url
        return FakeResp(200, {"content": "hi", "model": json["model"], "usage": {}})
    monkeypatch.setattr(gc, "_token", lambda: "TKN")
    monkeypatch.setattr(gc.httpx, "post", fake_post)
    out = gc.chat("gpt-5.4", [{"role": "user", "content": "hey"}])
    assert out["content"] == "hi"
    assert seen["auth"] == "Bearer TKN"
    assert seen["url"].endswith("/api/gateway/chat")

def test_401_refreshes_once_then_succeeds(monkeypatch):
    calls = {"n": 0, "tok": ["OLD", "NEW"]}
    def fake_post(url, json, headers, timeout):
        calls["n"] += 1
        return FakeResp(401) if calls["n"] == 1 else FakeResp(200, {"content": "ok"})
    monkeypatch.setattr(gc, "_token", lambda: calls["tok"][min(calls["n"], 1)])
    monkeypatch.setattr(gc.httpx, "post", fake_post)
    out = gc.chat("m", [])
    assert out["content"] == "ok" and calls["n"] == 2

def test_403_raises_plan_denied(monkeypatch):
    monkeypatch.setattr(gc, "_token", lambda: "T")
    monkeypatch.setattr(gc.httpx, "post",
        lambda url, json, headers, timeout: FakeResp(403, {"detail": "requires team plan"}))
    with pytest.raises(gc.PlanDenied):
        gc.chat("m", [])
```

- [ ] **Step 2: Run, verify it fails**

Run: `.venv/bin/pytest tests/test_gateway_client.py -v`
Expected: FAIL (`ModuleNotFoundError: cli.gateway_client`).

- [ ] **Step 3: Implement `cli/gateway_client.py`**

```python
"""Thin client that routes a CLI turn through the foundry gateway with a Crowe ID bearer.

The only place the CLI builds an HTTP call to the gateway. Token handling is delegated
to cli.auth; this module just attaches the bearer and handles 401-refresh-retry / 403.
"""
from __future__ import annotations

import os
import httpx
from cli import auth

GATEWAY_BASE = os.environ.get(
    "CROWE_LOGIC_GATEWAY_URL", "https://chat.crowelogic.com"
).rstrip("/")


class PlanDenied(Exception):
    pass


def _token() -> str:
    return auth.current_access_token()


def chat(model: str, messages: list[dict], max_tokens: int | None = None,
         temperature: float | None = None) -> dict:
    """POST one turn to /api/gateway/chat. Returns the GatewayResponse JSON."""
    url = f"{GATEWAY_BASE}/api/gateway/chat"
    body = {"model": model, "messages": messages}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if temperature is not None:
        body["temperature"] = temperature

    for attempt in range(2):
        resp = httpx.post(url, json=body,
                          headers={"Authorization": f"Bearer {_token()}"}, timeout=120)
        if resp.status_code == 401 and attempt == 0:
            # force a refresh on the next _token() call by clearing expiry
            try:
                c = auth.load_creds(); c["expires_at"] = 0; auth.save_creds(c)
            except auth.NotLoggedIn:
                raise
            continue
        if resp.status_code == 403:
            raise PlanDenied(resp.json().get("detail", "plan does not allow this model"))
        if resp.status_code == 401:
            raise auth.NotLoggedIn("Session expired. Run `crowe-logic login`.")
        resp.raise_for_status()
        return resp.json()
    raise auth.NotLoggedIn("Authentication failed. Run `crowe-logic login`.")
```

> Note: confirm `CROWE_LOGIC_GATEWAY_URL` default. If `chat.crowelogic.com` does not front `control_plane`, set the env var to the live control_plane URL during C6 and update this default.

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest tests/test_gateway_client.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add cli/gateway_client.py tests/test_gateway_client.py
git commit -m "feat(cli): gateway_client routes a turn with Crowe ID bearer (401-refresh/403)"
```

### Task C4: `login` / `logout` / `whoami` Click commands

**Files:**
- Modify: `cli/crowe_logic.py` (add three `@main.command()` functions near the other commands, e.g. after the `route` command around line 2599)

- [ ] **Step 1: Add the commands**

```python
@main.command(name="login")
def login_cmd():
    """Sign in to Crowe ID in the browser."""
    from cli import auth
    try:
        who = auth.login_pkce()
    except Exception as exc:
        console.print(f"[red]Sign-in failed:[/red] {exc}")
        raise SystemExit(1)
    console.print(f"[#bfa669]Signed in as {who['username']} ({who['crowe_tier']}).[/#bfa669]")


@main.command(name="logout")
def logout_cmd():
    """Forget the stored Crowe ID session."""
    from cli import auth
    auth.logout()
    console.print("Signed out.")


@main.command(name="whoami")
def whoami_cmd():
    """Show the signed-in Crowe ID identity."""
    from cli import auth
    try:
        who = auth.whoami()
    except auth.NotLoggedIn:
        console.print("Not signed in. Run `crowe-logic login`.")
        raise SystemExit(1)
    console.print(f"{who['username']} (tier: {who['crowe_tier']})")
```

- [ ] **Step 2: Verify the commands register**

Run: `.venv/bin/python -m cli.crowe_logic --help` (or the installed `crowe-logic --help`)
Expected: `login`, `logout`, `whoami` appear in the command list.

- [ ] **Step 3: Smoke logout/whoami (no network)**

Run: `.venv/bin/python -m cli.crowe_logic whoami`
Expected: "Not signed in..." (exit 1) when no store exists.

- [ ] **Step 4: Commit**

```bash
git add cli/crowe_logic.py
git commit -m "feat(cli): login/logout/whoami commands"
```

### Task C5: route the turn through the gateway when signed in

**Files:**
- Modify: `cli/crowe_logic.py` (the turn dispatch path, near the provider-call site around line 1900-1960)

- [ ] **Step 1: Add a gateway-routing branch at the turn dispatch**

Locate where a turn currently calls the provider / enters `_advance_model`. Before the local provider path, insert:

```python
    # ── Signed-in users route execution through the gateway (no local keys, no cascade) ──
    use_local = os.environ.get("CROWE_LOGIC_LOCAL", "").strip().lower() in ("1", "true", "yes")
    if not use_local:
        from cli import auth, gateway_client
        try:
            _ = auth.load_creds()  # raises NotLoggedIn if absent
            resp = gateway_client.chat(model=model_cfg["name"], messages=turn_messages)
            _render_answer(resp["content"])  # use the existing answer renderer
            session_state["api_status"] = "ok"
            continue  # turn complete; do not enter the local cascade
        except auth.NotLoggedIn:
            console.print("[dim]Not signed in. Run `crowe-logic login`, or set "
                          "CROWE_LOGIC_LOCAL=1 to use local provider keys.[/dim]")
            break
        except gateway_client.PlanDenied as exc:
            _render_error(str(exc), "Plan does not allow this model")
            break
```

Adjust `model_cfg["name"]`, `turn_messages`, `_render_answer`, and the loop control (`continue`/`break`) to match the actual variable names at the dispatch site (read the surrounding ~40 lines first). The intent: signed-in -> one gateway call, render, end the turn; never enter `_advance_model`.

- [ ] **Step 2: Verify the local path still works behind the flag**

Run: `CROWE_LOGIC_LOCAL=1 .venv/bin/python -m cli.crowe_logic route "ping" ` (or the existing non-interactive entry)
Expected: behaves exactly as today (local provider path), proving the branch is correctly gated.

- [ ] **Step 3: Commit**

```bash
git add cli/crowe_logic.py
git commit -m "feat(cli): route turns through the gateway when signed in (CROWE_LOGIC_LOCAL escape hatch)"
```

### Task C6: end-to-end verification (local gateway + live issuer)

- [ ] **Step 1: Run control_plane locally pointed at the live issuer**

```bash
cd ~/Projects/crowe-logic-foundry
set -a; . .env; set +a
export CROWE_ID_ISSUER=https://id.crowelogic.com/realms/crowe
.venv/bin/uvicorn control_plane.web:app --port 8099 &   # adjust app path if different
```
Expected: server starts; `curl -s localhost:8099/api/gateway/catalog` returns the model catalog.

- [ ] **Step 2: Sign in via the CLI**

Run: `.venv/bin/python -m cli.crowe_logic login`
Expected: Safari opens Crowe ID; after login, `Signed in as michael@crowelogic.com (enterprise).`

- [ ] **Step 3: Route a real turn through the local gateway**

```bash
CROWE_LOGIC_GATEWAY_URL=http://localhost:8099 .venv/bin/python -m cli.crowe_logic route "say hello in 5 words"
```
Expected: a model answer rendered, with NO "Model failed, switching to..." line. The gateway used a server-side provider key.

- [ ] **Step 4: Confirm tier enforcement**

Sign-in token is `enterprise`, so all tiers are allowed. To prove 403 wiring, temporarily set a lower tier on a throwaway test user or trust the unit test `test_403_raises_plan_denied`. Record the result.

- [ ] **Step 5: Stop the local server**

```bash
kill %1 2>/dev/null || pkill -f "uvicorn control_plane"
```

- [ ] **Step 6: Commit any fixes found during E2E**

```bash
git add -A && git commit -m "fix(cli-signin): adjustments from end-to-end verification"
```

---

## Self-Review

- **Spec coverage:** B1 oidc.py (verify + tier map) ✓; B2 `_resolve_principal` token branch ✓; B3 metering guard ✓; C1 token store+refresh ✓; C2 PKCE login (Safari + listener) ✓; C3 gateway_client (401/403) ✓; C4 commands ✓; C5 turn routing + `CROWE_LOGIC_LOCAL` ✓; C6 end-to-end ✓. Error-handling and testing sections map to B1-B3 / C1-C3 tests.
- **Placeholder scan:** no TBD/TODO; the one judgment call (matching variable names at the C5 dispatch site, and confirming the gateway base URL in C3/C6) is called out explicitly with how to resolve it, not left vague.
- **Type/name consistency:** `_resolve_principal`, `_is_metered`, `oidc.verify_token`/`tier_to_plan`/`looks_like_jwt`, `auth.save_creds`/`load_creds`/`current_access_token`/`login_pkce`/`logout`/`whoami`/`NotLoggedIn`/`_refresh_grant`/`_token_exchange`/`_build_creds_from_exchange`, `gateway_client.chat`/`PlanDenied`/`_token` are used consistently across tasks and tests.
- **Known follow-ups (not in scope):** deploy the updated control_plane to Azure; streaming via `/chat/stream`; metering for token principals (sub->workspace bridge); device-code flow.
