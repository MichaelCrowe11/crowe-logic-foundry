"""x402 protocol surface: price catalog, 402 envelope builder, X-PAYMENT parser.

Pure functions, no I/O. PRICE_CATALOG is the single source of truth shared by the
402 envelope and the discovery manifest, so quoted price and charged price can
never drift. Prices are integer micro-USD (1 unit = $0.000001).

No-mock-data policy: the on-chain ("exact") scheme is advertised ONLY when a real
Base treasury address is configured via X402_BASE_PAYTO. No placeholder address is
ever emitted. The "crowe-credit" scheme (settled against the agent's prepaid
balance) is always available.
"""

from __future__ import annotations

import base64
import functools
import json
import os
from pathlib import Path

# Single source of truth. Add an entry here to monetize a new endpoint.
PRICE_CATALOG: dict[str, int] = {
    "/api/agent/v1/chat": 50,  # micro-USD — legacy per-endpoint floor; per-MODEL pricing below
}

X402_NETWORK = os.environ.get("X402_NETWORK", "base")
X402_ASSET = os.environ.get("X402_ASSET", "USDC")

# Per-model price table (micro-USD/call), grounded in upstream_costs.json. Keeps the
# pay-per-call premise while ensuring frontier models are not sold at nano price.
_PRICING_PATH = Path(__file__).resolve().parent.parent / "config" / "x402_pricing.json"


@functools.lru_cache(maxsize=1)
def _pricing() -> dict:
    """Load + cache the per-model price table. Empty on read/parse failure."""
    try:
        return json.loads(_PRICING_PATH.read_text())
    except (OSError, ValueError):
        return {}


def price_for(resource: str) -> int:
    """Price in micro-USD for a metered resource. Raises KeyError if unpriced."""
    return PRICE_CATALOG[resource]


def price_for_model(model: str) -> int:
    """Per-model x402 price in micro-USD. Frontier models cost more than nano;
    unknown models fall back to the configured default (never the old flat 50)."""
    cfg = _pricing()
    models = cfg.get("models", {})
    if model in models:
        return int(models[model])
    return int(cfg.get("default_micro_usd", PRICE_CATALOG["/api/agent/v1/chat"]))


def build_payment_required(resource: str, price: int | None = None) -> dict:
    """Build the x402 `402` body. Advertises the on-chain scheme only when a real
    treasury address (X402_BASE_PAYTO) is configured."""
    amount = price if price is not None else price_for(resource)
    amount_s = str(amount)
    accepts: list[dict] = [
        {
            "scheme": "crowe-credit",
            "network": "crowe",
            "asset": "credit",
            "maxAmountRequired": amount_s,
            "payTo": "crowe-ledger",
            "resource": resource,
            "mimeType": "application/json",
        }
    ]
    pay_to = os.environ.get("X402_BASE_PAYTO")
    if pay_to:
        accepts.insert(
            0,
            {
                "scheme": "exact",
                "network": X402_NETWORK,
                "asset": X402_ASSET,
                "maxAmountRequired": amount_s,
                "payTo": pay_to,
                "resource": resource,
                "mimeType": "application/json",
            },
        )
    return {"x402Version": 1, "error": "payment required", "accepts": accepts}


def parse_x_payment(header: str) -> dict:
    """Decode a base64-encoded JSON X-PAYMENT header. Raises ValueError on garbage."""
    try:
        raw = base64.b64decode(header, validate=True)
        obj = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"malformed X-PAYMENT header: {exc}")
    if not isinstance(obj, dict):
        raise ValueError("X-PAYMENT must decode to a JSON object")
    return obj
