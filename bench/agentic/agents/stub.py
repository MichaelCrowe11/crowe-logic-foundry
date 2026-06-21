from __future__ import annotations

from pathlib import Path
from typing import Callable

from bench.agentic.agents.base import AgentResult


class StubRunner:
    def __init__(
        self,
        name: str = "stub",
        mutate: Callable[[Path], None] | None = None,
        self_verified: bool = False,
        error: str | None = None,
    ):
        self.name = name
        self._mutate = mutate
        self._self_verified = self_verified
        self._error = error

    def run(
        self, *, prompt, workdir, model, tools, max_rounds, timeout_s
    ) -> AgentResult:
        if self._mutate is not None:
            self._mutate(Path(workdir))
        return AgentResult(
            workdir=Path(workdir),
            transcript=[{"role": "stub", "prompt": prompt}],
            rounds=1,
            tool_calls=1,
            wall_s=0.0,
            tokens=0,
            cost_usd=0.0,
            self_verified=self._self_verified,
            error=self._error,
        )
