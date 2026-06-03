# Crowe ID CLI Sign-In (B+C) — Verification Record

Date: 2026-06-02. Branch: `feat/crowe-id-cli-signin`.

## Automated (green)

- **Unit tests — 21 new, all passing** (`.venv/bin/pytest`):
  - `tests/test_oidc.py` (5): tier->plan table, JWT shape, verify valid/expired/wrong-issuer.
  - `tests/test_gateway_principal.py` (4): token principal resolves plan, pro-tier maps, invalid token -> 401, `_is_metered` classification.
  - `tests/test_cli_auth.py` (7): 0600 store round-trip, NotLoggedIn, fresh-token no-refresh, near-expiry refresh, whoami/logout, code-exchange parse, S256 challenge.
  - `tests/test_gateway_client.py` (4): bearer attached, 401 refresh+retry, second 401 -> NotLoggedIn, 403 -> PlanDenied.
- **No regressions:** `test_control_plane.py` + `test_gateway_openai_compat.py` still green (43 passed together).
- **CLI surface:** `crowe-logic --help` lists `login` / `logout` / `whoami`; `crowe-logic whoami` with no session prints "Not signed in" and exits non-zero.

## Live HTTP (gateway boundary, green)

Booted `control_plane.preview:app` (SQLite mock DB) on a local port with
`CROWE_ID_ISSUER=https://id.crowelogic.com/realms/crowe`:

| Request | Result |
| --- | --- |
| `GET /api/gateway/catalog` (no auth) | 200 |
| `POST /api/gateway/chat` (no auth) | 401 `API key required` |
| `POST /api/gateway/chat` (malformed Crowe ID JWT) | 401 `Invalid Crowe ID token: ...` |

The third response is emitted by the new `_resolve_principal` token branch, proving
the gateway recognizes a Bearer JWT and routes it through `oidc.verify_token`.

## Remaining — user-gated live turn (NOT yet executed)

A real signed-in model turn needs (a) a browser PKCE login and (b) live Azure
provider creds, neither of which can be driven headlessly. To complete it:

```bash
cd ~/Projects/crowe-logic-foundry
# 1. boot a local gateway from this branch (SQLite mock; no Neon needed)
CROWE_ID_ISSUER=https://id.crowelogic.com/realms/crowe \
  .venv/bin/python -m uvicorn control_plane.preview:app --port 8787 &

# 2. sign in through the browser (loopback listener on :8765)
.venv/bin/python -m cli.crowe_logic login

# 3. route a real turn through the local gateway (no local provider keys)
CROWE_LOGIC_GATEWAY_URL=http://127.0.0.1:8787 \
  .venv/bin/python -m cli.crowe_logic run "Say hello in one short sentence."
```

Expected: step 3 returns a model answer with no "Model failed — switching to..."
cascade, because a signed-in client never reads a provider key.

## Known scope boundaries (deliberate, documented in commits)

- Gateway routing wired for the **`run()` single-prompt path** only; the interactive
  `chat()` loop has 8 streaming sites and is a follow-up.
- Streaming (`/chat/stream`) deferred per the plan — non-streamed `/api/gateway/chat`
  proven first.
- `gateway_client.GATEWAY_BASE` defaults to `https://chat.crowelogic.com`, which was
  unreachable at verification time; override with `CROWE_LOGIC_GATEWAY_URL` until the
  production gateway hostname is confirmed.
- Metering for Crowe ID token principals is intentionally skipped (`_is_metered`);
  a `sub`->workspace bridge is a future task.
