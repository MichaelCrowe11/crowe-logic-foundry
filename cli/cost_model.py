"""Crowe Logic cost model.

Two concerns, one module:

1. **Upstream cost estimation** (what Crowe Logic pays providers).
   Reads ``config/upstream_costs.json`` and maps
   ``(provider, backend_name, input_tokens, output_tokens, cache_stats)``
   to a USD cost. Used by the operator-side HUD to show real burn.

2. **Customer credit accounting** (what a customer spends from their
   monthly allocation). Reads ``config/customer_pricing.json`` and
   maps ``(model_cfg, tool_call_count, dual_mode, synthesis)`` to a
   credit cost. Used by the control plane at billing time and,
   optionally, surfaced to the operator in the HUD when the CLI is
   running against a Crowe Logic account.

Both layers intentionally decouple from each other: upstream rate
changes (Anthropic drops Opus pricing) don't force customer credit
reshuffles, and customer tier changes don't force upstream JSON edits.
The bridge is ``tier_for_model()``, which classifies a model config
into ``fast | balanced | flagship`` for credit lookup.

Files loaded lazily on first call. Callers safe to import freely at
module top, the JSON reads happen on demand.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---- Data loading --------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_UPSTREAM_PATH = _PROJECT_ROOT / "config" / "upstream_costs.json"
_CUSTOMER_PATH = _PROJECT_ROOT / "config" / "customer_pricing.json"

_lock = threading.Lock()
_upstream: dict | None = None
_customer: dict | None = None


def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def upstream_costs() -> dict:
    """Return the upstream provider cost table, loading once."""
    global _upstream
    with _lock:
        if _upstream is None:
            _upstream = _load_json(_UPSTREAM_PATH)
        return _upstream


def customer_pricing() -> dict:
    """Return the customer tier + credit table, loading once."""
    global _customer
    with _lock:
        if _customer is None:
            _customer = _load_json(_CUSTOMER_PATH)
        return _customer


def reload() -> None:
    """Force reload both JSON files on the next call. Useful for tests."""
    global _upstream, _customer
    with _lock:
        _upstream = None
        _customer = None


# ---- Upstream cost estimation -------------------------------------------

@dataclass(frozen=True)
class TurnCost:
    """Estimated USD cost for a single provider turn."""
    provider: str
    backend: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    usd: float
    hit_cache: bool

    def fmt(self) -> str:
        """Short display form for the HUD."""
        base = f"${self.usd:.4f}" if self.usd < 0.01 else f"${self.usd:.3f}"
        if self.hit_cache:
            base += " (cached)"
        return base


def estimate_turn_cost(
    provider: str,
    backend_name: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_duration: str = "5m",
) -> TurnCost:
    """Compute the USD cost of one provider turn.

    ``cached_input_tokens`` are input tokens that hit the provider's
    prompt cache (cheap). ``cache_write_tokens`` are tokens that
    populated a new cache entry this turn (expensive, one-time).
    Providers that don't support caching leave both at zero.

    Anthropic bills cache writes at 1.25x base for 5-min TTL and 2x
    base for 1-hour TTL. ``cache_duration`` picks the multiplier.
    """
    prices = upstream_costs().get("prices", {})
    defaults = upstream_costs().get("defaults", {"input": 1.0, "output": 5.0})

    key = f"{provider}:{backend_name}"
    rate = prices.get(key)
    # Try a wildcard entry for the provider (e.g. openrouter:*).
    if rate is None:
        rate = prices.get(f"{provider}:*")
    if rate is None:
        rate = defaults

    in_rate = float(rate.get("input", defaults["input"]))
    out_rate = float(rate.get("output", defaults["output"]))
    cached_rate = float(rate.get("cached_input", in_rate))
    write_key = f"cache_write_{cache_duration}"
    write_rate = float(rate.get(write_key, rate.get("cache_write_5m", in_rate * 1.25)))

    # A subscription-based provider has zero per-token cost but a fixed
    # monthly fee. We don't amortize that fee per turn here; the
    # HUD's session-total line can add it as a separate row. Per-turn
    # cost for those models stays $0.
    fresh_input = max(input_tokens - cached_input_tokens - cache_write_tokens, 0)
    cost = (
        (fresh_input / 1_000_000) * in_rate
        + (cached_input_tokens / 1_000_000) * cached_rate
        + (cache_write_tokens / 1_000_000) * write_rate
        + (output_tokens / 1_000_000) * out_rate
    )

    return TurnCost(
        provider=provider,
        backend=backend_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_write_tokens=cache_write_tokens,
        usd=cost,
        hit_cache=(cached_input_tokens > 0),
    )


def is_subscription_model(provider: str, backend_name: str) -> bool:
    """True when the upstream uses a flat subscription (e.g. Ollama Pro)."""
    rate = upstream_costs().get("prices", {}).get(f"{provider}:{backend_name}", {})
    return bool(rate.get("subscription_monthly"))


def subscription_monthly_cost(provider: str, backend_name: str) -> float:
    """Monthly subscription fee for the provider covering this model, or 0."""
    rate = upstream_costs().get("prices", {}).get(f"{provider}:{backend_name}", {})
    return float(rate.get("subscription_monthly", 0.0))


# ---- Customer credit accounting -----------------------------------------

def tier_for_model(model_cfg: dict) -> str:
    """Classify a model config into 'fast', 'balanced', 'flagship', or 'deepparallel'.

    Matches against both ``backend_name`` and ``label`` so local rebrands
    (CroweLM Supreme vs claude-opus-4-7) and aliased NIM entries all
    resolve. Returns 'balanced' as the conservative default so an
    unknown model doesn't get free flagship billing.

    Order matters: ``deepparallel`` is checked first so a DeepParallel tier
    (which lists ``crowelm-deepparallel`` and ``CroweLM DeepParallel`` in its
    bucket) doesn't accidentally also match the flagship bucket.
    """
    classification = customer_pricing().get("tier_classification", {})
    label = model_cfg.get("label", "")
    backend = model_cfg.get("backend_name") or model_cfg.get("name", "")

    for tier in ("deepparallel", "flagship", "balanced", "fast"):
        bucket = classification.get(tier, [])
        if label in bucket or backend in bucket:
            return tier
    return "balanced"


@dataclass(frozen=True)
class CreditCost:
    """Credit cost of one user-facing turn."""
    credits: int
    breakdown: dict


def estimate_turn_credits(
    model_cfg: dict,
    *,
    tool_call_count: int = 0,
    dual_mode_peer_cfg: dict | None = None,
    synthesis: bool = False,
    browser_automation: bool = False,
    nemoclaw_sandbox: bool = False,
) -> CreditCost:
    """Compute credits for one user-facing turn.

    ``dual_mode_peer_cfg`` triggers dual-model billing: credits are
    the sum of both sides' per-turn credits. ``synthesis=True`` adds
    one Supreme-equivalent turn on top. Tool calls get a free budget
    per turn and overage at 1 credit per 10.
    """
    cp = customer_pricing()
    costs = cp.get("credit_costs", {})

    def _credits_for_cfg(cfg: dict) -> int:
        tier = tier_for_model(cfg)
        return int(costs.get(f"turn_{tier}", 2))

    breakdown: dict[str, int] = {}
    total = _credits_for_cfg(model_cfg)
    breakdown["primary"] = total

    if dual_mode_peer_cfg is not None:
        peer = _credits_for_cfg(dual_mode_peer_cfg)
        total += peer
        breakdown["dual_mode_peer"] = peer

    if synthesis:
        synth = int(costs.get("synthesis_addition", 5))
        total += synth
        breakdown["synthesis"] = synth

    free_budget = int(costs.get("tool_call_free_budget_per_turn", 10))
    overage_per_10 = int(costs.get("tool_call_overage_per_10", 1))
    if tool_call_count > free_budget:
        chargeable = tool_call_count - free_budget
        overage = (chargeable + 9) // 10 * overage_per_10
        total += overage
        breakdown["tool_overage"] = overage

    if browser_automation:
        bs = int(costs.get("browser_automation_session", 3))
        total += bs
        breakdown["browser"] = bs

    if nemoclaw_sandbox:
        ns = int(costs.get("nemoclaw_sandbox_turn_surcharge", 2))
        total += ns
        breakdown["nemoclaw"] = ns

    return CreditCost(credits=total, breakdown=breakdown)


def tier_details(tier_key: str) -> dict:
    """Return the raw tier block from customer_pricing.json."""
    tiers = customer_pricing().get("tiers", {})
    return tiers.get(tier_key, {})


def all_tiers() -> dict:
    """Return every tier keyed by machine name."""
    return customer_pricing().get("tiers", {})


# ---- Margin reporting ---------------------------------------------------

@dataclass(frozen=True)
class MarginReport:
    """Unit economics snapshot for one customer's month."""
    tier: str
    price: float
    upstream_cost: float
    gross_margin: float
    gross_margin_pct: float
    credits_used: int
    credits_allocated: int | str


def margin_report(
    tier_key: str,
    *,
    upstream_cost_monthly: float,
    credits_used: int,
) -> MarginReport:
    """Build a margin report for a tier at a given upstream burn.

    ``upstream_cost_monthly`` is the sum of all ``estimate_turn_cost``
    USD totals for the customer's month, plus allocated subscription
    costs (e.g. Ollama Pro amortized). ``credits_used`` is for display.
    """
    tier = tier_details(tier_key)
    # Enterprise tier stores "custom" as a literal sentinel; in that case
    # fall through to the floor. Numeric prices convert cleanly.
    raw_price = (
        tier.get("price_monthly")
        or tier.get("price_monthly_per_seat")
        or tier.get("price_floor_monthly_per_seat", 0)
        or 0
    )
    if isinstance(raw_price, str):
        raw_price = tier.get("price_floor_monthly_per_seat", 0) or 0
    price = float(raw_price)
    margin = price - upstream_cost_monthly
    pct = (margin / price * 100) if price > 0 else 0.0
    credits_allocated = tier.get("credits_monthly") or tier.get("credits_monthly_per_seat") or 0

    return MarginReport(
        tier=tier_key,
        price=price,
        upstream_cost=upstream_cost_monthly,
        gross_margin=margin,
        gross_margin_pct=pct,
        credits_used=credits_used,
        credits_allocated=credits_allocated,
    )


# ---- Session running totals (operator HUD) ------------------------------

class SessionCostTracker:
    """Accumulates cost + credit totals across a CLI session.

    Thread-safe so dual-mode worker threads can post from their own
    contexts. The operator HUD reads `.snapshot()` each frame to
    render running totals.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._turns: list[dict] = []

    def record_turn(
        self,
        *,
        model_cfg: dict,
        cost: TurnCost,
        credits: CreditCost | None = None,
        dual_pair: bool = False,
    ) -> None:
        with self._lock:
            self._turns.append({
                "label": model_cfg.get("label", "?"),
                "usd": cost.usd,
                "credits": credits.credits if credits else 0,
                "input_tokens": cost.input_tokens,
                "output_tokens": cost.output_tokens,
                "hit_cache": cost.hit_cache,
                "dual_pair": dual_pair,
            })

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            turns = list(self._turns)
        return {
            "turn_count": len(turns),
            "total_usd": sum(t["usd"] for t in turns),
            "total_credits": sum(t["credits"] for t in turns),
            "total_input_tokens": sum(t["input_tokens"] for t in turns),
            "total_output_tokens": sum(t["output_tokens"] for t in turns),
            "cached_turns": sum(1 for t in turns if t["hit_cache"]),
            "turns": turns,
        }

    def reset(self) -> None:
        with self._lock:
            self._turns.clear()
