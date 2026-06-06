"""Anonymous device registration for the free tier.

Stateless HMAC tokens (see tokens.make_device_token) + a per-IP in-process
rate limit. NOTE: the limiter is per-replica; at >1 ACA replica the effective
ceiling is N x _REGISTER_MAX_PER_IP. Acceptable for launch - the daily turn
cap is the real spend bound.

Behind Azure Container Apps ingress, request.client.host is the LB IP, so
_client_ip() prefers the first hop of X-Forwarded-For. XFF is caller-
controlled for direct hits, so this limiter is best-effort; the per-device
daily turn cap is the real spend bound.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request

from .plans import ANON_DAILY_TURN_CAP
from .tokens import make_device_token

router = APIRouter(prefix="/v1/anonymous", tags=["anonymous"])

_REGISTER_WINDOW = 3600.0  # seconds
_REGISTER_MAX_PER_IP = 5
_REGISTER_LOG_MAX = 10_000
_register_log: dict[str, list[float]] = {}

FREE_MODEL = "crowelm-mycelium"


def _client_ip(request: Request) -> str:
    """First X-Forwarded-For hop when present (ACA ingress rewrites client to the LB).

    XFF is caller-controlled for direct hits, so this limiter is best-effort;
    the per-device daily turn cap is the real spend bound.
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/register")
async def register_device(request: Request) -> dict:
    """Mint an anonymous device token. No PII; rate limited per source IP."""
    ip = _client_ip(request)
    now = time.time()

    if len(_register_log) > _REGISTER_LOG_MAX:
        cutoff = now - _REGISTER_WINDOW
        for stale_ip in [k for k, v in _register_log.items() if not v or v[-1] < cutoff]:
            del _register_log[stale_ip]

    hits = [t for t in _register_log.get(ip, []) if now - t < _REGISTER_WINDOW]
    if len(hits) >= _REGISTER_MAX_PER_IP:
        raise HTTPException(
            status_code=429,
            detail="Too many device registrations from this address; try again later.",
        )
    hits.append(now)
    _register_log[ip] = hits

    device_id, token = make_device_token()
    return {
        "device_id": device_id,
        "token": token,
        "free_model": FREE_MODEL,
        "daily_turn_cap": ANON_DAILY_TURN_CAP,
    }
