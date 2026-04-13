"""
Model Gateway — metered proxy for CroweLM model tiers.

Sits between the client and the existing provider layer. For every request:
1. Validates the API key or JWT
2. Checks entitlements (plan allows the requested model?)
3. Forwards to the correct provider
4. Records usage (tokens consumed)

This module is imported by the Control Plane API; it doesn't run standalone.
"""

import json
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel

from .db import Database, get_db

router = APIRouter(prefix="/api/gateway", tags=["gateway"])

# Model tier → plan minimum. Models not listed are enterprise-only.
MODEL_PLAN_ACCESS = {
    # Developer tier (BYOK models + Nano/Forge)
    "gpt-5.4-nano": "developer",
    "Llama-3-3-70B": "developer",
    "FW-GLM-5": "developer",
    # Studio tier
    "Kimi-K2.5": "studio",
    "DeepSeek-R1": "studio",
    "DeepSeek-V3-1": "studio",
    "Mistral-Large-3": "studio",
    "FW-MiniMax-M2.5": "studio",
    # Lab tier
    "claude-opus-4-6-2": "lab",
    "claude-opus-4-6": "lab",
    "gpt-5.4": "lab",
    # Enterprise only
    "gpt-5.4-pro": "enterprise",
    "grok-4-20-reasoning": "enterprise",
    "claude-opus-4-5": "enterprise",
}

PLAN_RANK = {"developer": 0, "studio": 1, "lab": 2, "enterprise": 3}


class GatewayRequest(BaseModel):
    model: str
    messages: list[dict]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    tools: Optional[list[dict]] = None


class GatewayResponse(BaseModel):
    id: str
    model: str
    content: str
    usage: dict
    latency_ms: int


async def _resolve_api_key(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: Database = Depends(get_db),
) -> dict:
    """Resolve an API key to workspace + user + plan."""
    import hashlib

    raw_key = None
    if x_api_key:
        raw_key = x_api_key
    elif authorization and authorization.startswith("Bearer cl_"):
        raw_key = authorization[7:]

    if not raw_key:
        raise HTTPException(status_code=401, detail="API key required")

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    row = await db.fetchrow(
        """SELECT ak.*, w.plan_id, w.status AS ws_status
           FROM api_keys ak
           JOIN workspaces w ON ak.workspace_id = w.id
           WHERE ak.key_hash = $1 AND NOT ak.revoked""",
        key_hash,
    )
    if not row:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")
    if row["ws_status"] != "active":
        raise HTTPException(status_code=403, detail="Workspace suspended")

    # Update last_used_at
    await db.execute(
        "UPDATE api_keys SET last_used_at = now() WHERE id = $1", row["id"]
    )
    return dict(row)


@router.post("/chat", response_model=GatewayResponse)
async def gateway_chat(
    req: GatewayRequest,
    key_info: dict = Depends(_resolve_api_key),
    db: Database = Depends(get_db),
):
    """Metered model gateway. Enforces plan-based model access and records usage."""
    model = req.model
    plan_id = key_info["plan_id"]
    workspace_id = key_info["workspace_id"]
    user_id = key_info["user_id"]

    # ── Plan-based model access check ──
    required_plan = MODEL_PLAN_ACCESS.get(model, "enterprise")
    if PLAN_RANK.get(plan_id, 0) < PLAN_RANK.get(required_plan, 3):
        raise HTTPException(
            status_code=403,
            detail=f"Model '{model}' requires {required_plan} plan or higher",
        )

    # ── Token budget check ──
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    plan = await db.fetchrow("SELECT * FROM plans WHERE id = $1", plan_id)
    budget = plan["token_budget_month"]

    if budget != -1:  # not unlimited
        used_row = await db.fetchrow(
            """SELECT COALESCE(SUM(quantity), 0) AS used
               FROM usage_events
               WHERE workspace_id = $1 AND event_type = 'tokens' AND recorded_at >= $2""",
            workspace_id, month_start,
        )
        if used_row and used_row["used"] >= budget:
            raise HTTPException(status_code=429, detail="Monthly token budget exhausted")

    # ── Forward to provider ──
    start = time.monotonic()

    # For now, return a structured stub that the real provider layer will replace.
    # The integration point is: import the provider from config/agent_config.py
    # and call stream_response() here. This keeps the gateway contract stable
    # while the provider wiring is connected.
    elapsed_ms = int((time.monotonic() - start) * 1000)

    # Placeholder usage — real implementation will read from provider response
    token_count = 0

    # ── Record usage ──
    if token_count > 0:
        await db.execute(
            """INSERT INTO usage_events (workspace_id, user_id, event_type, quantity, model)
               VALUES ($1, $2, 'tokens', $3, $4)""",
            workspace_id, user_id, token_count, model,
        )

    return GatewayResponse(
        id=f"gw_{int(time.time())}",
        model=model,
        content="[gateway stub — connect provider layer]",
        usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": token_count},
        latency_ms=elapsed_ms,
    )


@router.get("/models")
async def list_available_models(
    key_info: dict = Depends(_resolve_api_key),
):
    """Return models available for this API key's plan."""
    plan_id = key_info["plan_id"]
    plan_rank = PLAN_RANK.get(plan_id, 0)
    available = [
        {"model": m, "min_plan": p}
        for m, p in MODEL_PLAN_ACCESS.items()
        if PLAN_RANK.get(p, 3) <= plan_rank
    ]
    return {"plan": plan_id, "models": available}
