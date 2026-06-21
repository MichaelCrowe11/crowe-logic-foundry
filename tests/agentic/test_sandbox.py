from pathlib import Path

import pytest

from bench.agentic.sandbox import sandbox


def _seed(tmp_path: Path) -> Path:
    s = tmp_path / "seed"
    s.mkdir()
    (s / "a.txt").write_text("original")
    return s


def test_sandbox_copies_seed(tmp_path):
    seed = _seed(tmp_path)
    with sandbox(seed) as work:
        assert (work / "a.txt").read_text() == "original"


def test_mutating_workdir_never_touches_seed(tmp_path):
    seed = _seed(tmp_path)
    with sandbox(seed) as work:
        (work / "a.txt").write_text("mutated")
        (work / "new.txt").write_text("added")
    assert (seed / "a.txt").read_text() == "original"
    assert not (seed / "new.txt").exists()


def test_sandbox_cleans_up_on_exception(tmp_path):
    seed = _seed(tmp_path)
    captured = {}
    with pytest.raises(RuntimeError):
        with sandbox(seed) as work:
            captured["work"] = work
            raise RuntimeError("boom")
    assert not captured["work"].exists()


def test_missing_seed_raises(tmp_path):
    with pytest.raises(ValueError):
        with sandbox(tmp_path / "nope"):
            pass
