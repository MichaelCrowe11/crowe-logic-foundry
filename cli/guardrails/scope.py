"""
ScopeBudget: detect runaway reasoning and gold-plating.

The 2026-04-30 Eclipse session burned 5,856 reasoning tokens to produce 698
output tokens (8.4x ratio) on a "send four emails" task. The model
self-noticed the overbuild mid-stream and continued anyway. ScopeBudget is the
external interrupt that fires when this happens: it raises a signal the
session runtime can use to inject a "summarize and act" prompt.

Decisions:
    OK         - within budget, continue.
    WARN       - approaching budget, recommend wrap-up.
    INTERRUPT  - over budget; session runtime should inject a course-correct.

Mid-stream interrupt:
    `ScopeBudgetExceeded` exception is raised by `evaluate_or_raise` when the
    INTERRUPT verdict fires. The renderer's feed_reasoning() can call this
    periodically to cap runaway reasoning before finish() is reached.
"""
from __future__ import annotations

from dataclasses import dataclass


class ScopeBudgetExceeded(Exception):
    """Raised mid-stream when the reasoning-to-output ratio exceeds budget.

    The agent loop should catch this, surface the interrupt prompt to the
    model as a system message, and re-prompt the variant for a concise
    summary-and-act response.
    """

    def __init__(self, decision: "BudgetDecision"):
        self.decision = decision
        super().__init__(decision.reason)


@dataclass(frozen=True)
class BudgetDecision:
    verdict: str  # "OK", "WARN", "INTERRUPT"
    reason: str
    reasoning_tokens: int
    output_tokens: int
    ratio: float


class ScopeBudget:
    """Track reasoning vs output token spend per turn.

    Defaults are calibrated against the 2026-04-30 Eclipse incident:
      - max_reasoning_to_output_ratio = 5.0 (Eclipse hit 8.4x)
      - min_output_tokens = 500 (small tasks are exempt from ratio check
        because high ratio is sometimes legitimate when the answer is short)
      - hard_reasoning_cap = 32_000 (absolute ceiling)
    """

    def __init__(
        self,
        max_reasoning_to_output_ratio: float = 5.0,
        min_output_tokens_for_ratio: int = 500,
        warn_reasoning_tokens: int = 12_000,
        hard_reasoning_cap: int = 32_000,
    ):
        self.max_ratio = max_reasoning_to_output_ratio
        self.min_output_for_ratio = min_output_tokens_for_ratio
        self.warn_tokens = warn_reasoning_tokens
        self.hard_cap = hard_reasoning_cap

    def evaluate(self, reasoning_tokens: int, output_tokens: int) -> BudgetDecision:
        ratio = self._ratio(reasoning_tokens, output_tokens)

        if reasoning_tokens >= self.hard_cap:
            return BudgetDecision(
                verdict="INTERRUPT",
                reason=f"reasoning tokens {reasoning_tokens} exceeded hard cap {self.hard_cap}",
                reasoning_tokens=reasoning_tokens,
                output_tokens=output_tokens,
                ratio=ratio,
            )

        if (
            output_tokens >= self.min_output_for_ratio
            and ratio > self.max_ratio
        ):
            return BudgetDecision(
                verdict="INTERRUPT",
                reason=(
                    f"reasoning-to-output ratio {ratio:.1f}x exceeded "
                    f"limit {self.max_ratio}x at {output_tokens} output tokens"
                ),
                reasoning_tokens=reasoning_tokens,
                output_tokens=output_tokens,
                ratio=ratio,
            )

        if reasoning_tokens >= self.warn_tokens:
            return BudgetDecision(
                verdict="WARN",
                reason=f"reasoning tokens {reasoning_tokens} approaching cap",
                reasoning_tokens=reasoning_tokens,
                output_tokens=output_tokens,
                ratio=ratio,
            )

        return BudgetDecision(
            verdict="OK",
            reason="within budget",
            reasoning_tokens=reasoning_tokens,
            output_tokens=output_tokens,
            ratio=ratio,
        )

    @staticmethod
    def _ratio(reasoning_tokens: int, output_tokens: int) -> float:
        if output_tokens <= 0:
            return float("inf") if reasoning_tokens > 0 else 0.0
        return reasoning_tokens / output_tokens

    @staticmethod
    def interrupt_prompt() -> str:
        """The system message to inject when an INTERRUPT fires."""
        return (
            "You have exceeded the reasoning-token budget for this turn. "
            "Stop exploring. Summarize what you have learned in two sentences, "
            "then take the single most useful action that addresses the user's "
            "original request. Do not start a new line of investigation."
        )

    def evaluate_or_raise(
        self, reasoning_tokens: int, output_tokens: int
    ) -> BudgetDecision:
        """Evaluate the budget; raise ScopeBudgetExceeded on INTERRUPT.

        Use this in the renderer's feed_reasoning() (with a debounce so it
        doesn't run on every token) to short-circuit runaway reasoning
        streams before finish() time. The renderer or agent loop catches
        the exception, drops further reasoning tokens, and surfaces the
        interrupt prompt to the model on the next turn.
        """
        decision = self.evaluate(reasoning_tokens, output_tokens)
        if decision.verdict == "INTERRUPT":
            raise ScopeBudgetExceeded(decision)
        return decision
