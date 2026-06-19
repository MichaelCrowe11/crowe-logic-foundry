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
# Streaming first-byte can lag on a cold gateway, so the read budget is generous
# while connect stays short.
_STREAM_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class PlanDenied(Exception):
    """Raised when the signed-in user's plan does not allow the requested model."""


class StreamingUnavailable(Exception):
    """No streaming gateway is configured, or the endpoint does not serve
    ``/v1/chat/completions``. The caller falls back to non-streaming ``chat()``."""


def _token() -> str:
    return auth.current_access_token()


def stream_base() -> str | None:
    """Base URL of a CSP gateway that streams ``/v1/chat/completions``, or None.

    Reuses dp's ``FOUNDRY_BASE_URL`` so a working DeepParallel setup lights up
    reasoning streaming in crowe-logic for free; ``CROWE_LOGIC_GATEWAY_STREAM_URL``
    overrides it. When neither is set, callers use the non-streaming proxy — so
    streaming is strictly opt-in and adds no behavior by default.
    """
    base = (
        (
            os.environ.get("CROWE_LOGIC_GATEWAY_STREAM_URL")
            or os.environ.get("FOUNDRY_BASE_URL")
            or ""
        )
        .strip()
        .rstrip("/")
    )
    return base or None


def streaming_available() -> bool:
    """True when a streaming gateway base is configured (see ``stream_base``)."""
    return stream_base() is not None


def _parse_sse_lines(lines):
    """OpenAI-style SSE lines -> (channel, text) chunks.

    ``channel`` is ``"thinking"`` for ``delta.reasoning_content`` and
    ``"content"`` for ``delta.content`` — so reasoning visibility stays a
    rendering decision. Mirrors ``deepparallel.backend.parse_sse_lines``.
    """
    for raw in lines:
        line = raw.strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            return
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        reasoning = delta.get("reasoning_content")
        if reasoning:
            yield ("thinking", reasoning)
        content = delta.get("content")
        if content:
            yield ("content", content)


def stream_chat(
    model: str,
    messages: list[dict],
    max_tokens: int | None = None,
    temperature: float | None = None,
):
    """Stream one turn from the CSP gateway's ``/v1/chat/completions``.

    Yields ``(channel, text)`` chunks (``"thinking"`` for reasoning, ``"content"``
    for the answer) so the caller can render reasoning live, matching dp. Raises
    ``StreamingUnavailable`` when no streaming base is configured or the endpoint
    has no streaming route (404) — the caller then falls back to ``chat()``. A
    401 refreshes the token once; 403 is ``PlanDenied``.
    """
    base = stream_base()
    if not base:
        raise StreamingUnavailable("no streaming gateway configured")
    url = f"{base}/v1/chat/completions"
    body: dict = {"model": model, "messages": messages, "stream": True}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if temperature is not None:
        body["temperature"] = temperature

    for attempt in range(2):
        with httpx.stream(
            "POST",
            url,
            json=body,
            headers={
                "Authorization": f"Bearer {_token()}",
                "content-type": "application/json",
            },
            timeout=_STREAM_TIMEOUT,
        ) as resp:
            if resp.status_code == 401 and attempt == 0:
                creds = auth.load_creds()  # raises NotLoggedIn if the store is gone
                creds["expires_at"] = 0
                auth.save_creds(creds)
                continue
            if resp.status_code == 403:
                resp.read()
                raise PlanDenied(
                    resp.json().get("detail", "plan does not allow this model")
                )
            if resp.status_code == 404:
                raise StreamingUnavailable(
                    "gateway does not serve /v1/chat/completions"
                )
            if resp.status_code == 401:
                raise auth.NotLoggedIn("Session expired. Run `crowe-logic login`.")
            resp.raise_for_status()
            yield from _parse_sse_lines(resp.iter_lines())
            return

    raise auth.NotLoggedIn("Authentication failed. Run `crowe-logic login`.")


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
