from wrap import wrap


def test_wraps_on_word_boundaries():
    assert wrap("the quick brown fox", 10) == ["the quick", "brown fox"]


def test_no_word_split():
    words = "alpha beta gamma delta".split()
    lines = wrap("alpha beta gamma delta", 11)
    for line in lines:
        assert len(line) <= 11
        # every token on a line must be a whole original word (never split)
        for tok in line.split():
            assert tok in words


def test_single_long_run():
    assert wrap("one two three", 13) == ["one two three"]
