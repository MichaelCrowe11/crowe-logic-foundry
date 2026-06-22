"""Thin client that routes a CLI turn through the foundry gateway with a Crowe ID bearer.

The only place the CLI builds an HTTP call to the gateway. Token handling is
delegated to ``cli.auth``; this module just attaches the bearer and handles the
401-refresh-retry and 403-plan-denied cases. Provider keys never live here — the
gateway holds them server-side, which is what makes the local fallback cascade
impossible for a signed-in user.
"""

from __future__ import annotations

import json
import os

import httpx

from cli import auth

GATEWAY_BASE = os.environ.get(
    "CROWE_LOGIC_GATEWAY_URL", "https://api.crowelogic.com"
).rstrip("/")
_TIMEOUT = 120

DEVICE_STORE = os.path.expanduser("~/.config/crowe-logic/device.json")


class PlanDenied(Exception):
    """Raised when the signed-in user's plan does not allow the requested model."""


class GatewayError(Exception):
    """Raised when the gateway returns an unhandled HTTP error."""

    def __init__(self, status_code: int, detail: object):
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


class FreeTierCapped(Exception):
    """Raised on a structured 402 from the gateway (anonymous daily cap)."""

    def __init__(self, detail: dict):
        self.detail = detail if isinstance(detail, dict) else {"message": str(detail)}
        super().__init__(self.detail.get("message", "free tier capped"))


def save_device(device: dict) -> None:
    os.makedirs(os.path.dirname(DEVICE_STORE), exist_ok=True)
    fd = os.open(DEVICE_STORE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(device, fh)


def load_device() -> dict | None:
    try:
        with open(DEVICE_STORE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def register_device() -> dict:
    """Mint + persist an anonymous device token. Returns the register payload."""
    resp = httpx.post(f"{GATEWAY_BASE}/v1/anonymous/register", timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    save_device(payload)
    return payload


def _token() -> str:
    return auth.current_access_token()


def chat(
    model: str,
    messages: list[dict],
    max_tokens: int | None = None,
    temperature: float | None = None,
    bearer: str | None = None,
) -> dict:
    """POST one turn to /api/gateway/chat and return the GatewayResponse JSON.

    On 401 we force a token refresh once and retry (signed-in path only); a second
    401 means the session is unrecoverable (NotLoggedIn). A 403 is a plan/tier denial
    (PlanDenied). A structured 402 means the anonymous daily cap has been hit
    (FreeTierCapped). Never fall back to local keys.

    Pass ``bearer`` to use an anonymous device token instead of a Crowe ID session;
    the 401-refresh logic is skipped for anonymous callers since there is no Crowe ID
    session to refresh.
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
            headers={"Authorization": f"Bearer {bearer or _token()}"},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 401 and attempt == 0 and bearer is None:
            # Force the next _token() call to refresh by expiring the stored token.
            creds = auth.load_creds()  # raises NotLoggedIn if the store is gone
            creds["expires_at"] = 0
            auth.save_creds(creds)
            continue
        if resp.status_code == 403:
            raise PlanDenied(
                resp.json().get("detail", "plan does not allow this model")
            )
        if resp.status_code == 402:
            raise FreeTierCapped(resp.json().get("detail", {}))
        if resp.status_code == 401:
            raise auth.NotLoggedIn("Session expired. Run `crowe-logic login`.")
        if resp.status_code >= 400:
            try:
                payload = resp.json()
                detail = payload.get("detail", payload)
            except ValueError:
                detail = resp.text
            raise GatewayError(resp.status_code, detail)
        return resp.json()

    raise auth.NotLoggedIn("Authentication failed. Run `crowe-logic login`.")
