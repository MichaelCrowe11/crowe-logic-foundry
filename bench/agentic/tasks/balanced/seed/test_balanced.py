from balanced import is_balanced


def test_matched_types():
    assert is_balanced("([]{})") is True


def test_wrong_order():
    assert is_balanced("([)]") is False


def test_unclosed():
    assert is_balanced("(((") is False


def test_empty():
    assert is_balanced("") is True
