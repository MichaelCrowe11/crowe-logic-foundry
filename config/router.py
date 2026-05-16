"""
Crowe Logic Foundry - Prompt Router

Classifies user prompts into intents and resolves them to the best available
CroweLM tier in MODEL_CHAIN. Heuristic-only (no extra LLM call before the
user's request).

This module is the canonical replacement for the older ``classify_task`` /
``TASK_CLASS_ROUTES`` system in ``config.agent_config``. The two coexist for
now; the older system will be removed once call sites migrate to
``route_prompt``.

Design principles
-----------------
- Heuristic, not LLM-based. An LLM-based router adds ~500ms TTFB on the 80%
  of prompts that have an obvious shape; the cost of routing a domain prompt
  to Nano is much smaller than the cost of always paying that TTFB tax.
- Conservative escalation. When two intents tie, escalate to the higher tier.
- Structured output (``RouteDecision``) so call sites can log, display, or override.
- No side effects. Does not mutate ``MODEL_CHAIN`` or any global state.

Intent ladder (cheapest first):

    capability_question     "can you...", "do you...", "is there..."
    arithmetic              digits + math operators, no prose
    trivial                 short greetings, yes/no, ack
    ambiguous               very short prompts that aren't clearly one thing
    code                    "write/refactor/fix" + code-shaped tokens
    vision                  references to images, screenshots, photos
    domain                  mycology, biotech, drug discovery keywords
    general                 default reasoning prompts
    deep                    explicit asks for analysis/strategy/architecture
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable

from config.agent_config import MODEL_CHAIN


# ── Intent classification ─────────────────────────────────────────────

_CAPABILITY_PATTERNS = (
    r"\bcan you\b",
    r"\bcould you\b",
    r"\bdo you (have|support|know|offer)\b",
    r"\bare you (able|capable)\b",
    r"\bis there a way\b",
    r"\bwhat (can|do) you\b",
)

# Arithmetic patterns require at least one digit (the lookahead) so that
# punctuation-only prompts like "...?" don't get misclassified as math.
_ARITHMETIC_PATTERN = re.compile(
    r"^\s*(?=[\d().\s+\-*/^%=]*\d)[\d().\s+\-*/^%=]+\s*\??\s*$"
)
_ARITHMETIC_INLINE_PATTERN = re.compile(
    r"^\s*(what(?:'?s)? is |compute |calculate |how much is )?"
    r"(?=[\d().\s+\-*/^%=]*\d)[\d().\s+\-*/^%=]{3,}\s*\??\s*$",
    re.IGNORECASE,
)

_TRIVIAL_PHRASES = {
    "hi", "hello", "hey", "yo", "sup", "thanks", "thank you", "ty",
    "ok", "okay", "k", "cool", "nice", "great", "good", "yes", "no",
    "yep", "nope", "sure", "got it", "understood", "ack",
}

_CODE_KEYWORDS = (
    " refactor", " rewrite", " implement", " function ", " class ", " method ",
    " bug ", " stack trace", " compile", " lint", " test ", " unit test",
    " regex", " snippet", " endpoint", " schema", " migration", " diff ",
)
_CODE_FENCES = re.compile(
    r"```|^\s*(def |class |function |const |let |var )", re.MULTILINE
)

_VISION_KEYWORDS = (
    "screenshot", "screen shot", "image", "photo", "picture", "diagram",
    "look at this", "see this", "in the image", "from the photo",
    "plate", "contam", "contamination",  # mycology lab vision
)

_DOMAIN_KEYWORDS = (
    # Mycology / cultivation
    "mushroom", "mycology", "myceli", "fruiting", "substrate", "spawn",
    "lion's mane", "lions mane", "oyster", "shiitake", "reishi",
    "grow log", "grow tent", "incubation", "pinhead", "primordia",
    # Drug discovery / psychedelics
    "psilocybin", "psilocin", "tryptamine", "serotonin receptor",
    "drug discovery", "lead optimization", "ic50", "binding affinity",
    "docking", "smiles", "compound", "scaffold",
    # Biotech / lab
    "assay", "ph", "agar", "sterilization", "autoclave", "petri",
    "fermentation", "extract", "potency",
)

_DEEP_KEYWORDS = (
    "architect", "architecture", "strategy", "strategic", "design doc",
    "long-form", "write a plan", "write a spec", "deep dive",
    "trade-off", "tradeoff", "evaluate options", "analyze in depth",
)

LOW_CONFIDENCE_THRESHOLD = 0.60

_INTENT_CONFIDENCE: dict[str, float] = {
    "arithmetic": 0.99,
    "trivial": 0.92,
    "vision": 0.90,
    "code": 0.86,
    "capability_question": 0.88,
    "domain": 0.76,
    "deep": 0.78,
    "general": 0.65,
    "ambiguous": 0.40,
}


def _matches_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(p, text) for p in patterns)


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(k in text for k in keywords)


def classify_prompt(text: str) -> str:
    """Return a single intent label for ``text``. Order: specific → general."""
    if not text or not text.strip():
        return "ambiguous"

    raw = text.strip()
    lower = raw.lower()
    # Pad with spaces so space-bounded keywords (e.g. " test ") match at word
    # boundaries rather than across them ("la[test ]" must not classify as code).
    padded = f" {lower} "

    if _ARITHMETIC_PATTERN.match(raw) or _ARITHMETIC_INLINE_PATTERN.match(raw):
        return "arithmetic"

    if lower in _TRIVIAL_PHRASES:
        return "trivial"
    if len(lower) <= 4 and lower.isalpha():
        return "trivial"

    if _matches_any(lower, _CAPABILITY_PATTERNS) and len(raw) < 200:
        return "capability_question"

    if _contains_any(padded, _VISION_KEYWORDS):
        return "vision"

    if _contains_any(padded, _CODE_KEYWORDS) or _CODE_FENCES.search(raw):
        return "code"

    if _contains_any(padded, _DOMAIN_KEYWORDS):
        return "domain"

    if _contains_any(padded, _DEEP_KEYWORDS):
        return "deep"

    if len(raw) < 25:
        return "ambiguous"

    return "general"


def _keyword_density_confidence(
    text: str,
    keywords: Iterable[str],
    *,
    base: float,
    step: float = 0.04,
    ceiling: float = 0.95,
) -> float:
    padded = f" {text.lower()} "
    hits = sum(1 for keyword in keywords if keyword in padded)
    return min(ceiling, base + max(0, hits - 1) * step)


def classify_with_confidence(text: str) -> tuple[str, float]:
    """Return ``(intent, confidence)`` for the heuristic prompt classifier."""
    intent = classify_prompt(text)
    confidence = _INTENT_CONFIDENCE.get(intent, 0.50)

    raw = (text or "").strip()
    lower = raw.lower()

    if intent == "capability_question":
        # Long "can you..." prompts often carry the real task after the opener.
        if len(raw) > 120 or _contains_any(f" {lower} ", _DOMAIN_KEYWORDS):
            confidence = 0.72
    elif intent == "domain":
        confidence = _keyword_density_confidence(
            lower,
            _DOMAIN_KEYWORDS,
            base=_INTENT_CONFIDENCE["domain"],
        )
    elif intent == "code":
        confidence = _keyword_density_confidence(
            lower,
            _CODE_KEYWORDS,
            base=_INTENT_CONFIDENCE["code"],
            step=0.03,
        )
    elif intent == "vision":
        confidence = _keyword_density_confidence(
            lower,
            _VISION_KEYWORDS,
            base=_INTENT_CONFIDENCE["vision"],
            step=0.03,
        )

    return (intent, max(0.0, min(1.0, confidence)))


# ── Intent → tier preference ──────────────────────────────────────────

# Ordered selectors per intent. The router walks each list and picks the
# first selector that resolves to a model present in MODEL_CHAIN AND
# passes the optional availability check.
_INTENT_PREFERENCES: dict[str, tuple[str, ...]] = {
    "capability_question": ("CroweLM Nano", "nano", "CroweLM Lite", "CroweLM Swift"),
    "arithmetic":          ("CroweLM Nano", "nano", "CroweLM Lite"),
    "trivial":             ("CroweLM Nano", "nano", "CroweLM Lite"),
    "ambiguous":           ("CroweLM Nano", "nano", "CroweLM Swift", "CroweLM Nexus"),
    "vision":              ("CroweLM Vision", "vision"),
    "code":                ("CroweLM Coder", "CroweLM Dev", "CroweLM Apex", "CroweLM Titan"),
    "domain":              ("CroweLM Apex", "CroweLM Titan", "CroweLM Sovereign", "CroweLM Prime"),
    "deep":                ("CroweLM Titan", "CroweLM Apex", "CroweLM Sovereign", "CroweLM Frontier"),
    "general":             ("CroweLM Nexus", "CroweLM Apex", "CroweLM Titan"),
}

# Companions for parallel fan-out. When a turn is dispatched in
# ``present_both`` or ``ensemble_synthesis`` fusion mode, these labels are
# added alongside the primary. Empty by default - fan-out is opt-in per call site.
_INTENT_COMPANIONS: dict[str, tuple[str, ...]] = {
    "domain": ("DeepParallel",),  # second opinion via local 8-chain
    "deep":   ("DeepParallel",),
}


# ── Route decision ────────────────────────────────────────────────────

@dataclass(frozen=True)
class RouteDecision:
    """Routing outcome: classified intent, primary + fallback chain, optional companions."""

    intent: str
    primary: dict                   # model_cfg from MODEL_CHAIN
    fallbacks: tuple[dict, ...]     # ordered same-turn fallbacks
    companions: tuple[dict, ...]    # parallel-dispatch companions (may be empty)
    reason: str
    confidence: float = 0.50

    @property
    def primary_label(self) -> str:
        return str(self.primary.get("label", self.primary.get("name", "?")))

    @property
    def selected_label(self) -> str:
        return self.primary_label

    @property
    def selected_name(self) -> str:
        return str(self.primary.get("name", ""))

    @property
    def selected_type(self) -> str:
        return str(self.primary.get("type", ""))

    @property
    def low_confidence(self) -> bool:
        return self.confidence < LOW_CONFIDENCE_THRESHOLD

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "primary": self.primary_label,
            "selected_label": self.selected_label,
            "selected_name": self.selected_name,
            "selected_type": self.selected_type,
            "fallbacks": [str(c.get("label", c.get("name", ""))) for c in self.fallbacks],
            "companions": [str(c.get("label", c.get("name", ""))) for c in self.companions],
            "confidence": self.confidence,
            "low_confidence": self.low_confidence,
            "reason": self.reason,
        }


def _maybe_apply_deepparallel_fallback(
    text: str,
    intent: str,
    confidence: float,
) -> tuple[str, float, str]:
    """Return possibly overridden classifier output plus a reason suffix."""
    if confidence >= LOW_CONFIDENCE_THRESHOLD:
        return (intent, confidence, "")

    try:
        from config import synapse_fallback
    except Exception:
        return (intent, confidence, "")

    if not synapse_fallback.fallback_enabled():
        return (intent, confidence, "")

    result = synapse_fallback.classify_with_deepparallel(text)
    if result is None:
        return (intent, confidence, "")

    fallback_intent, fallback_confidence = result
    if fallback_confidence <= confidence:
        return (intent, confidence, "")

    return (
        fallback_intent,
        fallback_confidence,
        (
            f"; DeepParallel override {intent}:{confidence:.2f} -> "
            f"{fallback_intent}:{fallback_confidence:.2f}"
        ),
    )


def _resolve_all(
    selectors: Iterable[str],
    chain: list[dict],
    availability: Callable[[dict], bool] | None,
) -> list[dict]:
    """Resolve every selector in order to a model in ``chain`` that passes availability."""
    seen: set[int] = set()
    out: list[dict] = []
    for selector in selectors:
        cfg = _resolve_in_chain(selector, chain)
        if not cfg or id(cfg) in seen:
            continue
        seen.add(id(cfg))
        if availability is not None and not availability(cfg):
            continue
        out.append(cfg)
    return out


def _selector_key(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _model_selectors(model_cfg: dict) -> list[str]:
    selectors = [model_cfg.get("name", ""), model_cfg.get("label", "")]
    selectors.extend(model_cfg.get("aliases", []))
    return [str(selector) for selector in selectors if selector]


def _resolve_in_chain(selector: str, chain: list[dict]) -> dict | None:
    needle = _selector_key(selector or "")
    if not needle:
        return None

    for model_cfg in chain:
        if any(_selector_key(candidate) == needle for candidate in _model_selectors(model_cfg)):
            return model_cfg

    for model_cfg in chain:
        if any(needle in _selector_key(candidate) for candidate in _model_selectors(model_cfg)):
            return model_cfg

    return None


def route_prompt(
    text: str,
    *,
    chain: list[dict] | None = None,
    availability: Callable[[dict], bool] | None = None,
) -> RouteDecision:
    """Classify ``text`` and return a :class:`RouteDecision` against ``chain``.

    ``availability(model_cfg) -> bool`` is consulted for every candidate; pass
    it when you want to skip provider tiers whose endpoints are unconfigured
    at runtime, so degraded environments still produce a route instead of
    falling all the way through to the chain head.
    """
    if chain is None:
        from config.agent_config import MODEL_CHAIN as active_chain
        chain = active_chain
    heuristic_intent, heuristic_confidence = classify_with_confidence(text)
    intent, confidence, fallback_reason = _maybe_apply_deepparallel_fallback(
        text,
        heuristic_intent,
        heuristic_confidence,
    )

    preferences = _INTENT_PREFERENCES.get(intent, ())
    backstop = ("CroweLM Nexus", "CroweLM Apex", "CroweLM Titan")
    available = _resolve_all((*preferences, *backstop), chain, availability)

    if not available:
        primary = next(
            (cfg for cfg in chain if cfg.get("provider") != "auto"),
            chain[0] if chain else {},
        )
        return RouteDecision(
            intent=intent,
            primary=primary,
            fallbacks=(),
            companions=(),
            reason=(
                f"intent={intent}; no preferred tier resolved; "
                f"fell back to chain head ({primary.get('label', '?')})"
                f"{fallback_reason}"
            ),
            confidence=confidence,
        )

    primary = available[0]
    fallbacks = tuple(available[1:4])  # keep up to 3 same-turn fallbacks

    companion_selectors = _INTENT_COMPANIONS.get(intent, ())
    companions = tuple(
        cfg for cfg in _resolve_all(companion_selectors, chain, availability)
        if id(cfg) != id(primary)
    )

    return RouteDecision(
        intent=intent,
        primary=primary,
        fallbacks=fallbacks,
        companions=companions,
        reason=(
            f"intent={intent}; primary={primary.get('label', '?')}; "
            f"{len(fallbacks)} fallback(s); {len(companions)} companion(s)"
            f"{fallback_reason}"
        ),
        confidence=confidence,
    )


__all__ = [
    "LOW_CONFIDENCE_THRESHOLD",
    "RouteDecision",
    "classify_prompt",
    "classify_with_confidence",
    "route_prompt",
]
