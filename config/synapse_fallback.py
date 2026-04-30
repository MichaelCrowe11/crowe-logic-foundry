"""
Synapse Phase 2 - DeepParallel low-confidence fallback classifier.

When the heuristic router classifies a prompt with confidence below
LOW_CONFIDENCE_THRESHOLD, optionally escalate to DeepParallel - a
locally-hosted multi-chain reasoning model (default
`Mcrowe1210/DeepParallel:v2.2` on Ollama) - for a second-opinion
routing decision. Marginal cost is essentially zero (local Ollama
inference); the upside is correctly routing genuinely ambiguous
prompts.

Activation
----------
Off by default. Enable with::

    export CROWE_LOGIC_SYNAPSE_FALLBACK=1

Optional overrides::

    export CROWE_LOGIC_SYNAPSE_FALLBACK_MODEL='Mcrowe1210/DeepParallel:v2.2'
    export CROWE_LOGIC_SYNAPSE_FALLBACK_BASE_URL='http://localhost:11434/v1'
    export CROWE_LOGIC_SYNAPSE_FALLBACK_TIMEOUT_S='8'

Failure modes
-------------
The fallback never raises. If Ollama is unreachable, the model is not
pulled, the response is malformed, or the call exceeds its timeout,
the function returns ``None`` and the caller keeps the heuristic
decision. All failures and successes are logged via the telemetry
sink so operators can audit fallback behavior.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

# Valid intent labels - must match config/router.py classifier outputs.
_VALID_INTENTS: tuple[str, ...] = (
    "arithmetic", "trivial", "vision", "code", "capability_question",
    "domain", "deep", "general", "ambiguous",
)

# Compact classification prompt. DeepParallel will run multi-chain
# internally; we only need a single structured decision back.
_CLASSIFIER_SYSTEM = (
    "You are an intent classifier for the CroweLM Synapse Router. "
    "Given a user prompt, classify it into exactly one of these intents:\n"
    "- arithmetic: pure math expressions or 'what is X+Y' style queries\n"
    "- trivial: greetings, acks, single-word responses\n"
    "- vision: references to images, screenshots, lab plates, photos\n"
    "- code: code authoring, refactoring, debugging, snippets\n"
    "- capability_question: 'can you...', 'do you have...' meta questions\n"
    "- domain: mycology, biotech, drug discovery, lab work, cultivation\n"
    "- deep: architecture, strategy, design docs, in-depth analysis\n"
    "- general: substantive questions that don't fit the above\n"
    "- ambiguous: too short or unclear to classify\n\n"
    "Reply with a single line of JSON: "
    '{"intent": "<one_of_the_above>", "confidence": <0.0..1.0>}\n'
    "No other text. No explanation."
)


def fallback_enabled() -> bool:
    """Return True when CROWE_LOGIC_SYNAPSE_FALLBACK selects DeepParallel."""
    val = os.environ.get("CROWE_LOGIC_SYNAPSE_FALLBACK", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _model_name() -> str:
    return os.environ.get(
        "CROWE_LOGIC_SYNAPSE_FALLBACK_MODEL",
        "Mcrowe1210/DeepParallel:v2.2",
    )


def _base_url() -> str:
    return os.environ.get(
        "CROWE_LOGIC_SYNAPSE_FALLBACK_BASE_URL",
        os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
    )


def _timeout_s() -> float:
    raw = os.environ.get("CROWE_LOGIC_SYNAPSE_FALLBACK_TIMEOUT_S", "8")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 8.0


def classify_with_deepparallel(text: str) -> Optional[tuple[str, float]]:
    """Return (intent, confidence) from DeepParallel, or None on failure.

    Synchronous and short - DeepParallel runs locally and we cap the
    request at `_timeout_s()` seconds. Failures (timeout, parse error,
    unknown intent) return None and log to telemetry; the caller keeps
    its heuristic decision.
    """
    if not text or not text.strip():
        return None

    try:
        # Lazy import: callers that never enable the fallback should
        # not pay the openai-package import cost on every routing call.
        from openai import OpenAI
    except Exception:
        _log("import_error", reason="openai_sdk_missing")
        return None

    try:
        client = OpenAI(
            api_key="ollama",
            base_url=_base_url(),
            timeout=_timeout_s(),
        )
    except Exception as e:
        _log("client_init_error", reason=str(e)[:200])
        return None

    try:
        response = client.chat.completions.create(
            model=_model_name(),
            messages=[
                {"role": "system", "content": _CLASSIFIER_SYSTEM},
                {"role": "user", "content": text},
            ],
            stream=False,
            temperature=0.1,
            max_tokens=64,
        )
    except Exception as e:
        _log("call_error", reason=type(e).__name__, message=str(e)[:200])
        return None

    try:
        content = response.choices[0].message.content or ""
    except (AttributeError, IndexError):
        _log("response_shape_error")
        return None

    parsed = _parse_classifier_output(content)
    if parsed is None:
        _log("parse_error", raw=content[:200])
        return None

    intent, confidence = parsed
    if intent not in _VALID_INTENTS:
        _log("unknown_intent", intent=intent, raw=content[:200])
        return None

    confidence = max(0.0, min(1.0, confidence))
    _log("ok", intent=intent, confidence=round(confidence, 3))
    return (intent, confidence)


def _parse_classifier_output(text: str) -> Optional[tuple[str, float]]:
    """Pull `{intent, confidence}` out of the model's reply.

    Tolerates leading/trailing prose by extracting the first JSON object.
    """
    if not text:
        return None

    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not match:
        return None

    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return None

    intent = data.get("intent")
    confidence = data.get("confidence")
    if not isinstance(intent, str):
        return None
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        return None

    return (intent.strip().lower(), confidence)


def _log(status: str, **fields) -> None:
    """Best-effort telemetry write; never raises."""
    try:
        from config.telemetry import telemetry
        telemetry.log_event(
            "synapse_fallback",
            {"status": status, "model": _model_name(), **fields},
        )
    except Exception:
        pass


__all__ = [
    "classify_with_deepparallel",
    "fallback_enabled",
]
