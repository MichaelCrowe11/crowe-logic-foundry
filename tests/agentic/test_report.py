from pathlib import Path

from bench.agentic.agents.base import AgentResult
from bench.agentic.report import render_scoreboard
from bench.agentic.score import make_row
from bench.agentic.verify import VerifyResult


def _row(task, agent, passed):
    res = AgentResult(
        workdir=Path("/w"),
        transcript=[],
        rounds=2,
        tool_calls=1,
        wall_s=1.0,
        tokens=1,
        cost_usd=0.0,
        self_verified=passed,
        error=None,
    )
    vr = VerifyResult(
        passed=passed, exit_code=0 if passed else 1, timed_out=False, output=""
    )
    return make_row(task, agent, "opus", res, vr)


def test_scoreboard_has_gap_and_matrix():
    rows = [
        _row("t1", "crowe-logic", True),
        _row("t2", "crowe-logic", False),
        _row("t1", "reference", True),
        _row("t2", "reference", True),
    ]
    md = render_scoreboard(rows)
    assert "pass@1" in md
    assert "gap" in md.lower()
    assert "+50 pp" in md  # reference 100% - crowe 50%
    assert "## Per-task pass@1" in md
    assert "t1" in md and "t2" in md
