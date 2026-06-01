from bench.scoring import build_judge_prompt, parse_judge_score


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


def test_build_judge_prompt_includes_all_parts():
    p = build_judge_prompt(question="Q?", source_passage="SRC", answer="ANS")
    assert "Q?" in p and "SRC" in p and "ANS" in p
    assert "0" in p and "5" in p  # rubric range stated
