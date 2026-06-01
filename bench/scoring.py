"""Scorers for the benchmark harness.

Track A (this module's first scorers): deterministic exact/numeric matching.
Track B judge scoring is added in a later task.
"""

from __future__ import annotations

import re


def score_multiple_choice(answer: str, expected: str) -> float:
    """1.0 if the model selected the expected option letter, else 0.0.

    Prefers an explicit 'answer is X' / 'ANSWER: X' marker; otherwise falls
    back to the last standalone A-E letter mentioned.
    """
    upper = answer.upper()
    m = re.search(r"ANSWER\s*(?:IS|:)?\s*\(?([A-E])\)?", upper)
    if m:
        chosen = m.group(1)
    else:
        letters = re.findall(r"\b([A-E])\b", upper)
        if not letters:
            return 0.0
        chosen = letters[-1]
    return 1.0 if chosen == expected.strip().upper() else 0.0


def score_numeric(answer: str, expected: str) -> float:
    """1.0 if the expected number appears in the answer (comma/space tolerant)."""
    want = expected.replace(",", "").replace(" ", "").strip()
    nums = re.findall(r"-?\d[\d,]*(?:\.\d+)?", answer)
    normalized = {n.replace(",", "") for n in nums}
    return 1.0 if want in normalized else 0.0
