"""Thin client that routes a CLI turn through the foundry gateway with a Crowe ID bearer.

The only place the CLI builds an HTTP call to the gateway. Token handling is
delegated to ``cli.auth``; this module just attaches the bearer and handles the
401-refresh-retry and 403-plan-denied cases. Provider keys never live here — the
gateway holds them server-side, which is what makes the local fallback cascade
impossible for a signed-in user.
"""

from __future__ import annotations

import os

import httpx

from cli import auth

GATEWAY_BASE = os.environ.get(
    "CROWE_LOGIC_GATEWAY_URL", "https://api.crowelogic.com"
).rstrip("/")
_TIMEOUT = 120


class PlanDenied(Exception):
    """Raised when the signed-in user's plan does not allow the requested model."""


def _token() -> str:
    return auth.current_access_token()


def chat(
    model: str,
    messages: list[dict],
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> dict:
    """POST one turn to /api/gateway/chat and return the GatewayResponse JSON.

    On 401 we force a token refresh once and retry; a second 401 means the session
    is unrecoverable (NotLoggedIn). A 403 is a plan/tier denial (PlanDenied) — never
    a reason to fall back to local keys.
    """
    url = f"{GATEWAY_BASE}/api/gateway/chat"
    body: dict = {"model": model, "messages": messages}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if temperature is not None:
        body["temperature"] = temperature

    for attempt in range(2):
        resp = httpx.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {_token()}"},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 401 and attempt == 0:
            # Force the next _token() call to refresh by expiring the stored token.
            creds = auth.load_creds()  # raises NotLoggedIn if the store is gone
            creds["expires_at"] = 0
            auth.save_creds(creds)
            continue
        if resp.status_code == 403:
            raise PlanDenied(
                resp.json().get("detail", "plan does not allow this model")
            )
        if resp.status_code == 401:
            raise auth.NotLoggedIn("Session expired. Run `crowe-logic login`.")
        resp.raise_for_status()
        return resp.json()

    raise auth.NotLoggedIn("Authentication failed. Run `crowe-logic login`.")
