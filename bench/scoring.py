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


def build_judge_prompt(*, question: str, source_passage: str, answer: str) -> str:
    """Prompt a judge model to grade an answer against a source passage (0-5)."""
    return (
        "You are grading an answer for factual alignment with a source passage.\n"
        "Score 0-5 (0 = contradicts or irrelevant, 5 = fully correct and grounded).\n"
        "Judge ONLY against the source passage; do not use outside knowledge.\n\n"
        f"QUESTION:\n{question}\n\n"
        f"SOURCE PASSAGE (ground truth):\n{source_passage}\n\n"
        f"ANSWER TO GRADE:\n{answer}\n\n"
        "Respond with one line: SCORE: <0-5>"
    )


def parse_judge_score(judge_text: str) -> int | None:
    """Extract a 0-5 integer score from judge output, or None if absent.

    Prefers an explicit 'SCORE: N' marker; otherwise the first standalone
    0-5 digit. Numbers outside 0-5 are not treated as scores.
    """
    m = re.search(r"SCORE\s*[:=]?\s*([0-5])\b", judge_text.upper())
    if m:
        return int(m.group(1))
    m2 = re.search(r"\b([0-5])\b", judge_text)
    return int(m2.group(1)) if m2 else None
