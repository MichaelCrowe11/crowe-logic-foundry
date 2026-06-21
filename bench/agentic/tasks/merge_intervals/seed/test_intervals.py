from intervals import merge


def test_merges_overlap():
    assert merge([[1, 3], [2, 6], [8, 10]]) == [[1, 6], [8, 10]]


def test_touching_merge():
    assert merge([[1, 4], [4, 5]]) == [[1, 5]]


def test_unsorted_input():
    assert merge([[8, 10], [1, 3], [2, 6]]) == [[1, 6], [8, 10]]
