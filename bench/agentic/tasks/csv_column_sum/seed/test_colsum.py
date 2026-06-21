import pytest

from colsum import column_sum


def test_skips_header():
    rows = [["name", "qty"], ["a", "2"], ["b", "5"]]
    assert column_sum(rows, 1) == 7


def test_single_data_row():
    rows = [["x", "y"], ["a", "10"]]
    assert column_sum(rows, 1) == 10
