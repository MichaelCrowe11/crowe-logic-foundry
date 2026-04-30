"""
ReasoningNarrationDetector: spot intent narration in reasoning streams.

The 2026-04-30 Talon transcript exhibited reasoning blocks like:

    "We need to interpret. We have just demonstrated parallel work. We can
    respond that yes... Let's invoke DeepParallel... We'll do both in parallel..."

The base system prompt forbids this kind of narration in OUTPUT, but reasoning
content bypasses the prompt's enforcement because reasoning is a separate
stream that doesn't shape future turns the same way. This detector fires when
the reasoning stream crosses a density threshold of narration phrases.

The detector does NOT modify reasoning text. It records events on the chain
so the agent loop can surface them, count them, and (with mid-stream interrupt
on) trigger a course-correction prompt.

Pattern philosophy: we look for FIRST-PERSON-PLURAL DELIBERATION ("we need to",
"we should", "let's", "we'll") and SELF-INSTRUCTING SECOND THOUGHTS ("wait,",
"actually,", "let me reconsider", "let me step back"). These are the verbal
fingerprints of a model that is thinking out loud instead of acting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# Each pattern is a tuple of (label, compiled regex). The regex is
# case-insensitive and word-bounded so we don't false-positive on "we're"
# inside a quoted string or a code identifier.
_NARRATION_SPECS: list[tuple[str, str]] = [
    ("first_person_plural", r"\b(we need to|we should|we can|we'll|we will|we could|we must|we have to|we want to|we'd|we are|we're going to)\b"),
    ("imperative_to_self", r"\b(let's|let me|let us|i'll|i should|i need to|i can|i could|i must|i have to|i want to|i'd)\b"),
    ("second_thought", r"\b(wait,|actually,|let me reconsider|let me step back|let me think|hmm,|hold on|on second thought|reconsidering)\b"),
    ("planning_phrase", r"\b(my plan|here's the plan|the plan is|first,? i'll|next,? i'll|then,? i'll|next,? we|then,? we|first,? we)\b"),
    ("hedge_to_action", r"\b(maybe i should|perhaps i should|maybe we|perhaps we|i think i|i think we)\b"),
]


_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (label, re.compile(pat, re.IGNORECASE)) for label, pat in _NARRATION_SPECS
]


@dataclass(frozen=True)
class NarrationHit:
    """A single narration phrase detected in reasoning."""
    label: str
    phrase: str
    start: int
    end: int


@dataclass(frozen=True)
class NarrationReport:
    """Density of narration in a chunk of reasoning text."""
    total_hits: int
    by_label: dict[str, int]
    chars_scanned: int
    hits_per_1k_chars: float
    samples: list[str]  # up to 5 short excerpts


class ReasoningNarrationDetector:
    """Detect intent-narration phrases in reasoning streams.

    Use mode:
        detector = ReasoningNarrationDetector()
        report = detector.scan(reasoning_text)
        if report.hits_per_1k_chars > 5:
            ... fire course-correct event ...
    """

    def __init__(self, patterns: list[tuple[str, re.Pattern[str]]] = _PATTERNS):
        self._patterns = patterns

    def scan(self, text: str) -> NarrationReport:
        if not text:
            return NarrationReport(
                total_hits=0,
                by_label={},
                chars_scanned=0,
                hits_per_1k_chars=0.0,
                samples=[],
            )

        by_label: dict[str, int] = {}
        samples: list[str] = []
        all_hits: list[NarrationHit] = []
        for label, pattern in self._patterns:
            for match in pattern.finditer(text):
                hit = NarrationHit(
                    label=label,
                    phrase=match.group(0),
                    start=match.start(),
                    end=match.end(),
                )
                all_hits.append(hit)
                by_label[label] = by_label.get(label, 0) + 1
                if len(samples) < 5:
                    samples.append(self._sample(text, match.start(), match.end()))

        total = len(all_hits)
        chars = len(text)
        per_1k = (total / chars * 1000) if chars else 0.0

        return NarrationReport(
            total_hits=total,
            by_label=by_label,
            chars_scanned=chars,
            hits_per_1k_chars=per_1k,
            samples=samples,
        )

    @staticmethod
    def _sample(text: str, start: int, end: int, radius: int = 32) -> str:
        s = max(0, start - radius)
        e = min(len(text), end + radius)
        snippet = text[s:e].replace("\n", " ")
        return snippet
