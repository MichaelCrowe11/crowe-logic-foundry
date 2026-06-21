from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class AgentResult:
    workdir: Path
    transcript: list[dict]
    rounds: int
    tool_calls: int
    wall_s: float
    tokens: int | None
    cost_usd: float | None
    self_verified: bool
    error: str | None


class AgentRunner(Protocol):
    name: str

    def run(
        self,
        *,
        prompt: str,
        workdir: Path,
        model: str,
        tools: list[str],
        max_rounds: int,
        timeout_s: int,
    ) -> AgentResult: ...
