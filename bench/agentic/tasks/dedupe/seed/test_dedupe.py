from dedupe import dedupe


def test_preserves_order():
    assert dedupe([3, 1, 3, 2, 1]) == [3, 1, 2]


def test_no_dups():
    assert dedupe([1, 2, 3]) == [1, 2, 3]


def test_empty():
    assert dedupe([]) == []
