from pathlib import Path

from bench.agentic.agents.base import AgentResult
from bench.agentic.score import aggregate, append_row, make_row
from bench.agentic.verify import VerifyResult


def _res(self_verified=True, rounds=2):
    return AgentResult(
        workdir=Path("/w"),
        transcript=[],
        rounds=rounds,
        tool_calls=3,
        wall_s=1.0,
        tokens=10,
        cost_usd=0.0,
        self_verified=self_verified,
        error=None,
    )


def _ok():
    return VerifyResult(passed=True, exit_code=0, timed_out=False, output="ok")


def _fail():
    return VerifyResult(passed=False, exit_code=1, timed_out=False, output="bad")


def test_make_row_shape():
    row = make_row("t1", "stub", "opus", _res(), _ok())
    assert (
        row["task_id"] == "t1"
        and row["passed"] is True
        and row["self_verified"] is True
    )


def test_append_row_is_appending(tmp_path):
    p = tmp_path / "raw.jsonl"
    append_row(p, make_row("t1", "a", "m", _res(), _ok()))
    append_row(p, make_row("t2", "a", "m", _res(), _fail()))
    assert len(p.read_text().splitlines()) == 2


def test_aggregate_pass_at_1():
    rows = [
        make_row("t1", "crowe", "opus", _res(), _ok()),
        make_row("t2", "crowe", "opus", _res(), _fail()),
        make_row("t1", "ref", "opus", _res(), _ok()),
    ]
    agg = aggregate(rows)
    assert agg["crowe"]["pass_at_1"] == 0.5
    assert agg["ref"]["pass_at_1"] == 1.0
    assert agg["crowe"]["n"] == 2
