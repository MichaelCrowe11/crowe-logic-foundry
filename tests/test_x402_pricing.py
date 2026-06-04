"""Per-model x402 pricing: frontier models must not be sold at nano price.

This closes the flat-pricing leak (every model charged the same 50 micro-USD).
Prices come from config/x402_pricing.json, grounded in upstream_costs.json.
"""

from control_plane import x402


def test_frontier_costs_more_than_nano():
    assert x402.price_for_model("claude-opus-4-6") > x402.price_for_model("FW-GLM-5")
    assert x402.price_for_model("gpt-5.4-pro") > x402.price_for_model("claude-opus-4-6")


def test_unknown_model_gets_default_not_floor_of_zero():
    cfg = x402._pricing()
    assert x402.price_for_model("totally-unknown-model") == cfg["default_micro_usd"]


def test_prices_are_positive_ints():
    for m in ("FW-GLM-5", "Kimi-K2.5", "claude-opus-4-6", "gpt-5.4-pro"):
        p = x402.price_for_model(m)
        assert isinstance(p, int) and p > 0


def test_frontier_is_not_the_old_flat_nano_price():
    # The bug: claude-opus used to cost the same flat 50 micro-USD as a nano model.
    assert x402.price_for_model("claude-opus-4-6") > 50
    assert x402.price_for_model("gpt-5.4-pro") > 50
