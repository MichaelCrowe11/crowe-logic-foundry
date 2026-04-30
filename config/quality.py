"""
Crowe Logic Foundry - Response-Quality Heuristics

Cheap, deterministic checks for "did the model actually answer?". Used by
the Synapse Router to:
- log shallow-response signals to telemetry,
- gate adaptive promotion (auto-retry on a higher tier when a fast-tier
  response looks weak - currently logged-only, opt-in execution behind a
  future env flag),
- power CI guards against silent quality regressions.

These are heuristics. False positives are acceptable; false negatives
(missing a real refusal or tautology) are the cost we minimize.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Phrases that signal the model declined or admitted inability. Kept
# tight to minimize false positives on legitimate uses ("I cannot find
# the file" is a legitimate observation, not a refusal).
_REFUSAL_PHRASES = (
    "i cannot help with",
    "i can't help with",
    "i'm unable to",
    "i am unable to",
    "i don't have the ability",
    "i do not have the ability",
    "as an ai",
    "i'm just an ai",
    "i'm not able to",
    "i am not able to",
    "i can't do that",
    "sorry, i can't",
)

# Boilerplate openers that often precede empty answers.
_HEDGE_OPENERS = (
    "great question",
    "that's a great question",
    "let me think",
    "interesting question",
)

_ECHO_QUESTION_RE = re.compile(r"^[A-Za-z][^?\.!]{0,80}\?\s*$")


@dataclass(frozen=True)
class QualitySignal:
    """A quality assessment of a single response.

    `shallow` is True when at least one weakness signal fires. `reasons`
    enumerates which ones, in order. Caller decides whether to act
    (auto-promote, log only, surface to user).
    """

    shallow: bool
    reasons: tuple[str, ...]
    length: int

    def to_dict(self) -> dict:
        return {
            "shallow": self.shallow,
            "reasons": list(self.reasons),
            "length": self.length,
        }


def assess_response(text: str, prompt: str | None = None, *, min_length: int = 24) -> QualitySignal:
    """Return a `QualitySignal` for the assistant response.

    `prompt` is optional; when supplied it powers tautology detection
    (response that echoes the user's question without answering it).

    Heuristics:
    1. **Empty / whitespace-only** - clearly shallow.
    2. **Below `min_length`** - too short to be a substantive answer.
    3. **Refusal phrase** - model declined.
    4. **Hedge-only** - message is just "Great question!" with nothing else.
    5. **Echoed-question** - short message that ends with "?" and looks
       like the model bouncing the question back.
    6. **Tautology** - response substring matches >70% of the prompt
       (the model rephrased the question instead of answering).
    """
    reasons: list[str] = []
    if text is None:
        return QualitySignal(shallow=True, reasons=("empty",), length=0)

    stripped = text.strip()
    length = len(stripped)

    if length == 0:
        return QualitySignal(shallow=True, reasons=("empty",), length=0)

    if length < min_length:
        reasons.append("too_short")

    lower = stripped.lower()

    if any(phrase in lower for phrase in _REFUSAL_PHRASES):
        reasons.append("refusal")

    # Pure hedge with no follow-through.
    if any(lower.startswith(opener) for opener in _HEDGE_OPENERS) and length < 80:
        reasons.append("hedge_only")

    if _ECHO_QUESTION_RE.match(stripped) and length < 100:
        reasons.append("echoed_question")

    if prompt:
        prompt_clean = prompt.strip().lower()
        if prompt_clean and len(prompt_clean) >= 10:
            # Naive overlap: how much of the prompt's character span
            # appears verbatim in the response, normalized by prompt
            # length. >70% means the response is mostly the prompt.
            overlap = _longest_common_substring_len(prompt_clean, lower)
            if overlap / len(prompt_clean) > 0.7 and length < len(prompt) * 1.3:
                reasons.append("tautology")

    return QualitySignal(
        shallow=bool(reasons),
        reasons=tuple(reasons),
        length=length,
    )


def _longest_common_substring_len(a: str, b: str) -> int:
    """Return the length of the longest common substring of `a` and `b`.

    O(n*m) DP. Fine for prompts up to a few thousand characters. The
    quality check runs once per turn, so the cost is negligible.
    """
    if not a or not b:
        return 0
    n, m = len(a), len(b)
    if n * m > 1_000_000:
        # Cheap guard: very large prompts skip tautology check.
        return 0
    prev = [0] * (m + 1)
    best = 0
    for i in range(1, n + 1):
        curr = [0] * (m + 1)
        for j in range(1, m + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
                if curr[j] > best:
                    best = curr[j]
        prev = curr
    return best


__all__ = ["QualitySignal", "assess_response"]
