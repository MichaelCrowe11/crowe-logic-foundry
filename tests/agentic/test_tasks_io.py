import json
from pathlib import Path

import pytest

from bench.agentic.tasks_io import load_tasks


def _make_task(root: Path, tid: str, meta: dict):
    d = root / tid
    (d / "seed").mkdir(parents=True)
    (d / "seed" / "code.py").write_text("x = 1\n")
    (d / "prompt.txt").write_text("do the thing")
    (d / "verify.sh").write_text("#!/bin/sh\nexit 0\n")
    (d / "meta.json").write_text(json.dumps(meta))


_GOOD = {
    "lang": "python",
    "difficulty": "easy",
    "tags": ["fix"],
    "timeout_s": 60,
    "max_rounds": 20,
}


def test_load_valid_task(tmp_path):
    _make_task(tmp_path, "t1", _GOOD)
    tasks = load_tasks(tmp_path)
    assert len(tasks) == 1
    assert tasks[0].task_id == "t1"
    assert tasks[0].prompt == "do the thing"
    assert tasks[0].meta.max_rounds == 20


def test_malformed_meta_rejected(tmp_path):
    bad = dict(_GOOD)
    del bad["timeout_s"]
    _make_task(tmp_path, "t1", bad)
    with pytest.raises(ValueError, match="missing keys"):
        load_tasks(tmp_path)


def test_tasks_loaded_in_sorted_order(tmp_path):
    _make_task(tmp_path, "b", _GOOD)
    _make_task(tmp_path, "a", _GOOD)
    assert [t.task_id for t in load_tasks(tmp_path)] == ["a", "b"]
