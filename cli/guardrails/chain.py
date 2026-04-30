"""
GuardrailChain: compose individual guardrails into a single pipeline.

The chain is the public surface the renderer and session runtime call. It
gives them three primitives:

    chain.scrub_output(text)     - block-level final scrub (secrets + style)
    chain.stream(chunk)          - streaming token-by-token scrub
    chain.flush_stream()         - end-of-stream flush
    chain.check_path(path)       - decide whether a Write target is allowed
    chain.check_budget(r, o)     - decide whether reasoning budget is exceeded

Every fired guardrail is recorded as a GuardrailEvent on the chain. The
session runtime can drain `chain.events` after each turn for telemetry and to
emit CSEP `error.surface` events later (see Cortex spec sub-project 7.2).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from cli.guardrails.narration import (
    NarrationReport,
    ReasoningNarrationDetector,
)
from cli.guardrails.paths import PathPolicy, PathDecision
from cli.guardrails.scope import (
    BudgetDecision,
    ScopeBudget,
    ScopeBudgetExceeded,
)
from cli.guardrails.secrets import SecretScrubber, StreamScrubber, SecretHit
from cli.guardrails.style import StyleEnforcer, StyleIssue


@dataclass(frozen=True)
class GuardrailEvent:
    """A single guardrail firing.

    `code` matches the future CSEP `error.surface` codes:
      - "secret-redacted"
      - "style-rewritten"
      - "style-warning"
      - "path-denied"
      - "path-confirm-required"
      - "scope-budget-warn"
      - "scope-budget-exceeded"
    """

    code: str
    severity: str  # "info", "warn", "error"
    message: str
    at: float = field(default_factory=time.time)
    detail: dict[str, Any] = field(default_factory=dict)


class GuardrailChain:
    def __init__(
        self,
        secrets: SecretScrubber | None = None,
        style: StyleEnforcer | None = None,
        paths: PathPolicy | None = None,
        budget: ScopeBudget | None = None,
        narration: ReasoningNarrationDetector | None = None,
    ):
        self._secrets = secrets or SecretScrubber()
        self._style = style or StyleEnforcer()
        self._paths = paths or PathPolicy()
        self._budget = budget or ScopeBudget()
        self._narration = narration or ReasoningNarrationDetector()
        self._stream = StreamScrubber(block_scrubber=self._secrets)
        self.events: list[GuardrailEvent] = []

    # ---- block-level scrubbing -------------------------------------------

    def scrub_output(self, text: str) -> str:
        """Scrub a final block of output: secrets, then style."""
        cleaned, secret_hits = self._secrets.scrub(text)
        for hit in secret_hits:
            self._record_secret(hit)
        cleaned, style_issues = self._style.enforce(cleaned)
        for issue in style_issues:
            self._record_style(issue)
        return cleaned

    # ---- streaming scrubbing ---------------------------------------------

    def stream(self, chunk: str) -> str:
        """Scrub one streaming chunk. Returns the prefix safe to emit."""
        safe = self._stream.feed(chunk)
        if safe:
            safe, style_issues = self._style.enforce(safe)
            for issue in style_issues:
                self._record_style(issue)
        return safe

    def flush_stream(self) -> str:
        """End of stream. Drain hold-back buffer, scrubbed."""
        tail = self._stream.flush()
        if tail:
            tail, style_issues = self._style.enforce(tail)
            for issue in style_issues:
                self._record_style(issue)
        for hit in self._stream.hits:
            self._record_secret(hit)
        return tail

    # ---- path policy -----------------------------------------------------

    def check_path(self, candidate_path: str) -> PathDecision:
        decision = self._paths.evaluate(candidate_path)
        if decision.verdict == "DENY":
            self.events.append(
                GuardrailEvent(
                    code="path-denied",
                    severity="error",
                    message=decision.reason,
                    detail={"path": decision.path},
                )
            )
        elif decision.verdict == "REQUIRE_CONFIRM":
            self.events.append(
                GuardrailEvent(
                    code="path-confirm-required",
                    severity="warn",
                    message=decision.reason,
                    detail={"path": decision.path},
                )
            )
        return decision

    # ---- reasoning narration ---------------------------------------------

    def scan_reasoning(
        self, reasoning_text: str, threshold_per_1k: float = 5.0
    ) -> NarrationReport:
        """Scan a block of reasoning text for narration density.

        Records a `reasoning-narration-detected` event when density exceeds
        the threshold. Default threshold (5.0 hits per 1k chars) corresponds
        to roughly the Talon transcript's reasoning density.
        """
        report = self._narration.scan(reasoning_text)
        if report.hits_per_1k_chars >= threshold_per_1k:
            self.events.append(
                GuardrailEvent(
                    code="reasoning-narration-detected",
                    severity="warn",
                    message=(
                        f"reasoning stream contains {report.total_hits} "
                        f"narration phrases ({report.hits_per_1k_chars:.1f} per 1k chars); "
                        f"variant is thinking out loud instead of acting"
                    ),
                    detail={
                        "total_hits": report.total_hits,
                        "by_label": report.by_label,
                        "samples": report.samples[:3],
                        "threshold": threshold_per_1k,
                    },
                )
            )
        return report

    # ---- scope budget ----------------------------------------------------

    def check_budget(
        self, reasoning_tokens: int, output_tokens: int, raise_on_interrupt: bool = False
    ) -> BudgetDecision:
        if raise_on_interrupt:
            decision = self._budget.evaluate_or_raise(reasoning_tokens, output_tokens)
        else:
            decision = self._budget.evaluate(reasoning_tokens, output_tokens)
        if decision.verdict == "WARN":
            self.events.append(
                GuardrailEvent(
                    code="scope-budget-warn",
                    severity="warn",
                    message=decision.reason,
                    detail={
                        "reasoning": decision.reasoning_tokens,
                        "output": decision.output_tokens,
                        "ratio": decision.ratio,
                    },
                )
            )
        elif decision.verdict == "INTERRUPT":
            self.events.append(
                GuardrailEvent(
                    code="scope-budget-exceeded",
                    severity="error",
                    message=decision.reason,
                    detail={
                        "reasoning": decision.reasoning_tokens,
                        "output": decision.output_tokens,
                        "ratio": decision.ratio,
                        "interrupt_prompt": ScopeBudget.interrupt_prompt(),
                    },
                )
            )
        return decision

    # ---- internal -------------------------------------------------------

    def _record_secret(self, hit: SecretHit) -> None:
        self.events.append(
            GuardrailEvent(
                code="secret-redacted",
                severity="error",
                message=f"redacted credential of type {hit.label}",
                detail={"label": hit.label, "marker": hit.redacted_with},
            )
        )

    def _record_style(self, issue: StyleIssue) -> None:
        if issue.kind == "em_dash":
            self.events.append(
                GuardrailEvent(
                    code="style-rewritten",
                    severity="info",
                    message=f"rewrote {issue.count} em-dash(es) per user MEMORY rule",
                    detail={"count": issue.count, "sample": issue.sample},
                )
            )
        elif issue.kind == "emoji":
            self.events.append(
                GuardrailEvent(
                    code="style-warning",
                    severity="warn",
                    message=f"output contained {issue.count} emoji(s); user MEMORY rule prefers no emoji",
                    detail={"count": issue.count, "sample": issue.sample},
                )
            )
