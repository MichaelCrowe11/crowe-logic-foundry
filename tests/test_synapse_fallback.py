"""Tests for the Synapse Phase 2 DeepParallel fallback classifier.

All tests mock the OpenAI client so the suite stays hermetic — no
Ollama needed. Coverage:
- env-flag activation
- successful classification
- malformed JSON tolerated
- timeouts and connection errors don't raise
- unknown intents rejected
- route_prompt() integration: low-confidence prompts get the fallback,
  high-confidence prompts skip it, fallback never lowers confidence
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from config import synapse_fallback
from config.router import LOW_CONFIDENCE_THRESHOLD, route_prompt
from config.synapse_fallback import (
    _parse_classifier_output,
    classify_with_deepparallel,
    fallback_enabled,
)


# ─── fallback_enabled ───────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("1", True),
    ("true", True),
    ("yes", True),
    ("on", True),
    ("0", False),
    ("false", False),
    ("", False),
    ("nope", False),
])
def test_fallback_enabled_reads_env(monkeypatch, value, expected):
    monkeypatch.setenv("CROWE_LOGIC_SYNAPSE_FALLBACK", value)
    assert fallback_enabled() is expected


def test_fallback_disabled_when_env_missing(monkeypatch):
    monkeypatch.delenv("CROWE_LOGIC_SYNAPSE_FALLBACK", raising=False)
    assert fallback_enabled() is False


# ─── _parse_classifier_output ───────────────────────────────────────

def test_parse_clean_json():
    assert _parse_classifier_output('{"intent": "domain", "confidence": 0.85}') == \
        ("domain", 0.85)


def test_parse_json_with_surrounding_prose():
    raw = 'After analysis: {"intent": "code", "confidence": 0.9} -- done.'
    assert _parse_classifier_output(raw) == ("code", 0.9)


def test_parse_intent_lowercased():
    assert _parse_classifier_output('{"intent": "DOMAIN", "confidence": 0.7}') == \
        ("domain", 0.7)


def test_parse_malformed_returns_none():
    assert _parse_classifier_output("not json at all") is None
    assert _parse_classifier_output("{intent: domain}") is None  # not valid JSON
    assert _parse_classifier_output("") is None


def test_parse_missing_fields_returns_none():
    assert _parse_classifier_output('{"intent": "domain"}') is None
    assert _parse_classifier_output('{"confidence": 0.8}') is None


def test_parse_non_numeric_confidence_returns_none():
    assert _parse_classifier_output('{"intent": "x", "confidence": "high"}') is None


# ─── classify_with_deepparallel ─────────────────────────────────────

def _mock_chat_response(content: str):
    """Build a duck-typed chat-completions response."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def test_classify_returns_none_on_empty_input():
    assert classify_with_deepparallel("") is None
    assert classify_with_deepparallel("   ") is None


def test_classify_success(monkeypatch):
    """Happy path: Ollama returns a valid classification."""
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return _mock_chat_response('{"intent": "domain", "confidence": 0.88}')

        def __init__(self, **kwargs):
            pass

    with patch("openai.OpenAI", FakeClient):
        result = classify_with_deepparallel("how do I sterilize a substrate?")

    assert result == ("domain", 0.88)


def test_classify_clamps_confidence_to_unit_interval(monkeypatch):
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return _mock_chat_response('{"intent": "domain", "confidence": 1.7}')

        def __init__(self, **kwargs):
            pass

    with patch("openai.OpenAI", FakeClient):
        result = classify_with_deepparallel("anything")

    assert result == ("domain", 1.0)


def test_classify_rejects_unknown_intent(monkeypatch):
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return _mock_chat_response('{"intent": "extraterrestrial", "confidence": 0.99}')

        def __init__(self, **kwargs):
            pass

    with patch("openai.OpenAI", FakeClient):
        result = classify_with_deepparallel("x")

    assert result is None


def test_classify_swallows_call_errors(monkeypatch):
    """Network errors must never raise out of the classifier."""
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise ConnectionError("ollama down")

        def __init__(self, **kwargs):
            pass

    with patch("openai.OpenAI", FakeClient):
        result = classify_with_deepparallel("whatever")

    assert result is None  # graceful failure


def test_classify_swallows_malformed_json(monkeypatch):
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return _mock_chat_response("This response has no JSON at all.")

        def __init__(self, **kwargs):
            pass

    with patch("openai.OpenAI", FakeClient):
        result = classify_with_deepparallel("anything")

    assert result is None


# ─── route_prompt integration ───────────────────────────────────────

def test_route_skips_fallback_when_disabled(monkeypatch):
    """High and low confidence both bypass the fallback when env flag is off."""
    monkeypatch.delenv("CROWE_LOGIC_SYNAPSE_FALLBACK", raising=False)

    called = {"count": 0}

    def spy(text):
        called["count"] += 1
        return ("domain", 0.95)

    monkeypatch.setattr(synapse_fallback, "classify_with_deepparallel", spy)

    # Even an ambiguous prompt (low heuristic confidence) does not
    # trigger the fallback when the env flag is off.
    decision = route_prompt("???")
    assert called["count"] == 0
    assert decision.confidence < LOW_CONFIDENCE_THRESHOLD


def test_route_skips_fallback_when_heuristic_is_high_confidence(monkeypatch):
    """Even with the env flag on, high-confidence heuristic answers
    skip the fallback (no marginal benefit, real latency cost)."""
    monkeypatch.setenv("CROWE_LOGIC_SYNAPSE_FALLBACK", "1")

    called = {"count": 0}

    def spy(text):
        called["count"] += 1
        return ("domain", 0.99)

    monkeypatch.setattr(synapse_fallback, "classify_with_deepparallel", spy)

    decision = route_prompt("2+2")  # arithmetic, conf 0.99
    assert called["count"] == 0
    assert decision.intent == "arithmetic"


def test_route_invokes_fallback_on_low_confidence(monkeypatch):
    """Low-confidence prompts trigger the fallback when env flag is on."""
    monkeypatch.setenv("CROWE_LOGIC_SYNAPSE_FALLBACK", "1")

    def spy(text):
        return ("domain", 0.92)

    monkeypatch.setattr(synapse_fallback, "classify_with_deepparallel", spy)

    decision = route_prompt("???")
    assert decision.intent == "domain"  # overridden by fallback
    assert decision.confidence == 0.92
    assert "DeepParallel" in decision.reason


def test_route_keeps_heuristic_when_fallback_returns_lower_confidence(monkeypatch):
    """Fallback never lowers confidence — it can only raise."""
    monkeypatch.setenv("CROWE_LOGIC_SYNAPSE_FALLBACK", "1")

    def spy(text):
        return ("domain", 0.30)  # lower than original heuristic

    monkeypatch.setattr(synapse_fallback, "classify_with_deepparallel", spy)

    # General prompt scores 0.65 from heuristic; fallback returns 0.30.
    # We should keep the heuristic decision.
    decision = route_prompt(
        "I need to understand what the right approach is for handling "
        "user session timeouts in a typical web application."
    )
    assert decision.confidence == 0.65
    assert "DeepParallel" not in decision.reason


def test_route_keeps_heuristic_when_fallback_fails(monkeypatch):
    """Fallback returning None means keep heuristic decision unchanged."""
    monkeypatch.setenv("CROWE_LOGIC_SYNAPSE_FALLBACK", "1")
    monkeypatch.setattr(synapse_fallback, "classify_with_deepparallel", lambda t: None)

    decision = route_prompt("???")
    assert decision.intent == "ambiguous"  # heuristic preserved
    assert "DeepParallel" not in decision.reason


def test_model_name_env_override(monkeypatch):
    monkeypatch.setenv("CROWE_LOGIC_SYNAPSE_FALLBACK_MODEL", "custom/model:v1")
    from config.synapse_fallback import _model_name
    assert _model_name() == "custom/model:v1"


def test_timeout_env_override(monkeypatch):
    monkeypatch.setenv("CROWE_LOGIC_SYNAPSE_FALLBACK_TIMEOUT_S", "12.5")
    from config.synapse_fallback import _timeout_s
    assert _timeout_s() == 12.5


def test_timeout_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("CROWE_LOGIC_SYNAPSE_FALLBACK_TIMEOUT_S", "not-a-number")
    from config.synapse_fallback import _timeout_s
    assert _timeout_s() == 8.0
