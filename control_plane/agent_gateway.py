"""Agent-native metered endpoint: POST /api/agent/v1/chat (x402).

discover -> authenticate (Crowe ID client_credentials) -> pay -> consume.
Dedicated path so the live human/API-key /chat is untouched. The "crowe-credit"
scheme is real prepaid balance (Stripe-funded) enforced by the wallet ledger;
the on-chain ("exact") scheme is real facilitator settlement. No mock data.
"""

from __future__ import annotations

import hmac
import json
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel

from . import agent_wallets, agents, oidc, settlement, x402
from .db import Database, get_db

RESOURCE = "/api/agent/v1/chat"
router = APIRouter()


class AgentChatRequest(BaseModel):
    model: str
    messages: list
    max_tokens: int | None = None
    temperature: float | None = None


def resolve_agent_principal(authorization: str | None) -> dict:
    """Verify a Crowe ID client_credentials bearer; return an agent principal."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Crowe ID agent token required")
    token = authorization[7:]
    if not oidc.looks_like_jwt(token):
        raise HTTPException(status_code=401, detail="not a Crowe ID token")
    try:
        claims = oidc.verify_token(token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail=f"Invalid Crowe ID token: {exc}")
    if not agents.is_agent_token(claims):
        raise HTTPException(
            status_code=403,
            detail="this endpoint requires an agent (client_credentials) token",
        )
    return agents.agent_principal(claims)


async def call_model(*, model, messages, max_tokens, temperature):
    """Forward to the existing provider call (lazy import avoids an import cycle).

    Returns the legacy ``(content, prompt_tokens, completion_tokens)`` tuple this
    x402 path expects. ``_call_provider`` now returns a ``_ProviderTurn`` (Phase 1
    tool-passthrough contract); the agent-payment surface does not pass tools, so
    we flatten the answer turn back to the tuple here.
    """
    from .gateway import _call_provider

    turn = await _call_provider(
        model=model, messages=messages, max_tokens=max_tokens, temperature=temperature
    )
    return (turn.content, turn.prompt_tokens, turn.completion_tokens)


def _payment_required(price: int) -> Response:
    return Response(
        content=json.dumps(x402.build_payment_required(RESOURCE, price)),
        status_code=402,
        media_type="application/json",
    )


@router.post(RESOURCE)
async def agent_chat(
    req: AgentChatRequest,
    authorization: str | None = Header(None),
    x_payment: str | None = Header(None, alias="X-PAYMENT"),
    db: Database = Depends(get_db),
):
    principal = resolve_agent_principal(authorization)
    client_id = principal["client_id"]
    price = x402.price_for_model(req.model)
    await agent_wallets.ensure_wallet(db, client_id)

    settled_tx = None
    if x_payment:
        try:
            payload = x402.parse_x_payment(x_payment)
        except ValueError:
            return _payment_required(price)
        fac = settlement.get_facilitator()
        if fac is None:
            return _payment_required(price)
        requirements = {
            "scheme": "exact",
            "network": os.environ.get("X402_NETWORK", "base"),
            "asset": os.environ.get("X402_ASSET", "USDC"),
            "maxAmountRequired": str(price),
            "resource": RESOURCE,
            "payTo": os.environ.get("X402_BASE_PAYTO"),
        }
        try:
            receipt = await fac.verify_and_settle(payload, requirements, price=price)
            await agent_wallets.credit(
                db,
                client_id,
                receipt.amount,
                receipt_id=receipt.id,
                scheme=receipt.scheme,
                resource=RESOURCE,
                tx_ref=receipt.tx_ref,
            )
            settled_tx = receipt.tx_ref
        except (settlement.PaymentError, agent_wallets.DuplicatePayment):
            return _payment_required(price)

    try:
        await agent_wallets.debit(db, client_id, price)
    except agent_wallets.InsufficientFunds:
        return _payment_required(price)

    # The call is already charged; if the upstream provider fails, refund so the
    # agent never pays for a completion it didn't receive, then surface the error.
    try:
        content, prompt_tokens, completion_tokens = await call_model(
            model=req.model,
            messages=req.messages,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
        )
    except Exception:
        await agent_wallets.refund(db, client_id, price)
        raise

    resp = Response(
        content=json.dumps(
            {
                "model": req.model,
                "content": content,
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }
        ),
        media_type="application/json",
    )
    resp.headers["X-PAYMENT-RESPONSE"] = json.dumps(
        {"charged": price, "client_id": client_id, "settlement": settled_tx}
    )
    return resp


class TopupRequest(BaseModel):
    client_id: str
    amount: int  # micro-USD to add to the agent's prepaid (crowe-credit) balance
    idempotency_key: str
    reason: str | None = "admin-topup"


@router.post("/api/agent/v1/wallet/topup")
async def wallet_topup(
    req: TopupRequest,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    db: Database = Depends(get_db),
):
    """Out-of-band crowe-credit top-up of an agent wallet (ops / Stripe-webhook seam).

    Fail-closed: disabled unless X402_ADMIN_TOKEN is set, and requires a matching
    X-Admin-Token (constant-time compare). Idempotent on ``idempotency_key`` so a
    retried top-up never double-credits. Lets an agent be funded before on-chain
    settlement is wired, so a real agent-paid completion can happen today."""
    admin = os.environ.get("X402_ADMIN_TOKEN")
    if not admin or not x_admin_token or not hmac.compare_digest(x_admin_token, admin):
        raise HTTPException(status_code=403, detail="admin token required")
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")
    await agent_wallets.ensure_wallet(db, req.client_id)
    try:
        balance = await agent_wallets.credit(
            db,
            req.client_id,
            req.amount,
            receipt_id=f"topup:{req.idempotency_key}",
            scheme="admin-topup",
            resource="/api/agent/v1/wallet/topup",
            tx_ref=req.reason,
        )
        applied = True
    except agent_wallets.DuplicatePayment:
        row = await agent_wallets.ensure_wallet(db, req.client_id)
        balance, applied = row["balance"], False
    return {"client_id": req.client_id, "balance": balance, "applied": applied}


@router.get("/.well-known/x402")
async def well_known_x402():
    """Machine-readable price catalog — agents crawl this to learn cost before paying.

    The on-chain ("exact") scheme is listed only when a real treasury address is
    configured (X402_BASE_PAYTO), matching the 402 envelope. No fake schemes.
    """
    chain_available = bool(os.environ.get("X402_BASE_PAYTO"))
    schemes = ["exact", "crowe-credit"] if chain_available else ["crowe-credit"]
    pricing = x402._pricing()
    return {
        "x402Version": 1,
        "unit": "micro-usd",
        "pricing": "per-model",
        "resources": [
            {
                "resource": res,
                "schemes": schemes,
                "pricing": "per-model",
                "default_price": pricing.get("default_micro_usd"),
            }
            for res in x402.PRICE_CATALOG
        ],
        "models": pricing.get("models", {}),
    }


@router.get("/.well-known/agent")
async def well_known_agent():
    """A2A-style agent card describing Crowe's agent-payable services."""
    return {
        "name": "Crowe Logic Foundry",
        "description": "Agent-native AI gateway: pay-per-call model + knowledge services.",
        "url": os.environ.get("CROWE_AGENT_URL", "https://chat.crowelogic.com"),
        "payments": {
            "protocol": "x402",
            "discovery": "/.well-known/x402",
            "priced": [
                {"resource": res, "price": price, "unit": "micro-usd"}
                for res, price in x402.PRICE_CATALOG.items()
            ],
        },
    }
