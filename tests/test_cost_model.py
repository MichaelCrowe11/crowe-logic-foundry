"""Tests for cli/cost_model.py.

Covers three concerns:
  1. Upstream cost math (rate lookup, cache discount, subscription models).
  2. Customer credit math (tier classification, dual mode, synthesis, tools).
  3. Margin reports and session tracker thread safety.

No network, no disk writes. Reads the real JSON files since those are
the source of truth this module is meant to compute against.
"""

from __future__ import annotations

import threading

import pytest

from cli.cost_model import (
    SessionCostTracker,
    all_tiers,
    estimate_turn_cost,
    estimate_turn_credits,
    is_subscription_model,
    margin_report,
    reload,
    subscription_monthly_cost,
    tier_details,
    tier_for_model,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    reload()


# ---- Upstream cost -------------------------------------------------------

def test_opus_cold_turn_is_in_expected_range():
    cost = estimate_turn_cost(
        "anthropic", "claude-opus-4-7",
        input_tokens=20_000, output_tokens=2_000,
    )
    assert 0.10 <= cost.usd <= 0.20, cost.usd


def test_opus_cached_turn_is_much_cheaper_than_cold():
    cold = estimate_turn_cost(
        "anthropic", "claude-opus-4-7",
        input_tokens=20_000, output_tokens=2_000,
    )
    cached = estimate_turn_cost(
        "anthropic", "claude-opus-4-7",
        input_tokens=20_000, output_tokens=2_000,
        cached_input_tokens=18_000,
    )
    assert cached.usd < cold.usd * 0.55
    assert cached.hit_cache is True


def test_cache_write_5m_stays_close_to_plain_cold():
    """5-minute cache write at 1.25x base should add minor overhead, not double cost."""
    plain = estimate_turn_cost(
        "anthropic", "claude-opus-4-7",
        input_tokens=20_000, output_tokens=500,
    )
    with_write = estimate_turn_cost(
        "anthropic", "claude-opus-4-7",
        input_tokens=2_000, output_tokens=500,
        cache_write_tokens=18_000,
        cache_duration="5m",
    )
    # Same total input tokens, cache-writing the bulk at 1.25x base.
    # Should be within 35% of plain cold cost.
    assert plain.usd < with_write.usd < plain.usd * 1.35


def test_cache_write_1h_is_more_expensive_than_5m():
    write_5m = estimate_turn_cost(
        "anthropic", "claude-opus-4-7",
        input_tokens=2_000, output_tokens=500,
        cache_write_tokens=18_000, cache_duration="5m",
    )
    write_1h = estimate_turn_cost(
        "anthropic", "claude-opus-4-7",
        input_tokens=2_000, output_tokens=500,
        cache_write_tokens=18_000, cache_duration="1h",
    )
    assert write_1h.usd > write_5m.usd


def test_ollama_cloud_reports_zero_per_turn_cost():
    cost = estimate_turn_cost(
        "ollama", "kimi-k2.6:cloud",
        input_tokens=20_000, output_tokens=2_000,
    )
    assert cost.usd == 0.0
    assert is_subscription_model("ollama", "kimi-k2.6:cloud") is True
    assert subscription_monthly_cost("ollama", "kimi-k2.6:cloud") == 20.0


def test_nvidia_nim_is_free_tier():
    cost = estimate_turn_cost(
        "nvidia", "deepseek-ai/deepseek-r1",
        input_tokens=50_000, output_tokens=5_000,
    )
    assert cost.usd == 0.0


def test_unknown_model_falls_back_to_defaults_not_zero():
    """Unknown models must NOT report zero cost, otherwise we under-report burn."""
    cost = estimate_turn_cost(
        "mystery", "some-new-model",
        input_tokens=20_000, output_tokens=2_000,
    )
    assert cost.usd > 0.02


def test_wildcard_provider_rate_is_respected():
    """openrouter:* entry should match any OpenRouter backend."""
    cost = estimate_turn_cost(
        "openrouter", "any/model-name",
        input_tokens=1_000, output_tokens=100,
    )
    # openrouter:* has zero per-token, fee is on top-up
    assert cost.usd == 0.0


# ---- Tier classification -------------------------------------------------

@pytest.mark.parametrize("label,backend,expected", [
    ("CroweLM Supreme", "claude-opus-4-7", "flagship"),
    ("CroweLM Eclipse", "kimi-k2.6:cloud", "flagship"),
    ("CroweLM Crescent", "kimi-k2.5:cloud", "balanced"),
    ("CroweLM Nano", "claude-haiku-4-5-20251001", "fast"),
    ("CroweLM Talon (NemoClaw)", "nvidia/llama-3.1-nemotron-ultra-253b-v1", "balanced"),
    ("Something Unknown", "unknown/model", "balanced"),
    # DeepParallel tier — premium per-query pricing, distinct from flagship.
    # tier_for_model must check "deepparallel" before "flagship" so DeepParallel
    # entries don't accidentally fall through to the cheaper flagship bucket.
    ("CroweLM DeepParallel", "crowelm-cluster-multilineage-v1", "deepparallel"),
    ("CroweLM DeepParallel", "crowelm-cluster-v1", "deepparallel"),
])
def test_tier_for_model(label, backend, expected):
    assert tier_for_model({"label": label, "backend_name": backend}) == expected


# ---- Credit estimation ---------------------------------------------------

def test_single_flagship_turn_costs_5_credits():
    cfg = {"label": "CroweLM Supreme", "backend_name": "claude-opus-4-7"}
    cc = estimate_turn_credits(cfg)
    assert cc.credits == 5
    assert cc.breakdown == {"primary": 5}


def test_single_fast_turn_costs_1_credit():
    cfg = {"label": "CroweLM Nano", "backend_name": "claude-haiku-4-5-20251001"}
    assert estimate_turn_credits(cfg).credits == 1


def test_single_deepparallel_turn_costs_15_credits():
    """DeepParallel tier is a premium per-query rate covering the cluster fan-out."""
    cfg = {"label": "CroweLM DeepParallel", "backend_name": "crowelm-cluster-multilineage-v1"}
    cc = estimate_turn_credits(cfg)
    assert cc.credits == 15
    assert cc.breakdown == {"primary": 15}


def test_dual_mode_sums_both_sides():
    supreme = {"label": "CroweLM Supreme", "backend_name": "claude-opus-4-7"}
    eclipse = {"label": "CroweLM Eclipse", "backend_name": "kimi-k2.6:cloud"}
    cc = estimate_turn_credits(supreme, dual_mode_peer_cfg=eclipse)
    assert cc.credits == 10


def test_synthesis_adds_5_credits():
    supreme = {"label": "CroweLM Supreme", "backend_name": "claude-opus-4-7"}
    eclipse = {"label": "CroweLM Eclipse", "backend_name": "kimi-k2.6:cloud"}
    cc = estimate_turn_credits(supreme, dual_mode_peer_cfg=eclipse, synthesis=True)
    assert cc.credits == 15
    assert cc.breakdown["synthesis"] == 5


def test_tool_overage_only_charges_above_free_budget():
    cfg = {"label": "CroweLM Nano", "backend_name": "claude-haiku-4-5-20251001"}
    under = estimate_turn_credits(cfg, tool_call_count=8)
    at_limit = estimate_turn_credits(cfg, tool_call_count=10)
    over = estimate_turn_credits(cfg, tool_call_count=25)
    assert under.credits == 1, under.breakdown
    assert at_limit.credits == 1, at_limit.breakdown
    assert over.credits == 1 + 2, over.breakdown   # 15 overage -> ceil(15/10) = 2


def test_browser_and_sandbox_surcharges_stack():
    cfg = {"label": "CroweLM Supreme", "backend_name": "claude-opus-4-7"}
    cc = estimate_turn_credits(cfg, browser_automation=True, nemoclaw_sandbox=True)
    assert cc.credits == 5 + 3 + 2


# ---- Tier definitions ----------------------------------------------------

def test_all_tiers_have_a_price_and_credit_allocation():
    tiers = all_tiers()
    assert set(tiers.keys()) == {"personal", "pro", "team", "enterprise"}
    for key, tier in tiers.items():
        has_price = any(k in tier for k in (
            "price_monthly", "price_monthly_per_seat", "price_floor_monthly_per_seat"
        ))
        has_credits = any(k in tier for k in (
            "credits_monthly", "credits_monthly_per_seat"
        ))
        assert has_price, f"{key} missing price"
        assert has_credits, f"{key} missing credit allocation"


def test_personal_tier_is_29_dollars():
    personal = tier_details("personal")
    assert personal["price_monthly"] == 29.0


def test_pro_tier_is_99_dollars():
    pro = tier_details("pro")
    assert pro["price_monthly"] == 99.0


def test_team_tier_is_49_per_seat_with_3_seat_min():
    team = tier_details("team")
    assert team["price_monthly_per_seat"] == 49.0
    assert team["seat_min"] == 3


# ---- Margin reports ------------------------------------------------------

def test_personal_margin_is_healthy_at_light_usage():
    mr = margin_report("personal", upstream_cost_monthly=8.0, credits_used=500)
    assert mr.gross_margin_pct > 60


def test_pro_margin_survives_heavy_usage():
    mr = margin_report("pro", upstream_cost_monthly=35.0, credits_used=2800)
    assert mr.gross_margin_pct > 55


def test_margin_report_handles_custom_enterprise_price():
    mr = margin_report("enterprise", upstream_cost_monthly=80.0, credits_used=10000)
    assert mr.price >= 250.0, "Enterprise floor should kick in when price is custom"
    assert mr.gross_margin > 0


# ---- Session tracker -----------------------------------------------------

def test_session_tracker_accumulates_totals():
    st = SessionCostTracker()
    cfg = {"label": "CroweLM Supreme", "backend_name": "claude-opus-4-7"}
    for _ in range(3):
        cost = estimate_turn_cost(
            "anthropic", "claude-opus-4-7",
            input_tokens=10_000, output_tokens=1_000,
        )
        credits = estimate_turn_credits(cfg)
        st.record_turn(model_cfg=cfg, cost=cost, credits=credits)
    snap = st.snapshot()
    assert snap["turn_count"] == 3
    assert snap["total_credits"] == 15
    assert snap["total_input_tokens"] == 30_000


def test_session_tracker_is_thread_safe():
    """Dual-mode workers post from different threads; must not race."""
    st = SessionCostTracker()
    cfg = {"label": "CroweLM Supreme", "backend_name": "claude-opus-4-7"}
    barrier = threading.Barrier(5)

    def worker():
        barrier.wait()
        for _ in range(100):
            cost = estimate_turn_cost(
                "anthropic", "claude-opus-4-7",
                input_tokens=100, output_tokens=10,
            )
            st.record_turn(model_cfg=cfg, cost=cost, credits=None)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = st.snapshot()
    assert snap["turn_count"] == 500
