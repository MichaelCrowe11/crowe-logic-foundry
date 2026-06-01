from bench.scoring import score_multiple_choice, score_numeric


def test_multiple_choice_answer_is_pattern():
    assert score_multiple_choice("The answer is (B).", "B") == 1.0
    assert score_multiple_choice("ANSWER: C", "C") == 1.0
    assert score_multiple_choice("A", "B") == 0.0


def test_multiple_choice_falls_back_to_last_letter():
    # no explicit "answer is" — use the last standalone option letter mentioned
    assert score_multiple_choice("I think it is D", "D") == 1.0


def test_multiple_choice_no_letter_is_zero():
    assert score_multiple_choice("I don't know", "B") == 0.0


def test_numeric_matches_with_formatting():
    assert score_numeric("The result is 42.", "42") == 1.0
    assert score_numeric("about 3,000 units", "3000") == 1.0
    assert score_numeric("7", "8") == 0.0


def test_numeric_no_number_is_zero():
    assert score_numeric("no digits here", "5") == 0.0
