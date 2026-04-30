"""Tests for the complexity router and tier runtime params."""

from __future__ import annotations

from config.agent_config import tier_runtime_params, tier_runtime_params_for_model
from config.router import (
    LOW_CONFIDENCE_THRESHOLD,
    RouteDecision,
    classify_prompt,
    classify_with_confidence,
    route_prompt,
)


# ─── classify_prompt ────────────────────────────────────────────────

def test_classify_capability_question():
    assert classify_prompt("can you work in parallel with the agent above?") == "capability_question"
    assert classify_prompt("Could you help me?") == "capability_question"
    assert classify_prompt("Do you have access to Slack?") == "capability_question"


def test_classify_arithmetic():
    assert classify_prompt("2+2") == "arithmetic"
    assert classify_prompt("what is 5 * 3?") == "arithmetic"
    assert classify_prompt("compute (4 + 7) / 2") == "arithmetic"


def test_classify_trivial():
    assert classify_prompt("hi") == "trivial"
    assert classify_prompt("thanks") == "trivial"
    assert classify_prompt("ok") == "trivial"


def test_classify_vision():
    assert classify_prompt("look at this screenshot and tell me what's wrong") == "vision"
    assert classify_prompt("here is a photo of a contam plate") == "vision"


def test_classify_code():
    assert classify_prompt("refactor this function to use async") == "code"
    assert classify_prompt("```python\ndef foo(): pass\n```") == "code"
    assert classify_prompt("write a unit test for the user model") == "code"


def test_classify_domain():
    assert classify_prompt("what humidity should oyster mushroom fruiting be at?") == "domain"
    assert classify_prompt("explain the binding affinity of psilocybin") == "domain"
    assert classify_prompt("how do I prepare a sterile substrate?") == "domain"


def test_classify_deep():
    assert classify_prompt("design the architecture for a multi-tenant ingestion pipeline") == "deep"
    assert classify_prompt("write a strategy doc for our launch") == "deep"


def test_classify_general_for_long_neutral_prompts():
    long_prompt = (
        "I need to understand what the right approach is for handling user "
        "session timeouts in a typical web application. There are a few options."
    )
    assert classify_prompt(long_prompt) == "general"


def test_classify_ambiguous_short_prompts():
    assert classify_prompt("???") == "ambiguous"
    assert classify_prompt("more") == "trivial"  # short alpha word
    assert classify_prompt("...?") == "ambiguous"


def test_classify_empty_or_whitespace():
    assert classify_prompt("") == "ambiguous"
    assert classify_prompt("   ") == "ambiguous"


# ─── route_prompt ───────────────────────────────────────────────────

def test_route_capability_question_picks_fast_tier():
    """The transcript prompt that wasted 7 minutes on the 120B flagship."""
    decision = route_prompt("can you work in parallel with the agent above?")
    assert isinstance(decision, RouteDecision)
    assert decision.intent == "capability_question"
    assert decision.selected_type == "fast", (
        f"Expected fast tier, got {decision.selected_type} ({decision.selected_label})"
    )


def test_route_arithmetic_picks_fast_tier():
    decision = route_prompt("2+2")
    assert decision.intent == "arithmetic"
    assert decision.selected_type == "fast"


def test_route_vision_picks_vision_tier():
    decision = route_prompt("look at this screenshot of the contam plate")
    assert decision.intent == "vision"
    assert decision.selected_type == "vision"


def test_route_code_picks_code_or_reasoning_tier():
    decision = route_prompt("write a function that parses semver strings")
    assert decision.intent == "code"
    # Code tier is preferred but reasoning is acceptable fallback if no code-typed
    # model is configured.
    assert decision.selected_type in ("code", "reasoning")


def test_route_domain_picks_reasoning_tier():
    decision = route_prompt("what is the optimal CO2 ppm for shiitake fruiting blocks?")
    assert decision.intent == "domain"
    assert decision.selected_type == "reasoning"


def test_route_deep_picks_reasoning_tier():
    decision = route_prompt("design the architecture for a federated training pipeline")
    assert decision.intent == "deep"
    assert decision.selected_type == "reasoning"


def test_route_decision_is_serializable():
    decision = route_prompt("hi")
    payload = decision.to_dict()
    assert "intent" in payload
    assert "selected_label" in payload
    assert "reason" in payload


def test_route_with_minimal_chain_falls_back_gracefully():
    """When no preferred selector matches, router returns the first chain entry."""
    minimal_chain = [
        {"name": "only-model", "label": "Solo", "type": "reasoning", "aliases": []},
    ]
    decision = route_prompt("can you do this?", chain=minimal_chain)
    assert decision.selected_label == "Solo"
    assert "fall" in decision.reason.lower() or "first entry" in decision.reason.lower() \
        or decision.intent == "capability_question"


# ─── tier_runtime_params ────────────────────────────────────────────

def test_tier_runtime_params_returns_dict_per_type():
    fast = tier_runtime_params({"type": "fast"})
    assert fast["temperature"] < 0.5
    assert fast["max_tokens"] <= 1024

    reasoning = tier_runtime_params({"type": "reasoning"})
    assert reasoning["max_tokens"] >= 2048

    code = tier_runtime_params({"type": "code"})
    assert code["temperature"] <= 0.3
    assert code["max_tokens"] >= 4096


def test_tier_runtime_params_unknown_type_returns_empty():
    assert tier_runtime_params({"type": "unknown"}) == {}
    assert tier_runtime_params(None) == {}


def test_tier_runtime_params_for_model_resolves_via_registry():
    # Nano resolves through alias and yields fast-tier params.
    params = tier_runtime_params_for_model("CroweLM Nano")
    assert params.get("temperature", 1.0) < 0.5

    # Unknown selector returns empty.
    assert tier_runtime_params_for_model("CroweLM DoesNotExist") == {}


# ─── classify_with_confidence ───────────────────────────────────────

def test_confidence_arithmetic_is_near_certain():
    intent, conf = classify_with_confidence("2+2")
    assert intent == "arithmetic"
    assert conf >= 0.95


def test_confidence_short_capability_high():
    intent, conf = classify_with_confidence("can you help me?")
    assert intent == "capability_question"
    assert conf >= 0.80


def test_confidence_long_capability_lower():
    """Long capability questions are often disguised domain prompts."""
    intent, conf = classify_with_confidence(
        "can you walk me through the optimal substrate sterilization "
        "protocol for industrial-scale oyster mushroom cultivation, "
        "including pasteurization vs sterilization tradeoffs?"
    )
    # Long enough that it's no longer recognized as a pure capability question.
    # Either returns capability_question with reduced confidence, or domain.
    assert intent in ("capability_question", "domain")
    if intent == "capability_question":
        assert conf < 0.80


def test_confidence_domain_scales_with_keyword_density():
    intent_one, conf_one = classify_with_confidence("how do I prepare a substrate?")
    intent_two, conf_two = classify_with_confidence(
        "how do I prepare a sterile substrate for oyster mushroom fruiting?"
    )
    assert intent_one == "domain"
    assert intent_two == "domain"
    assert conf_two > conf_one  # more keywords = higher confidence


def test_confidence_ambiguous_below_threshold():
    intent, conf = classify_with_confidence("???")
    assert intent == "ambiguous"
    assert conf < LOW_CONFIDENCE_THRESHOLD


def test_confidence_general_below_threshold_flagged_as_borderline():
    """General prompts hover around the threshold; that's intentional —
    they should be candidates for promotion review."""
    intent, conf = classify_with_confidence(
        "I need to understand what the right approach is for handling "
        "user session timeouts in a typical web application."
    )
    assert intent == "general"
    # General confidence is set near the threshold so the auto-router
    # can decide whether to escalate.
    assert 0.5 <= conf <= 0.75


def test_route_decision_carries_confidence():
    decision = route_prompt("can you do this?")
    assert hasattr(decision, "confidence")
    assert 0.0 <= decision.confidence <= 1.0


def test_route_decision_low_confidence_flag():
    """Ambiguous prompts produce low-confidence decisions."""
    decision = route_prompt("???")
    assert decision.low_confidence is True


def test_route_decision_high_confidence_for_clear_prompts():
    decision = route_prompt("2+2")
    assert decision.low_confidence is False
    assert decision.confidence >= 0.95


def test_route_decision_to_dict_includes_confidence():
    decision = route_prompt("hi")
    payload = decision.to_dict()
    assert "confidence" in payload
    assert payload["confidence"] >= 0.85  # trivial = high confidence


def test_classify_prompt_still_returns_string():
    """Backward compat: classify_prompt should still return just the intent."""
    result = classify_prompt("can you help?")
    assert isinstance(result, str)
    assert result == "capability_question"
