from pathlib import Path

from bench.agentic.agents.base import AgentResult


def test_agent_result_is_constructable():
    r = AgentResult(
        workdir=Path("/tmp/x"),
        transcript=[],
        rounds=3,
        tool_calls=5,
        wall_s=1.2,
        tokens=100,
        cost_usd=0.01,
        self_verified=True,
        error=None,
    )
    assert r.rounds == 3
    assert r.self_verified is True
    assert r.error is None
