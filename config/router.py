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
