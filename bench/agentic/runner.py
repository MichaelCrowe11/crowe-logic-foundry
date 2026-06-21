from __future__ import annotations

import time
from pathlib import Path

from bench.agentic.agents.base import AgentResult
from bench.agentic.report import render_scoreboard
from bench.agentic.sandbox import sandbox
from bench.agentic.score import append_row, make_row
from bench.agentic.tasks_io import load_tasks
from bench.agentic.verify import VerifyResult, run_verify


def _crash_result(workdir: Path, err: str) -> AgentResult:
    return AgentResult(
        workdir=workdir,
        transcript=[],
        rounds=0,
        tool_calls=0,
        wall_s=0.0,
        tokens=None,
        cost_usd=None,
        self_verified=False,
        error=err,
    )


def run_suite(tasks_root, runners, results_dir, model: str) -> Path:
    tasks_root = Path(tasks_root)
    results_dir = Path(results_dir)
    raw = results_dir / "raw.jsonl"
    tasks = load_tasks(tasks_root)
    rows: list[dict] = []
    for task in tasks:
        for runner in runners:
            with sandbox(task.seed) as work:
                t0 = time.monotonic()
                try:
                    result = runner.run(
                        prompt=task.prompt,
                        workdir=work,
                        model=model,
                        tools=[],
                        max_rounds=task.meta.max_rounds,
                        timeout_s=task.meta.timeout_s,
                    )
                except Exception as e:  # crash isolation: record, never abort
                    result = _crash_result(work, f"{type(e).__name__}: {e}")
                    result.wall_s = time.monotonic() - t0
                if result.error:
                    verify = VerifyResult(False, None, False, result.error)
                else:
                    verify = run_verify(task.verify, work, task.meta.timeout_s)
                row = make_row(task.task_id, runner.name, model, result, verify)
                append_row(raw, row)
                rows.append(row)
    (results_dir / "scoreboard.md").write_text(render_scoreboard(rows))
    return results_dir


if __name__ == "__main__":  # pragma: no cover
    import argparse

    from bench.agentic.agents.crowe_logic import CroweLogicRunner
    from bench.agentic.agents.reference import ReferenceRunner

    ap = argparse.ArgumentParser(description="Run the agentic coding eval suite.")
    ap.add_argument("--tasks", default="bench/agentic/tasks")
    ap.add_argument("--results", default="bench/agentic/results")
    # The live Azure-Anthropic deployment (4.7/4.8 pending per config). Both
    # sides MUST resolve to the SAME Azure deployment for a fair comparison.
    ap.add_argument("--model", default="claude-opus-4-6")
    args = ap.parse_args()
    out = run_suite(
        args.tasks, [CroweLogicRunner(), ReferenceRunner()], args.results, args.model
    )
    print(f"scoreboard: {out / 'scoreboard.md'}")
