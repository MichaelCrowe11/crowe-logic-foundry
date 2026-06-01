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


def score_results_file(raw_path, scored_path, *, judge=None) -> None:
    """Read raw.jsonl, attach a `score` to each row, write scored.jsonl.

    Track A rows scored deterministically by qtype. Track B rows scored by a
    judge callable ``judge(prompt:str)->str`` (defaults to a live JUDGE_TIER
    call via run_headless). Rows with an error get score=None.
    """
    import json
    from pathlib import Path

    if judge is None:
        from bench import config
        from bench.headless_client import run_headless

        def judge(prompt):
            return run_headless(prompt, config.JUDGE_TIER, tools=False).answer

    raw_path, scored_path = Path(raw_path), Path(scored_path)
    out_lines = []
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("error"):
            row["score"] = None
        elif row.get("track") == "a":
            qtype = row.get("qtype", "")
            ans = row.get("answer", "")
            exp = row.get("expected", "")
            if qtype == "multiple_choice":
                row["score"] = score_multiple_choice(ans, exp)
            elif qtype == "numeric":
                row["score"] = score_numeric(ans, exp)
            elif qtype == "code":
                row["score"] = None  # code scoring handled in a later task
            else:
                row["score"] = None
        elif row.get("track") == "b":
            prompt = build_judge_prompt(
                question=row.get("question", ""),
                source_passage=row.get("source_passage", ""),
                answer=row.get("answer", ""),
            )
            row["score"] = parse_judge_score(judge(prompt))
        else:
            row["score"] = None
        out_lines.append(json.dumps(row))
    scored_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
