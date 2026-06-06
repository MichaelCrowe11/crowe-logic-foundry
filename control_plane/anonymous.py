"""Anonymous device registration for the free tier.

Stateless HMAC tokens (see tokens.make_device_token) + a per-IP in-process
rate limit. NOTE: the limiter is per-replica; at >1 ACA replica the effective
ceiling is N x _REGISTER_MAX_PER_IP. Acceptable for launch - the daily turn
cap is the real spend bound.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request

from .plans import ANON_DAILY_TURN_CAP
from .tokens import make_device_token

router = APIRouter(prefix="/v1/anonymous", tags=["anonymous"])

_REGISTER_WINDOW = 3600.0  # seconds
_REGISTER_MAX_PER_IP = 5
_register_log: dict[str, list[float]] = {}

FREE_MODEL = "crowelm-mycelium"


@router.post("/register")
async def register_device(request: Request) -> dict:
    """Mint an anonymous device token. No PII; rate limited per source IP."""
    ip = request.client.host if request.client else "unknown"
    now = time.time()
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
