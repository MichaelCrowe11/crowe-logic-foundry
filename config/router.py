"""
Crowe Logic Foundry - Complexity Router

Classifies user prompts and routes them to the right CroweLM tier.

Design notes
------------
- Heuristic-only by default. No extra LLM call before the user's request.
  An LLM-based router would itself need ~500ms TTFB plus a model decision,
  which negates the savings on the 80% of prompts that have an obvious shape.
- The classifier is conservative: when in doubt, escalate to the highest
  tier that fits. The cost of routing a domain prompt to Nano is much
  higher than the cost of routing a trivial prompt to a reasoning tier.
- Output is structured (`RouteDecision`) so call sites can log it,
  display it, or override it.

Intent ladder (cheapest first):
    capability_question     "can you...", "do you...", "is there..."
    arithmetic              digits + math operators, no prose
    trivial                 short greetings, yes/no, ack
    ambiguous               very short prompts that aren't clearly one thing
    code                    "write/refactor/fix" + code-shaped tokens
    vision                  references to images, screenshots, photos
    domain                  mycology, biotech, drug discovery, agronomy keywords
    general                 default reasoning prompts
    deep                    explicit asks for analysis/strategy/architecture
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Iterable

from config.agent_config import MODEL_CHAIN, resolve_model_config

# ─────────────────────────────────────────────────────────────────────
# Intent classification
# ─────────────────────────────────────────────────────────────────────

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
    "refactor", "rewrite", "implement", "function", "class ", "method",
    "bug", "stack trace", "compile", "lint", "test ", "unit test",
    "regex", "snippet", "endpoint", "schema", "migration", "diff ",
)
_CODE_FENCES = re.compile(r"```|^\s*(def |class |function |const |let |var )", re.MULTILINE)

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


def _matches_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(p, text) for p in patterns)


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(k in text for k in keywords)


def classify_prompt(text: str) -> str:
    """Return a single intent label for the prompt (no confidence)."""
    intent, _ = classify_with_confidence(text)
    return intent


def classify_with_confidence(text: str) -> tuple[str, float]:
    """Return (intent, confidence in [0, 1]).

    Confidence is calibrated per signal:
    - Regex/exact-match intents (arithmetic, trivial) score near 1.0.
    - Keyword-density intents (domain) score higher when multiple
      keywords fire than when only one does.
    - Length-gated intents (capability_question) lose confidence as the
      prompt gets longer (long capability questions are often disguised
      domain prompts).
    - Default fallthroughs (general, ambiguous) carry intrinsically low
      confidence so the auto-router knows to escalate or surface.
    """
    if not text or not text.strip():
        return ("ambiguous", _INTENT_CONFIDENCE["ambiguous"])

    raw = text.strip()
    lower = raw.lower()

    if _ARITHMETIC_PATTERN.match(raw) or _ARITHMETIC_INLINE_PATTERN.match(raw):
        return ("arithmetic", _INTENT_CONFIDENCE["arithmetic"])

    if lower in _TRIVIAL_PHRASES:
        return ("trivial", _INTENT_CONFIDENCE["trivial"])
    if len(lower) <= 4 and lower.isalpha():
        return ("trivial", 0.85)  # short alpha words less certain than exact matches

    if _matches_any(lower, _CAPABILITY_PATTERNS) and len(raw) < 200:
        # Short capability questions are highly confident; longer ones drift
        # toward "this might really be a domain ask in disguise."
        if len(raw) < 80:
            return ("capability_question", _INTENT_CONFIDENCE["capability_question"])
        return ("capability_question", 0.65)

    if _contains_any(lower, _VISION_KEYWORDS):
        return ("vision", _INTENT_CONFIDENCE["vision"])

    if _CODE_FENCES.search(raw):
        return ("code", _INTENT_CONFIDENCE["code"])  # fenced code is strong signal
    if _contains_any(lower, _CODE_KEYWORDS):
        return ("code", 0.72)  # keyword-only is weaker than fence

    domain_hits = sum(1 for k in _DOMAIN_KEYWORDS if k in lower)
    if domain_hits >= 2:
        return ("domain", _INTENT_CONFIDENCE["domain"])
    if domain_hits == 1:
        return ("domain", 0.62)

    if _contains_any(lower, _DEEP_KEYWORDS):
        return ("deep", _INTENT_CONFIDENCE["deep"])

    if len(raw) < 25:
        return ("ambiguous", _INTENT_CONFIDENCE["ambiguous"])

    return ("general", _INTENT_CONFIDENCE["general"])


# ─────────────────────────────────────────────────────────────────────
# Intent → tier mapping
# ─────────────────────────────────────────────────────────────────────

# Maps intent -> ordered list of preferred selectors (label or alias).
# Router walks this list and picks the first selector that resolves to a
# model present in MODEL_CHAIN (so missing-model environments degrade
# gracefully to whatever is configured).
_INTENT_PREFERENCES: dict[str, tuple[str, ...]] = {
    "capability_question": ("CroweLM Nano", "nano", "CroweLM Lite", "CroweLM Swift"),
    "arithmetic":          ("CroweLM Nano", "nano", "CroweLM Lite"),
    "trivial":             ("CroweLM Nano", "nano", "CroweLM Lite"),
    "ambiguous":           ("CroweLM Nano", "nano", "CroweLM Swift", "CroweLM Nexus"),
    "vision":              ("CroweLM Vision", "vision", "talon-vision"),
    "code":                ("CroweLM Coder", "CroweLM Dev", "CroweLM Apex", "CroweLM Titan"),
    "domain":              ("CroweLM Apex", "CroweLM Titan", "CroweLM Sovereign", "CroweLM Prime"),
    "deep":                ("CroweLM Titan", "CroweLM Apex", "CroweLM Sovereign", "CroweLM Frontier"),
    "general":             ("CroweLM Nexus", "CroweLM Apex", "CroweLM Titan"),
}


# ─────────────────────────────────────────────────────────────────────
# Route decision
# ─────────────────────────────────────────────────────────────────────

# Per-intent confidence ceilings. The classifier returns the highest score
# in this table for which all signals fired. Lower-confidence intents
# represent ambiguous inputs the router should flag for promotion review.
_INTENT_CONFIDENCE: dict[str, float] = {
    "arithmetic":          0.99,  # regex unambiguous
    "trivial":             0.98,  # exact phrase match
    "vision":              0.92,  # explicit image keyword
    "capability_question": 0.85,  # pattern + length cap
    "code":                0.85,  # code fence or strong keyword
    "domain":              0.80,  # domain keyword present
    "deep":                0.78,  # explicit "architecture/strategy"
    "general":             0.65,  # default for medium prompts
    "ambiguous":           0.40,  # short, unclear shape
}

# Confidence threshold below which the router decision is logged as a
# low-confidence dispatch. Used by adaptive-promotion logic and by the
# auto-route badge to highlight uncertain routes.
LOW_CONFIDENCE_THRESHOLD: float = 0.60


@dataclass(frozen=True)
class RouteDecision:
    """A routing outcome: which model, with calibrated confidence."""

    intent: str
    selected_label: str
    selected_name: str
    selected_type: str
    reason: str
    confidence: float = 0.0

    @property
    def low_confidence(self) -> bool:
        return self.confidence < LOW_CONFIDENCE_THRESHOLD

    def to_dict(self) -> dict:
        return asdict(self)


def _first_resolvable(selectors: Iterable[str], chain: list[dict]) -> dict | None:
    """Find the first selector that resolves to a model in the given chain."""
    chain_keys: dict[str, dict] = {}
    for cfg in chain:
        for sel in (cfg.get("name", ""), cfg.get("label", ""), *cfg.get("aliases", [])):
            if sel:
                chain_keys.setdefault(sel.lower(), cfg)

    for selector in selectors:
        cfg = chain_keys.get(selector.lower())
        if cfg:
            return cfg
        # Fall back to the registry resolver, which handles
        # normalized-key matching (strips punctuation, etc.).
        cfg = resolve_model_config(selector)
        if cfg and any(cfg is c for c in chain):
            return cfg
    return None


def route_prompt(text: str, chain: list[dict] | None = None) -> RouteDecision:
    """Classify `text` and return the best model in `chain` for it.

    Falls back to the first model in the chain if no preference resolves
    (e.g., a stripped-down deployment that has only one tier configured).

    When CROWE_LOGIC_SYNAPSE_FALLBACK=1 and the heuristic confidence is
    below LOW_CONFIDENCE_THRESHOLD, the prompt is re-classified by
    DeepParallel (a local multi-chain reasoning model on Ollama). The
    fallback never raises; on any failure the heuristic decision is
    kept.
    """
    chain = chain if chain is not None else MODEL_CHAIN
    intent, confidence = classify_with_confidence(text)
    fallback_used = False

    if confidence < LOW_CONFIDENCE_THRESHOLD:
        from config.synapse_fallback import classify_with_deepparallel, fallback_enabled
        if fallback_enabled():
            second_opinion = classify_with_deepparallel(text)
            if second_opinion is not None:
                dp_intent, dp_conf = second_opinion
                # Only override when the fallback returns higher confidence
                # AND a recognized intent. This prevents a low-quality
                # second opinion from making routing worse.
                if dp_intent in _INTENT_PREFERENCES and dp_conf > confidence:
                    intent, confidence = dp_intent, dp_conf
                    fallback_used = True

    selectors = _INTENT_PREFERENCES.get(intent, ()) + (
        # Backstop: any reasoning tier, then the first chain entry.
        "CroweLM Nexus", "CroweLM Apex", "CroweLM Titan",
    )

    chosen = _first_resolvable(selectors, chain)
    fallback_tag = " [via DeepParallel]" if fallback_used else ""
    if chosen is None:
        chosen = chain[0]
        reason = (
            f"intent={intent} (conf={confidence:.2f}){fallback_tag}; "
            f"no preferred selector resolved in chain; falling back to first entry."
        )
    else:
        reason = (
            f"intent={intent} (conf={confidence:.2f}){fallback_tag}; matched "
            f"preference list to {chosen.get('label', chosen.get('name', '?'))}."
        )

    return RouteDecision(
        intent=intent,
        selected_label=str(chosen.get("label", "")),
        selected_name=str(chosen.get("name", "")),
        selected_type=str(chosen.get("type", "")),
        reason=reason,
        confidence=confidence,
    )


__all__ = [
    "LOW_CONFIDENCE_THRESHOLD",
    "RouteDecision",
    "classify_prompt",
    "classify_with_confidence",
    "route_prompt",
]
