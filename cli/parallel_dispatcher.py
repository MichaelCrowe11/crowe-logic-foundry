"""
Crowe Logic Foundry - Parallel Dispatcher

Fan-out + fusion for multi-target turns. Used when the router emits a
``RouteDecision`` with non-empty ``companions`` and the call site wants the
companion answers alongside (or in lieu of) the primary's.

This module deliberately does NOT know about CroweLM's streaming/render
pipeline. It returns plain :class:`DispatchResult` objects. The caller is
responsible for translating those into renderer events.

Fusion modes
------------

``primary_only``
    Only return the primary's answer. Companions still run (warming caches,
    capturing traces) but their answers are dropped.

``primary_with_fallback``
    Return the primary's answer if it succeeded; otherwise return the first
    companion that produced an answer.

``present_both``
    Return all successful answers concatenated under model labels, in stable
    order (primary first). Useful for "is the agent right?" comparisons.

``ensemble_synthesis``
    Reserved. Will run a synthesizer over all answers. Not implemented;
    raises :class:`NotImplementedError`.
"""

from __future__ import annotations

import time
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FutureTimeoutError,
    as_completed,
)
from dataclasses import dataclass, field
from typing import Callable, Literal


FusionMode = Literal[
    "primary_only",
    "primary_with_fallback",
    "present_both",
    "ensemble_synthesis",
]


@dataclass
class DispatchResult:
    """Outcome of one provider invocation."""

    model_label: str
    answer: str = ""
    latency_s: float = 0.0
    cost_credits: int = 0
    error: BaseException | None = None
    is_primary: bool = False

    @property
    def succeeded(self) -> bool:
        return self.error is None and bool(self.answer)


@dataclass
class DispatchOutcome:
    """Combined result of a parallel dispatch."""

    fused_answer: str
    results: list[DispatchResult] = field(default_factory=list)
    fusion: FusionMode = "primary_only"
    total_latency_s: float = 0.0

    def successful_results(self) -> list[DispatchResult]:
        return [r for r in self.results if r.succeeded]


InvokeFn = Callable[[dict, str], DispatchResult]

# A synthesizer adapter: given the original prompt and the list of successful
# per-model results, return a single fused answer. Kept provider-agnostic (like
# InvokeFn) so the dispatcher never imports a provider module.
SynthFn = Callable[[str, "list[DispatchResult]"], str]


# Default system prompt for the ensemble synthesizer. Generalizes dual_mode's
# proven "merge" strategy from two peers to N CroweLM tiers. This is a behavior
# knob worth tuning for your domain (see build_synthesis_input below).
DEFAULT_ENSEMBLE_SYNTH_PROMPT = (
    "You are CroweLM's synthesis layer. Several peer CroweLM tiers answered the "
    "same user question independently. Produce a single authoritative response "
    "that keeps the strongest, best-supported claims from across the answers, "
    "reconciles contradictions by reasoning from evidence (and flags any that "
    "cannot be reconciled), and drops filler and repetition. Do not name or "
    "count the source models. Do not narrate the merging process. Write as if "
    "this were the original, final answer."
)


def dispatch(
    prompt: str,
    primary: dict,
    *,
    invoke: InvokeFn,
    companions: list[dict] | tuple[dict, ...] = (),
    timeout_s: float = 45.0,
    fusion: FusionMode = "primary_only",
    synthesize: SynthFn | None = None,
) -> DispatchOutcome:
    """Fan out ``prompt`` to ``primary`` + ``companions`` in parallel; fuse results.

    ``invoke(model_cfg, prompt) -> DispatchResult`` is the per-provider adapter.
    The caller wires this to whatever provider client they prefer; the dispatcher
    itself does not import any provider modules.

    On per-target timeout, the corresponding :class:`DispatchResult` carries
    ``error=FutureTimeoutError(...)`` and ``answer=""``. The dispatcher does
    not cancel the underlying provider call - Python threads cannot be killed
    safely from outside - it simply stops waiting. Use ``cost_credits``
    accounting on the caller side to ensure abandoned calls are still metered
    if they eventually return.
    """
    targets: list[tuple[dict, bool]] = [(primary, True)]
    targets.extend((cfg, False) for cfg in companions)
    started = time.time()

    # Collect by future, then reassemble in stable SUBMISSION order (primary
    # first, then companions as given) so present_both / ensemble output is
    # deterministic regardless of which model finishes first.
    result_for: dict = {}

    with ThreadPoolExecutor(max_workers=max(len(targets), 1)) as pool:
        futures = {
            pool.submit(_safe_invoke, invoke, cfg, prompt, is_primary): (
                cfg,
                is_primary,
            )
            for cfg, is_primary in targets
        }
        try:
            for fut in as_completed(futures, timeout=timeout_s):
                result_for[fut] = fut.result()
        except FutureTimeoutError:
            for fut, (cfg, is_primary) in futures.items():
                if fut in result_for:
                    continue
                result_for[fut] = DispatchResult(
                    model_label=str(cfg.get("label", cfg.get("name", "?"))),
                    error=FutureTimeoutError(f"timed out after {timeout_s}s"),
                    is_primary=is_primary,
                )

    primary_result: DispatchResult | None = None
    companion_results: list[DispatchResult] = []
    for fut, (cfg, is_primary) in futures.items():
        res = result_for.get(fut) or DispatchResult(
            model_label=str(cfg.get("label", cfg.get("name", "?"))),
            error=RuntimeError("future did not complete"),
            is_primary=is_primary,
        )
        if is_primary:
            primary_result = res
        else:
            companion_results.append(res)

    if primary_result is None:
        primary_result = DispatchResult(
            model_label=str(primary.get("label", primary.get("name", "?"))),
            error=RuntimeError("primary future did not complete"),
            is_primary=True,
        )

    all_results: list[DispatchResult] = [primary_result, *companion_results]
    if fusion == "ensemble_synthesis":
        fused = _ensemble_synthesize(prompt, all_results, synthesize)
    else:
        fused = _fuse(primary_result, companion_results, fusion)

    return DispatchOutcome(
        fused_answer=fused,
        results=all_results,
        fusion=fusion,
        total_latency_s=time.time() - started,
    )


def _safe_invoke(
    invoke: InvokeFn, cfg: dict, prompt: str, is_primary: bool
) -> DispatchResult:
    """Run the user-provided ``invoke`` adapter and trap any exception into the result."""
    started = time.time()
    label = str(cfg.get("label", cfg.get("name", "?")))
    try:
        result = invoke(cfg, prompt)
    except BaseException as exc:  # noqa: BLE001 - surface every failure mode
        return DispatchResult(
            model_label=label,
            error=exc,
            latency_s=time.time() - started,
            is_primary=is_primary,
        )
    result.is_primary = is_primary
    if not result.model_label:
        result.model_label = label
    if not result.latency_s:
        result.latency_s = time.time() - started
    return result


def _fuse(
    primary: DispatchResult,
    companions: list[DispatchResult],
    mode: FusionMode,
) -> str:
    if mode == "primary_only":
        if primary.succeeded:
            return primary.answer
        return _first_success_answer(companions)

    if mode == "primary_with_fallback":
        if primary.succeeded:
            return primary.answer
        return _first_success_answer(companions)

    if mode == "present_both":
        return _present_sections([primary, *companions])

    raise ValueError(f"Unknown fusion mode: {mode!r}")


def _present_sections(results: list[DispatchResult]) -> str:
    """Render each result as a labeled markdown section (stable input order)."""
    sections: list[str] = []
    for r in results:
        if r.succeeded:
            sections.append(f"### {r.model_label}\n\n{r.answer.strip()}")
        elif r.error is not None:
            sections.append(f"### {r.model_label} (error)\n\n{r.error}")
    return "\n\n---\n\n".join(sections) if sections else ""


def _ensemble_synthesize(
    prompt: str,
    results: list[DispatchResult],
    synthesize: SynthFn | None,
) -> str:
    """Fuse N successful answers into one via the caller's synthesizer.

    Degrades safely: zero successes -> empty; a single success is itself the
    answer (no synth call, no wasted tokens); and when no synthesizer is wired
    we fall back to a readable side-by-side rather than dropping content.
    """
    successful = [r for r in results if r.succeeded]
    if not successful:
        return ""
    if len(successful) == 1:
        return successful[0].answer.strip()
    if synthesize is None:
        return _present_sections(results)
    return synthesize(prompt, successful)


def build_synthesis_input(prompt: str, results: list[DispatchResult]) -> str:
    """Frame the original question + each peer answer as one synthesizer message.

    Mirrors dual_mode's proven framing, generalized from two peers to N. The
    synthesizer sees this as a single stateless user turn (no peer history).
    """
    parts = [f"User's original question:\n{prompt.strip()}\n"]
    for r in results:
        if r.succeeded:
            parts.append(f"--- Answer from {r.model_label} ---\n{r.answer.strip()}\n")
    parts.append("Produce the final synthesis now.")
    return "\n".join(parts)


def _first_success_answer(results: list[DispatchResult]) -> str:
    for r in results:
        if r.succeeded:
            return r.answer
    return ""


__all__ = [
    "DispatchResult",
    "DispatchOutcome",
    "FusionMode",
    "SynthFn",
    "dispatch",
    "build_synthesis_input",
    "DEFAULT_ENSEMBLE_SYNTH_PROMPT",
]
