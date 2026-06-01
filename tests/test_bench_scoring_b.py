import json

from bench.scoring import build_judge_prompt, parse_judge_score, score_results_file


def test_parse_judge_extracts_score():
    assert parse_judge_score("Reasoning... SCORE: 4") == 4
    assert parse_judge_score("score = 0 — wrong") == 0
    assert parse_judge_score("SCORE:5") == 5


def test_parse_judge_falls_back_to_bare_digit():
    assert parse_judge_score("I'd give this a 3 out of 5") == 3


def test_parse_judge_none_when_no_number():
    assert parse_judge_score("no number here") is None


def test_parse_judge_clamps_to_0_5_range():
    # a stray 9 is not a valid score; only 0-5 are accepted
    assert parse_judge_score("the year 1999") is None


def test_parse_judge_ignores_fraction_denominator():
    # "N/5" phrasing must not let the denominator 5 inflate the score via the
    # bare-digit fallback; the explicit SCORE: marker still wins.
    assert parse_judge_score("I rate it 4/5") == 4
    assert parse_judge_score("SCORE: 2 (that's 2/5)") == 2
    # a pure ratio with no standalone 0-5 digit yields None, not the denominator
    assert parse_judge_score("ratio 9/5") is None


def test_empty_answer_is_not_judged_and_scores_none(tmp_path):
    """A blank answer (silent failure, no error field) carries no signal. It
    must NOT be sent to the judge, and must score None so the scoreboard
    excludes it — otherwise a dead tier surfaces as a real-looking 0.00."""
    raw = tmp_path / "raw.jsonl"
    scored = tmp_path / "scored.jsonl"
    raw.write_text(
        json.dumps(
            {
                "track": "b",
                "condition": "grounded",
                "tier": "gpt-5.4",
                "question_id": "m1",
                "question": "Q?",
                "answer": "   ",  # whitespace-only: produced nothing
            }
        )
    )
    calls = []

    def judge(prompt):
        calls.append(prompt)
        return "SCORE: 0"

    score_results_file(raw, scored, judge=judge)
    out = [json.loads(line) for line in scored.read_text().splitlines() if line.strip()]
    assert out[0]["score"] is None
    assert calls == [], "empty answer must not reach the judge"


def test_build_judge_prompt_includes_all_parts():
    p = build_judge_prompt(question="Q?", source_passage="SRC", answer="ANS")
    assert "Q?" in p and "SRC" in p and "ANS" in p
    assert "0" in p and "5" in p  # rubric range stated
