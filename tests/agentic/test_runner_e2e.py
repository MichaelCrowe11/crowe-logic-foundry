import json
from pathlib import Path

from bench.agentic.agents.stub import StubRunner
from bench.agentic.runner import run_suite

FIX = Path("bench/agentic/tasks/_fixtures")


def _rows(out: Path):
    return [json.loads(line) for line in (out / "raw.jsonl").read_text().splitlines()]


def test_e2e_green_and_red(tmp_path):
    # A runner that fixes the bug (subtract -> add) passes both fixtures;
    # a no-op runner passes pass_trivial and fails fail_trivial.
    def fix(work: Path):
        (work / "code.py").write_text("def add(a, b):\n    return a + b\n")

    fixer = StubRunner(name="reference", mutate=fix, self_verified=True)
    noop = StubRunner(name="crowe-logic", mutate=None)

    out = run_suite(FIX, [fixer, noop], tmp_path, model="stub")
    by = {(r["task_id"], r["agent"]): r["passed"] for r in _rows(out)}

    assert by[("pass_trivial", "reference")] is True
    assert by[("fail_trivial", "reference")] is True
    assert by[("pass_trivial", "crowe-logic")] is True
    assert by[("fail_trivial", "crowe-logic")] is False  # no-op leaves bug

    sb = (out / "scoreboard.md").read_text()
    assert "pass@1" in sb and "gap" in sb.lower()


def test_crashing_runner_is_recorded_not_raised(tmp_path):
    class Boom:
        name = "boom"

        def run(self, **kw):
            raise RuntimeError("kaboom")

    out = run_suite(FIX, [Boom()], tmp_path, model="stub")
    rows = _rows(out)
    assert all(r["passed"] is False for r in rows)
    assert any("kaboom" in (r["error"] or "") for r in rows)
