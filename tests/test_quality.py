"""Tests for the response-quality heuristics in config.quality."""

from __future__ import annotations

from config.quality import assess_response


def test_empty_response_is_shallow():
    sig = assess_response("")
    assert sig.shallow is True
    assert "empty" in sig.reasons


def test_whitespace_only_is_shallow():
    sig = assess_response("   \n\t  ")
    assert sig.shallow is True


def test_short_response_flagged():
    sig = assess_response("yes.")
    assert sig.shallow is True
    assert "too_short" in sig.reasons


def test_refusal_flagged():
    sig = assess_response("I cannot help with that. As an AI, I have limits.")
    assert sig.shallow is True
    assert "refusal" in sig.reasons


def test_hedge_only_flagged():
    sig = assess_response("Great question! Let me think.")
    assert sig.shallow is True
    assert "hedge_only" in sig.reasons


def test_echoed_question_flagged():
    sig = assess_response("Did you mean to ask about deployment?")
    assert sig.shallow is True
    assert "echoed_question" in sig.reasons


def test_tautology_flagged_when_response_mostly_repeats_prompt():
    prompt = "what is the optimal humidity for oyster mushrooms?"
    response = "what is the optimal humidity for oyster mushrooms"
    sig = assess_response(response, prompt=prompt)
    assert sig.shallow is True
    assert "tautology" in sig.reasons


def test_substantive_answer_passes():
    response = (
        "Oyster mushrooms fruit best at 85-95% relative humidity, with "
        "fresh-air exchange every few hours to prevent CO2 buildup. "
        "Drop humidity to 75-85% during pinning."
    )
    sig = assess_response(response, prompt="what is the optimal humidity for oysters?")
    assert sig.shallow is False
    assert sig.reasons == ()


def test_quality_signal_to_dict_serializable():
    sig = assess_response("ok")
    payload = sig.to_dict()
    assert payload["shallow"] is True
    assert isinstance(payload["reasons"], list)
    assert payload["length"] == 2


def test_long_substantive_response_with_legitimate_inability_phrase():
    """A long answer that mentions inability in passing should not be flagged
    as a refusal — refusal markers fire on assistant declines, not on
    descriptive content."""
    response = (
        "The system was unable to verify the certificate during the handshake. "
        "To fix this, regenerate the cert chain with a fully-qualified intermediate, "
        "then redeploy. The error tells us where the trust failed."
    )
    sig = assess_response(response)
    # No refusal markers fire — "the system was unable" doesn't match
    # the assistant-decline phrases.
    assert "refusal" not in sig.reasons


def test_min_length_override():
    # A 30-char answer normally passes; setting min_length=50 flags it.
    text = "Yes, that is correct. Done."
    sig_default = assess_response(text)
    sig_strict = assess_response(text, min_length=50)
    assert sig_default.shallow is False
    assert sig_strict.shallow is True
    assert "too_short" in sig_strict.reasons
