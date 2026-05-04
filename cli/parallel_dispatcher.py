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


def dispatch(
    prompt: str,
    primary: dict,
    *,
    invoke: InvokeFn,
    companions: list[dict] | tuple[dict, ...] = (),
    timeout_s: float = 45.0,
    fusion: FusionMode = "primary_only",
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
    if fusion == "ensemble_synthesis":
        raise NotImplementedError(
            "ensemble_synthesis fusion is reserved; use present_both for now"
        )

    targets: list[tuple[dict, bool]] = [(primary, True)]
    targets.extend((cfg, False) for cfg in companions)
    started = time.time()

    primary_result: DispatchResult | None = None
    companion_results: list[DispatchResult] = []

    with ThreadPoolExecutor(max_workers=max(len(targets), 1)) as pool:
        futures = {
            pool.submit(_safe_invoke, invoke, cfg, prompt, is_primary): (cfg, is_primary)
            for cfg, is_primary in targets
        }
        try:
            for fut in as_completed(futures, timeout=timeout_s):
                result = fut.result()
                if result.is_primary:
                    primary_result = result
                else:
                    companion_results.append(result)
        except FutureTimeoutError:
            for fut, (cfg, is_primary) in futures.items():
                if fut.done():
                    continue
                stub = DispatchResult(
                    model_label=str(cfg.get("label", cfg.get("name", "?"))),
                    error=FutureTimeoutError(f"timed out after {timeout_s}s"),
                    is_primary=is_primary,
                )
                if is_primary:
                    primary_result = primary_result or stub
                else:
                    companion_results.append(stub)

    if primary_result is None:
        primary_result = DispatchResult(
            model_label=str(primary.get("label", primary.get("name", "?"))),
            error=RuntimeError("primary future did not complete"),
            is_primary=True,
        )

    all_results: list[DispatchResult] = [primary_result, *companion_results]
    fused = _fuse(primary_result, companion_results, fusion)

    return DispatchOutcome(
        fused_answer=fused,
        results=all_results,
        fusion=fusion,
        total_latency_s=time.time() - started,
    )


def _safe_invoke(invoke: InvokeFn, cfg: dict, prompt: str, is_primary: bool) -> DispatchResult:
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
        sections: list[str] = []
        if primary.succeeded:
            sections.append(f"### {primary.model_label}\n\n{primary.answer.strip()}")
        elif primary.error is not None:
            sections.append(f"### {primary.model_label} (error)\n\n{primary.error}")
        for c in companions:
            if c.succeeded:
                sections.append(f"### {c.model_label}\n\n{c.answer.strip()}")
            elif c.error is not None:
                sections.append(f"### {c.model_label} (error)\n\n{c.error}")
        return "\n\n---\n\n".join(sections) if sections else ""

    raise ValueError(f"Unknown fusion mode: {mode!r}")


def _first_success_answer(results: list[DispatchResult]) -> str:
    for r in results:
        if r.succeeded:
            return r.answer
    return ""


__all__ = [
    "DispatchResult",
    "DispatchOutcome",
    "FusionMode",
    "dispatch",
]
