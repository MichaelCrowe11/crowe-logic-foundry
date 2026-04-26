# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Research Engine, proprietary and private.

"""Cost estimation and budget tracking for the pipeline."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .models import StageUsage


@dataclass(frozen=True)
class Pricing:
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_creation_per_mtok: float


PRICING: dict[str, Pricing] = {
    "claude-sonnet-4-6": Pricing(
        input_per_mtok=3.00,
        output_per_mtok=15.00,
        cache_read_per_mtok=0.30,
        cache_creation_per_mtok=3.75,
    ),
    "claude-haiku-4-5-20251001": Pricing(
        input_per_mtok=1.00,
        output_per_mtok=5.00,
        cache_read_per_mtok=0.10,
        cache_creation_per_mtok=1.25,
    ),
}


def estimate_cost_usd(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
) -> float:
    p = PRICING[model]
    return (
        input_tokens * p.input_per_mtok
        + output_tokens * p.output_per_mtok
        + cache_read_tokens * p.cache_read_per_mtok
        + cache_creation_tokens * p.cache_creation_per_mtok
    ) / 1_000_000


class BudgetExceeded(Exception):
    def __init__(self, *, spent_usd: float, budget_usd: float) -> None:
        self.spent_usd = spent_usd
        self.budget_usd = budget_usd
        super().__init__(f"Budget exceeded: spent ${spent_usd:.2f} of ${budget_usd:.2f}")


class CostTracker:
    """Async-safe running total of pipeline costs."""

    def __init__(self, budget_usd: float | None) -> None:
        self.budget_usd = budget_usd
        self.total_cost_usd = 0.0
        self.total_duration_seconds = 0.0
        self.stage_usages: list[StageUsage] = []
        self._lock = asyncio.Lock()

    async def record(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_creation_tokens: int,
        stage: str,
        duration_seconds: float,
    ) -> StageUsage:
        cost = estimate_cost_usd(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )
        usage = StageUsage(
            stage=stage,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cost_usd=cost,
            duration_seconds=duration_seconds,
        )
        async with self._lock:
            self.total_cost_usd += cost
            self.total_duration_seconds += duration_seconds
            self.stage_usages.append(usage)
        return usage

    def can_afford(self, estimated_usd: float) -> bool:
        if self.budget_usd is None:
            return True
        return (self.total_cost_usd + estimated_usd) <= self.budget_usd
