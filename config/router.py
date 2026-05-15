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

from config.agent_config import MODEL_CHAIN, resolve_model_config


# ── Confidence threshold ─────────────────────────────────────────────

LOW_CONFIDENCE_THRESHOLD: float = 0.55
"""Prompts classified below this confidence are candidates for the
Synapse Phase 2 DeepParallel fallback (when env flag is on)."""


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


def _matches_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(p, text) for p in patterns)


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(k in text for k in keywords)


# Per-intent confidence ceilings. The classifier returns the highest score
# in this table for which all signals fired. Lower-confidence intents
# represent ambiguous inputs the router should flag for promotion review.
_INTENT_CONFIDENCE: dict[str, float] = {
    "arithmetic": 0.99,           # regex unambiguous
    "trivial": 0.98,              # exact phrase match
    "vision": 0.92,               # explicit image keyword
    "capability_question": 0.85,  # pattern + length cap
    "code": 0.85,                 # code fence or strong keyword
    "domain": 0.80,               # domain keyword present
    "deep": 0.78,                 # explicit "architecture/strategy"
    "general": 0.65,              # default for medium prompts
    "ambiguous": 0.40,            # short, unclear shape
}

# Confidence threshold below which the router decision is logged as a
# low-confidence dispatch. Used by adaptive-promotion logic and by the
# auto-route badge to highlight uncertain routes.
LOW_CONFIDENCE_THRESHOLD: float = 0.60


def classify_prompt(text: str) -> str:
    """Return a single intent label for ``text``. Order: specific → general."""
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
    # Pad with spaces so space-bounded keywords (e.g. " test ") match at word
    # boundaries rather than across them ("la[test ]" must not classify as code).
    padded = f" {lower} "

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

    if _contains_any(padded, _VISION_KEYWORDS):
        return ("vision", _INTENT_CONFIDENCE["vision"])

    if _CODE_FENCES.search(raw):
        return ("code", _INTENT_CONFIDENCE["code"])  # fenced code is strong signal
    if _contains_any(padded, _CODE_KEYWORDS):
        return ("code", 0.72)  # keyword-only is weaker than fence

    domain_hits = sum(1 for k in _DOMAIN_KEYWORDS if k in lower)
    if domain_hits >= 2:
        return ("domain", _INTENT_CONFIDENCE["domain"])
    if domain_hits == 1:
        return ("domain", 0.62)

    if _contains_any(padded, _DEEP_KEYWORDS):
        return ("deep", _INTENT_CONFIDENCE["deep"])

    if len(raw) < 25:
        return ("ambiguous", _INTENT_CONFIDENCE["ambiguous"])

    return ("general", _INTENT_CONFIDENCE["general"])


# ── Confidence scoring ────────────────────────────────────────────────

# Base confidence per intent. More specific intents get higher base
# confidence because their pattern match is more discriminating.
_INTENT_BASE_CONFIDENCE: dict[str, float] = {
    "arithmetic": 0.97,
    "trivial": 0.95,
    "capability_question": 0.85,
    "vision": 0.90,
    "code": 0.88,
    "domain": 0.80,
    "deep": 0.82,
    "ambiguous": 0.30,
    "general": 0.60,
}

# Bonus per distinct keyword group matched (domain and code prompts only).
_DOMAIN_KEYWORD_GROUPS = (
    ("mushroom", "mycology", "myceli", "fruiting", "substrate", "spawn",
     "lion's mane", "lions mane", "oyster", "shiitake", "reishi",
     "grow log", "grow tent", "incubation", "pinhead", "primordia"),
    ("psilocybin", "psilocin", "tryptamine", "serotonin receptor",
     "drug discovery", "lead optimization", "ic50", "binding affinity",
     "docking", "smiles", "compound", "scaffold"),
    ("assay", "ph", "agar", "sterilization", "autoclave", "petri",
     "fermentation", "extract", "potency"),
)

_CODE_KEYWORD_GROUPS = (
    (" refactor", " rewrite", " implement", " function ", " class ", " method "),
    (" bug ", " stack trace", " compile", " lint", " test ", " unit test"),
    (" regex", " snippet", " endpoint", " schema", " migration", " diff "),
)


def _count_keyword_groups(text: str, groups: tuple[tuple[str, ...], ...]) -> int:
    """Count how many distinct keyword groups contain at least one match."""
    count = 0
    lower = text.lower()
    padded = f" {lower} "
    for group in groups:
        if any(k in padded for k in group):
            count += 1
    return count


def classify_with_confidence(text: str) -> tuple[str, float]:
    """Classify ``text`` and return ``(intent, confidence)``.

    Confidence is a heuristic score in [0.0, 1.0]:
    - Specific pattern matches (arithmetic, trivial) start near 1.0.
    - Keyword-driven intents (domain, code) scale with keyword density.
    - Ambiguous prompts score low (below LOW_CONFIDENCE_THRESHOLD).
    - General prompts score moderate (~0.60).
    """
    if not text or not text.strip():
        return ("ambiguous", 0.10)

    intent = classify_prompt(text)
    base = _INTENT_BASE_CONFIDENCE.get(intent, 0.50)

    # Boost domain confidence for keyword density.
    if intent == "domain":
        matched = _count_keyword_groups(text, _DOMAIN_KEYWORD_GROUPS)
        bonus = min(matched * 0.05, 0.15)
        base = min(base + bonus, 0.97)

    # Boost code confidence for keyword density and fence presence.
    if intent == "code":
        matched = _count_keyword_groups(text, _CODE_KEYWORD_GROUPS)
        bonus = min(matched * 0.04, 0.10)
        if _CODE_FENCES.search(text):
            bonus += 0.05
        base = min(base + bonus, 0.97)

    # Capability questions with short prompts are more certain.
    if intent == "capability_question" and len(text.strip()) < 50:
        base = min(base + 0.08, 0.97)

    # Long capability questions that contain domain keywords are less
    # certain as capability questions (they might really be domain prompts).
    if intent == "capability_question" and len(text.strip()) >= 200:
        base = max(base - 0.20, 0.40)

    return (intent, round(base, 3))


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

    @property
    def primary_label(self) -> str:
        return str(self.primary.get("label", self.primary.get("name", "?")))

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "primary": self.primary_label,
            "fallbacks": [str(c.get("label", c.get("name", ""))) for c in self.fallbacks],
            "companions": [str(c.get("label", c.get("name", ""))) for c in self.companions],
            "reason": self.reason,
        }


def _resolve_all(
    selectors: Iterable[str],
    chain: list[dict],
    availability: Callable[[dict], bool] | None,
) -> list[dict]:
    """Resolve every selector in order to a model in ``chain`` that passes availability."""
    chain_ids = {id(cfg) for cfg in chain}
    seen: set[int] = set()
    out: list[dict] = []
    for selector in selectors:
        cfg = resolve_model_config(selector)
        if not cfg or id(cfg) in seen:
            continue
        if id(cfg) not in chain_ids:
            continue
        seen.add(id(cfg))
        if availability is not None and not availability(cfg):
            continue
        out.append(cfg)
    return out


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
    chain = chain if chain is not None else MODEL_CHAIN
    intent = classify_prompt(text)

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
            ),
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
        ),
    )


__all__ = [
    "RouteDecision",
    "classify_prompt",
    "route_prompt",
]
