"""Canonical Crowe Logic Code plan vocabulary.

The product used ``developer/studio/lab`` during early control-plane work.
Launch pricing uses ``personal/pro/team/enterprise``. Keep aliases here so
old rows and Stripe metadata continue to resolve while new code speaks the
customer-facing vocabulary.
"""

from __future__ import annotations

import os


LAUNCH_PLAN_IDS = ("byok", "personal", "pro", "team", "enterprise")
LEGACY_PLAN_ALIASES = {
    "developer": "personal",
    "studio": "pro",
    "lab": "team",
}
CANONICAL_LEGACY_ALIASES = {
    "personal": "developer",
    "pro": "studio",
    "team": "lab",
}
PLAN_RANK = {plan_id: idx for idx, plan_id in enumerate(LAUNCH_PLAN_IDS)}

# Anonymous free tier: deliberately NOT in LAUNCH_PLAN_IDS (Stripe surfaces
# iterate that tuple). Rank -1 sits below every paid plan.
ANON_PLAN_ID = "free-anonymous"
ANON_DAILY_TURN_CAP = 20  # server-side policy; tune without a client release

PLAN_DISPLAY_NAMES = {
    "free": "Free",
    "personal": "Personal",
    "pro": "Pro",
    "team": "Team",
    "enterprise": "Enterprise",
    "byok": "BYOK",
    "free-anonymous": "Free",
}

TIER_ALLOCATIONS = {
    "personal": 750,
    "pro": 3000,
    "team": 1500,
    "enterprise": 100_000,
    "byok": 0,
}

_STRIPE_PRICE_ENVS = {
    "personal": {
        "month": "STRIPE_PRICE_PERSONAL",
        "annual": "STRIPE_PRICE_PERSONAL_ANNUAL",
    },
    "pro": {
        "month": "STRIPE_PRICE_PRO",
        "annual": "STRIPE_PRICE_PRO_ANNUAL",
    },
    "team": {
        "month": "STRIPE_PRICE_TEAM",
        "annual": "STRIPE_PRICE_TEAM_ANNUAL",
    },
    "enterprise": {
        "month": "STRIPE_PRICE_ENTERPRISE",
        "annual": "STRIPE_PRICE_ENTERPRISE_ANNUAL",
    },
    "byok": {
        "month": "STRIPE_PRICE_BYOK",
        "annual": "STRIPE_PRICE_BYOK_ANNUAL",
    },
}

_LEGACY_STRIPE_PRICE_ENVS = {
    "developer": {
        "month": "STRIPE_PRICE_DEVELOPER",
        "annual": "STRIPE_PRICE_DEVELOPER_ANNUAL",
    },
    "studio": {
        "month": "STRIPE_PRICE_STUDIO",
        "annual": "STRIPE_PRICE_STUDIO_ANNUAL",
    },
    "lab": {
        "month": "STRIPE_PRICE_LAB",
        "annual": "STRIPE_PRICE_LAB_ANNUAL",
    },
}


def canonical_plan_id(plan_id: str | None, *, default: str = "personal") -> str:
    key = (plan_id or default).strip().lower()
    return LEGACY_PLAN_ALIASES.get(key, key)


def plan_rank(plan_id: str | None) -> int:
    canonical = canonical_plan_id(plan_id)
    if canonical == ANON_PLAN_ID:
        return -2
    if canonical == "free":
        return -1
    return PLAN_RANK.get(canonical, PLAN_RANK["personal"])


def display_plan_name(plan_id: str | None) -> str:
    canonical = canonical_plan_id(plan_id)
    return PLAN_DISPLAY_NAMES.get(canonical, canonical.title())


def is_self_serve_plan(plan_id: str | None) -> bool:
    return canonical_plan_id(plan_id) in {"personal", "pro", "team"}


def stripe_price_env(plan_id: str | None, *, interval: str = "month") -> str | None:
    key = (interval or "month").lower()
    if key in {"year", "yearly"}:
        key = "annual"
    elif key in {"month", "monthly"}:
        key = "month"

    raw = (plan_id or "").strip().lower()
    canonical = canonical_plan_id(raw)
    envs = _STRIPE_PRICE_ENVS.get(canonical)
    if envs and envs.get(key):
        return envs[key]

    legacy_envs = _LEGACY_STRIPE_PRICE_ENVS.get(raw)
    if legacy_envs:
        return legacy_envs.get(key)
    return None


def stripe_price_id(plan_id: str | None, *, interval: str = "month") -> str:
    env = stripe_price_env(plan_id, interval=interval)
    value = os.environ.get(env or "", "")
    if value:
        return value

    canonical = canonical_plan_id(plan_id)
    legacy = CANONICAL_LEGACY_ALIASES.get(canonical)
    if not legacy:
        return ""
    key = (interval or "month").lower()
    if key in {"year", "yearly"}:
        key = "annual"
    elif key in {"month", "monthly"}:
        key = "month"
    legacy_env = _LEGACY_STRIPE_PRICE_ENVS.get(legacy, {}).get(key)
    return os.environ.get(legacy_env or "", "")
