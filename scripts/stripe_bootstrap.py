#!/usr/bin/env python3
"""
One-shot Stripe setup for Crowe Logic Foundry launch.

Creates (or finds) the three paying products (Developer, Studio, Lab),
their monthly + annual recurring prices, and a metered token overage
price. Writes the resulting IDs to stdout as an env-var block that can
be pasted into Railway's variable editor, and optionally into a local
`.env.railway.out` file.

Enterprise is handled by sales and therefore has no Stripe product here.

Usage:
    export STRIPE_SECRET_KEY=sk_live_...         # or sk_test_... first
    python scripts/stripe_bootstrap.py
    python scripts/stripe_bootstrap.py --out .env.railway.out

Re-running is safe; products and prices are matched by a metadata tag
(`crowe_plan_id`) and reused rather than duplicated.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional


# Source of truth for launch pricing. Must match migrations/004_pricing.sql.
PLANS = [
    {
        "id": "developer",
        "name": "Crowe Logic Code: Developer",
        "description": "Solo developer tier. Crowe Logic extension for VS Code + 500K tokens on Developer-tier models.",
        "monthly_cents": 4900,
        "annual_cents": 47000,
    },
    {
        "id": "studio",
        "name": "Crowe Logic Code: Studio",
        "description": "Professional tier. Studio-tier models (DeepSeek R1, Mistral Large 3, Kimi K2.5), 5M tokens, 100 hosted IDE hours.",
        "monthly_cents": 12900,
        "annual_cents": 124000,
    },
    {
        "id": "lab",
        "name": "Crowe Logic Code: Lab",
        "description": "Team tier. Lab-tier models (Claude Opus 4.6, GPT-5.4), 50M tokens, 500 hosted IDE hours, private datasets, SSO.",
        "monthly_cents": 39900,
        "annual_cents": 383000,
    },
]

# Metered overage. 1 unit = 1000 tokens. Integer cents must be >= 1, so
# $0.02 per 1K is the minimum billable. The migration file commits to 2.
OVERAGE_CENTS_PER_1K = 2
OVERAGE_PRODUCT_NAME = "Crowe Logic Code: Token Overage"
OVERAGE_PRODUCT_DESC = "Metered billing for tokens beyond the included plan budget, billed monthly in arrears per 1K tokens."


def _find_product_by_tag(stripe, tag: str):
    """Stripe does not let us filter on metadata directly in the list API,
    so we paginate and match in Python. Fine for <100 products."""
    for p in stripe.Product.list(limit=100, active=True).auto_paging_iter():
        if p.metadata.get("crowe_plan_id") == tag:
            return p
    return None


def _find_price(stripe, product_id: str, interval: str, amount: int):
    for price in stripe.Price.list(product=product_id, limit=100, active=True).auto_paging_iter():
        if (price.recurring or {}).get("interval") == interval and price.unit_amount == amount:
            return price
    return None


def _ensure_product(stripe, tag: str, name: str, description: str):
    existing = _find_product_by_tag(stripe, tag)
    if existing:
        return existing
    return stripe.Product.create(
        name=name,
        description=description,
        metadata={"crowe_plan_id": tag},
    )


def _ensure_recurring_price(stripe, product_id: str, interval: str, amount: int, nickname: str):
    existing = _find_price(stripe, product_id, interval, amount)
    if existing:
        return existing
    return stripe.Price.create(
        product=product_id,
        currency="usd",
        unit_amount=amount,
        nickname=nickname,
        recurring={"interval": interval},
    )


def _ensure_metered_price(stripe, product_id: str, amount: int):
    for price in stripe.Price.list(product=product_id, limit=100, active=True).auto_paging_iter():
        recurring = price.recurring or {}
        if recurring.get("usage_type") == "metered" and price.unit_amount == amount:
            return price
    return stripe.Price.create(
        product=product_id,
        currency="usd",
        unit_amount=amount,
        nickname="Token overage per 1K",
        recurring={"interval": "month", "usage_type": "metered", "aggregate_usage": "sum"},
    )


def run(out_path: Optional[str]) -> int:
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        print("ERROR: STRIPE_SECRET_KEY not set. Export a sk_live_ or sk_test_ key.", file=sys.stderr)
        return 2

    try:
        import stripe
    except ImportError:
        print("ERROR: stripe SDK missing. pip install stripe", file=sys.stderr)
        return 2

    stripe.api_key = key

    env_lines: list[str] = []

    for plan in PLANS:
        tag = plan["id"]
        product = _ensure_product(stripe, tag, plan["name"], plan["description"])
        monthly = _ensure_recurring_price(stripe, product.id, "month", plan["monthly_cents"], f"{plan['name']} Monthly")
        annual = _ensure_recurring_price(stripe, product.id, "year", plan["annual_cents"], f"{plan['name']} Annual")
        env_lines.append(f"STRIPE_PRICE_{tag.upper()}={monthly.id}")
        env_lines.append(f"STRIPE_PRICE_{tag.upper()}_ANNUAL={annual.id}")
        print(f"  {plan['name']}: product={product.id} monthly={monthly.id} annual={annual.id}")

    overage_product = _ensure_product(stripe, "token_overage", OVERAGE_PRODUCT_NAME, OVERAGE_PRODUCT_DESC)
    overage_price = _ensure_metered_price(stripe, overage_product.id, OVERAGE_CENTS_PER_1K)
    env_lines.append(f"STRIPE_PRICE_USAGE_TOKENS={overage_price.id}")
    print(f"  Token overage: product={overage_product.id} price={overage_price.id}")

    print("\n--- Railway env vars ---")
    block = "\n".join(env_lines)
    print(block)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(block + "\n")
        print(f"\nWritten to {out_path}")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="Write the env block to this file as well")
    args = parser.parse_args()
    sys.exit(run(args.out))
