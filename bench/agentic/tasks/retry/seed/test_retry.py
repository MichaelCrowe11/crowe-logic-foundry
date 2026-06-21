import pytest

from retry import retry


def test_succeeds_after_failures():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("not yet")
        return "ok"

    assert retry(flaky, attempts=3) == "ok"
    assert calls["n"] == 3


def test_reraises_after_exhausting():
    def always():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        retry(always, attempts=2)
