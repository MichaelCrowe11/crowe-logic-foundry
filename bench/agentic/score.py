from __future__ import annotations

import json
from pathlib import Path

from bench.agentic.agents.base import AgentResult
from bench.agentic.verify import VerifyResult


def make_row(
    task_id: str, agent_name: str, model: str, result: AgentResult, verify: VerifyResult
) -> dict:
    return {
        "task_id": task_id,
        "agent": agent_name,
        "model": model,
        "passed": bool(verify.passed),
        "exit_code": verify.exit_code,
        "timed_out": verify.timed_out,
        "rounds": result.rounds,
        "tool_calls": result.tool_calls,
        "wall_s": round(result.wall_s, 3),
        "tokens": result.tokens,
        "cost_usd": result.cost_usd,
        "self_verified": result.self_verified,
        "error": result.error,
        "verify_output": verify.output,
    }


def append_row(path: Path, row: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
        fh.flush()


def aggregate(rows: list[dict]) -> dict:
    by_agent: dict[str, list[dict]] = {}
    for r in rows:
        by_agent.setdefault(r["agent"], []).append(r)
    out: dict[str, dict] = {}
    for agent, rs in by_agent.items():
        n = len(rs)
        out[agent] = {
            "n": n,
            "pass_at_1": sum(1 for r in rs if r["passed"]) / n if n else 0.0,
            "self_verified_rate": sum(1 for r in rs if r["self_verified"]) / n
            if n
            else 0.0,
            "avg_rounds": sum(r["rounds"] for r in rs) / n if n else 0.0,
        }
    return out
