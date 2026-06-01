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
    0-5 digit. Numbers outside 0-5 are not treated as scores, and digits that
    are part of a fraction/ratio (e.g. "9/5", "5/10") are ignored so a
    "rate it N/5" phrasing can't inflate the score via its denominator.
    """
    m = re.search(r"SCORE\s*[:=]?\s*([0-5])\b", judge_text.upper())
    if m:
        return int(m.group(1))
    # Fallback: a 0-5 digit that is NOT a fraction denominator (not preceded by
    # '/') and not part of a larger number. So "4/5" -> 4 (numerator is the
    # score), "9/5" -> None (9 is out of range, 5 is a denominator), "1999" -> None.
    m2 = re.search(r"(?<![\d/])([0-5])(?!\d)", judge_text)
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
                tests = row.get("tests", "")
                row["score"] = (
                    score_code(row.get("answer", ""), tests) if tests else None
                )
            else:
                row["score"] = None
        elif row.get("track") == "b":
            answer = row.get("answer", "") or ""
            if not answer.strip():
                # A blank answer (silent failure with no error field) carries no
                # signal. Don't ask the judge to grade "" — that scores 0 and
                # makes a dead tier surface as a real-looking 0.00. None == no data.
                row["score"] = None
            else:
                prompt = build_judge_prompt(
                    question=row.get("question", ""),
                    source_passage=row.get("source_passage", ""),
                    answer=answer,
                )
                row["score"] = parse_judge_score(judge(prompt))
        else:
            row["score"] = None
        out_lines.append(json.dumps(row))
    scored_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def score_code(answer: str, tests: str, *, timeout: int = 10) -> float:
    """Run candidate code + assertion tests in a subprocess; 1.0 if all pass.

    The answer (a function definition) and the tests (assert statements) are
    concatenated into one temp module and executed in a fresh subprocess so a
    crash, infinite loop (via timeout), or bad code cannot affect the harness.
    """
    import os
    import subprocess
    import sys
    import tempfile
    import textwrap

    program = textwrap.dedent(answer) + "\n" + textwrap.dedent(tests) + "\n"
    fd, tmp = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(program)
        proc = subprocess.run(
            [sys.executable, tmp], capture_output=True, text=True, timeout=timeout
        )
        return 1.0 if proc.returncode == 0 else 0.0
    except subprocess.TimeoutExpired:
        return 0.0
    finally:
        os.unlink(tmp)
