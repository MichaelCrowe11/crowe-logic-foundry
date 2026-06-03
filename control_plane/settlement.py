"""Real on-chain settlement for the x402 rail via an x402 facilitator.

NO MOCK DATA: the on-chain ("exact") scheme is settled by calling a real
facilitator over HTTP (verify then settle). If no facilitator is configured
(X402_FACILITATOR_URL unset) the rail honestly reports settlement unavailable
— it never fabricates a settlement. The "crowe-credit" scheme is NOT handled
here; it is real prepaid balance enforced by the wallet ledger in the endpoint.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx


class PaymentError(Exception):
    """On-chain settlement could not be completed (rejected, failed, or unconfigured)."""


@dataclass(frozen=True)
class Receipt:
    id: str  # payment nonce (idempotency key for the wallet credit)
    scheme: str
    amount: int
    tx_ref: str | None


class Facilitator:
    """Thin client for an x402 facilitator's /verify + /settle endpoints."""

    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None):
        self._base = base_url.rstrip("/")
        self._transport = transport

    async def verify_and_settle(
        self, payment: dict, requirements: dict, *, price: int
    ) -> Receipt:
        body = {
            "x402Version": 1,
            "paymentPayload": payment,
            "paymentRequirements": requirements,
        }
        async with httpx.AsyncClient(transport=self._transport, timeout=30) as client:
            vr = await client.post(f"{self._base}/verify", json=body)
            vr.raise_for_status()
            verified = vr.json()
            if not verified.get("isValid"):
                raise PaymentError(
                    f"facilitator rejected payment: {verified.get('invalidReason')}"
                )
            sr = await client.post(f"{self._base}/settle", json=body)
            sr.raise_for_status()
            settled = sr.json()
            if not settled.get("success"):
                raise PaymentError(f"settlement failed: {settled.get('errorReason')}")
        tx = settled.get("txHash") or settled.get("transaction")
        nonce = payment.get("nonce") or tx
        if not nonce:
            raise PaymentError("payment missing nonce/txHash")
        return Receipt(id=str(nonce), scheme="exact", amount=price, tx_ref=tx)


def get_facilitator() -> Facilitator | None:
    """Build a Facilitator from X402_FACILITATOR_URL, or None if unconfigured."""
    url = os.environ.get("X402_FACILITATOR_URL")
    return Facilitator(url) if url else None
