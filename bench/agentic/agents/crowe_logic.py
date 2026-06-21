from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from bench.agentic.agents.base import AgentResult

# Tool signatures that count as the agent verifying its own work.
_TEST_MARKERS = (
    "pytest",
    "verify.sh",
    "python -m pytest",
    "python3 -m pytest",
    "unittest",
)


def _looks_like_test_run(name: str, args: str) -> bool:
    blob = f"{name} {args}".lower()
    return any(m in blob for m in _TEST_MARKERS)


def parse_events(events: list[dict]) -> dict:
    rounds = tool_calls = 0
    tokens = None
    self_verified = False
    error = None
    for ev in events:
        et = ev.get("type")
        if et == "segment_end":
            rounds += 1
        elif et == "tool":
            tool_calls += 1
            if _looks_like_test_run(str(ev.get("name", "")), str(ev.get("args", ""))):
                self_verified = True
        elif et == "done":
            tokens = ev.get("tokens")
        elif et == "error":
            error = ev.get("message", "unknown error")
    return {
        "rounds": rounds,
        "tool_calls": tool_calls,
        "tokens": tokens,
        "self_verified": self_verified,
        "error": error,
    }


def _execute_env() -> dict:
    """Headless reads autonomy from cli.autonomy; force 'execute' for the bench."""
    env = dict(os.environ)
    env["CROWE_LOGIC_AUTONOMY"] = "execute"
    return env


class CroweLogicRunner:
    name = "crowe-logic"

    def run(
        self, *, prompt, workdir, model, tools, max_rounds, timeout_s
    ) -> AgentResult:
        workdir = Path(workdir)
        payload = {"messages": [{"role": "user", "content": prompt}], "model": model}
        t0 = time.monotonic()
        events: list[dict] = []
        error = None
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "cli.headless"],
                input=json.dumps(payload),
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=_execute_env(),
            )
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except subprocess.TimeoutExpired:
            error = f"timeout after {timeout_s}s"
        wall_s = time.monotonic() - t0
        parsed = parse_events(events)
        if error and not parsed["error"]:
            parsed["error"] = error
        return AgentResult(
            workdir=workdir,
            transcript=events,
            rounds=parsed["rounds"],
            tool_calls=parsed["tool_calls"],
            wall_s=wall_s,
            tokens=parsed["tokens"],
            cost_usd=None,
            self_verified=parsed["self_verified"],
            error=parsed["error"],
        )
