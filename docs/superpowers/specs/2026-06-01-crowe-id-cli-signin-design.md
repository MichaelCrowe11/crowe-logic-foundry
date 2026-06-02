# Crowe ID CLI Sign-In (Phases B+C) Design

**Status:** Approved 2026-06-01. Phase A (Crowe ID cloud deploy) is live; see `~/Projects/crowe-id/docs/superpowers/plans/2026-06-01-crowe-id-cloud-deploy.md`.

**Goal:** Let a user run `crowe-logic login`, sign in through the browser against Crowe ID, and have the CLI route all model execution through the foundry gateway, so the client never holds provider API keys and the "Model failed, switching to..." fallback cascade becomes structurally impossible.

**Origin:** The pasted CroweLM v0.3.0 session cascaded through tier after tier because `cli/crowe_logic.py` reads each provider's key from `os.environ` and fails over locally when a key is missing. Moving key custody and execution to the already-metered gateway, gated by a real sign-in, removes the root cause.

## Background (verified in code)

- Crowe ID is live at `https://id.crowelogic.com/realms/crowe` (TLS, OIDC discovery, RS256 JWKS). A public PKCE client `crowe-cli` (S256, loopback redirects 8765/9275) is registered. Owner `michael@crowelogic.com` has `crowe_tier=enterprise`. A real browser PKCE login mints an access token (300s) carrying `crowe_tier` plus a refresh token.
- `control_plane/gateway.py` is already a metered model proxy: `MODEL_PLAN_ACCESS` maps model -> minimum plan (Personal/Pro/Team/enterprise); `_resolve_api_key` (a FastAPI `Depends`) authenticates by API key and returns `{plan_id, workspace_id}`; `gateway_chat` enforces plan access; `_call_provider` already routes to real providers with server-side keys via `resolve_model_config`.
- `cli/crowe_logic.py` (Click app, `@main.command()` pattern) selects a model with the Synapse router, then executes provider calls client-side, walking `_advance_model` and printing the cascade at line ~1955 on failure.
- Config convention: `~/.config/crowe-logic/` (already holds `knowledge.db`, `models.extra.json`).

## Architecture: client selects, server executes

- The Synapse router stays client-side: it classifies the prompt and chooses the requested model. That is local, fast, and unchanged.
- All model execution goes through the gateway. A signed-in CLI POSTs the turn plus chosen model plus a Crowe ID bearer token to the gateway. The gateway verifies the token, enforces `crowe_tier`->plan access, calls the real provider with a server-side key, owns fallback, and meters usage.
- The client-side cascade is bypassed when authenticated. It is retained only behind an explicit `CROWE_LOGIC_LOCAL=1` escape hatch for offline/dev use with local keys. A signed-in client never reads a provider key, so it cannot cascade on a missing one.

## Component B: gateway accepts Crowe ID tokens

New unit `control_plane/oidc.py` (small, testable in isolation):
- `fetch_jwks(issuer)`: GET `{issuer}/protocol/openid-connect/certs`, cache keys by `kid` with a TTL (default 3600s) and a forced refresh on unknown `kid` (handles key rotation).
- `verify_token(token, issuer, audience=None) -> dict`: validate RS256 signature against the JWKS key matching the token's `kid`, check `exp`/`iss`; return claims. Raises on any failure.
- `tier_to_plan(crowe_tier: str) -> str`: map Crowe ID tier to the gateway plan id. Concrete table: `free->personal`, `pro->pro`, `studio->team`, `enterprise->enterprise`, unknown/missing->`personal` (least privilege). The exact plan-id strings are validated against `control_plane`'s `canonical_plan_id` in the plan; if the gateway has no distinct `enterprise` plan, `enterprise` maps to the highest available plan (`team`).

Integration in `control_plane/gateway.py`:
- Generalize `_resolve_api_key` into `_resolve_principal`: if `Authorization: Bearer <jwt>` is a JWT (three dot-separated segments) that is NOT a supported API key, verify it via `oidc.verify_token` against the configured `CROWE_ID_ISSUER`, and build `{"plan_id": tier_to_plan(claims.get("crowe_tier")), "workspace_id": claims["sub"], "user_id": claims["sub"], "principal": "crowe-id", "subject": claims.get("preferred_username")}`. Otherwise fall back to the existing API-key path unchanged. API keys keep working (backward-compatible).
- The gateway routes that enforce access (`POST /api/gateway/chat`, `POST /api/gateway/chat/stream`) depend on `_resolve_principal` (renamed from `_resolve_api_key`), so both gain token auth at once.
- `gateway_chat` and the stream variant currently do workspace-scoped DB work (lookup `plans` by `plan_id`, read/insert `usage_events`). A Crowe ID principal has a Keycloak `sub`, not a `workspaces` row, so that block must be guarded: when `key_info.get("principal") == "crowe-id"`, perform the tier->plan access check (via `plan_rank`) but SKIP the budget lookup and usage insert. Metering for token principals is a deliberate follow-up (a Crowe ID `sub` -> workspace bridge), not in this spec.
- Config: `CROWE_ID_ISSUER` (default `https://id.crowelogic.com/realms/crowe`) and `CROWE_ID_AUDIENCE` (optional; if unset, audience is not enforced) read from env.

## Component C: CLI sign-in and gateway routing

New unit `cli/auth.py` (token lifecycle, no Click coupling):
- Store: `~/.config/crowe-logic/auth.json`, file mode `0600`. Fields: `access_token`, `refresh_token`, `expires_at` (epoch), `id_issuer`, `username`, `crowe_tier`.
- `login_pkce(issuer, client_id='crowe-cli') -> creds`: generate PKCE, open the authorize URL in the browser, capture the redirect. Capture strategy: prefer the macOS Safari address-bar reader (the proven `safari-capture-redirect.applescript`), fall back to a short-lived `localhost` HTTP listener on a registered port (8765, then 9275) for non-macOS/headless. Exchange code for tokens, persist.
- `current_access_token() -> str`: load store; if `expires_at` is within a 30s skew, refresh via the refresh-token grant and persist; return a valid access token. Raise `NotLoggedIn` if no store or refresh fails.
- `logout()`: delete the store file. `whoami() -> dict`: return username/tier/expiry without secrets.

New unit `cli/gateway_client.py` (thin HTTP):
- `chat(model, messages) -> GatewayResponse-shaped dict`: POST to `{GATEWAY_BASE}/api/gateway/chat` with `Authorization: Bearer <current_access_token()>` and body `{"model": model, "messages": messages}`. On `401`, refresh once and retry; on second `401`, raise `NotLoggedIn`. On `403`, raise `PlanDenied(model, detail)`. `GATEWAY_BASE` from env (`CROWE_LOGIC_GATEWAY_URL`, default the live control_plane URL). Streaming via `/chat/stream` is a follow-on once non-streaming is proven end to end.

New Click commands in `cli/crowe_logic.py`:
- `crowe-logic login` (calls `auth.login_pkce`, prints `Signed in as <user> (<tier>)`), `crowe-logic logout`, `crowe-logic whoami`.

Turn-loop change (`cli/crowe_logic.py`):
- At the point where a turn currently dispatches to a provider, branch: if signed in (a valid token is available) and `CROWE_LOGIC_LOCAL` is not set, send the Synapse-chosen model through `gateway_client.chat` and render the result; the local `_advance_model` cascade is not entered. If not signed in and `CROWE_LOGIC_LOCAL=1`, keep the existing local path. If not signed in and no local flag, print a one-line prompt to run `crowe-logic login`.

## Data flow (signed-in turn)

`prompt -> Synapse selects model -> gateway_client.chat(model, messages) POST /api/gateway/chat (Bearer) -> gateway _resolve_principal verifies token + tier->plan -> plan access check -> _call_provider (server-side key, server-side fallback) -> GatewayResponse -> CLI renders`.

## Error handling

- Expired access token: `gateway_client` refreshes once and retries; if refresh fails, raise `NotLoggedIn` and tell the user to `crowe-logic login`.
- `403` plan-denied: clear message naming the model and the tier required; no cascade.
- Not logged in (no `CROWE_LOGIC_LOCAL`): one-line instruction to `login`. With `CROWE_LOGIC_LOCAL=1`: legacy local-key path.
- Gateway unreachable: surface the connection error directly. Do not silently fall through to local provider keys (silent fallback is what hid the original problem).

## Testing

- B `oidc.py`: verify a real token from the live issuer (RS256, correct `kid`); reject a token with a tampered signature; reject an expired token (`exp` in the past); `tier_to_plan` mapping table; JWKS cache refresh on unknown `kid`.
- B gateway: a request with a valid bearer resolves to the mapped plan; an API key still resolves the old way (backward-compat); a model above the plan returns 403.
- C `auth.py`: store round-trip writes mode `0600`; `current_access_token` refreshes when `expires_at` is near; `NotLoggedIn` when store absent; PKCE redirect parsing (code, error, state mismatch) reuses the logic already unit-tested in `login_smoke.py`.
- C `gateway_client.py`: `401`->refresh->retry path (mocked); `403`->`PlanDenied`; happy path attaches the bearer.

## Non-goals (YAGNI)

- Device-code flow (the browser+PKCE path covers desktop; revisit for fully headless boxes later).
- Making the entitlements package the live system-of-record for tiers (the owner tier was set directly in Phase A; reconciliation is a separate effort).
- Migrating existing API-key consumers off keys (they keep working).
- Multi-account / account-switching in the CLI (single signed-in identity for now).

## Interfaces between units

- `oidc.verify_token` returns claims; `gateway._resolve_principal` consumes them. No other gateway code reads tokens.
- `auth.current_access_token` is the only function that touches the token store and refresh; `gateway_client` and the Click commands call it, never the file directly.
- `gateway_client.chat` is the only place the CLI turn loop calls the gateway; the turn loop does not build HTTP or handle tokens itself.
