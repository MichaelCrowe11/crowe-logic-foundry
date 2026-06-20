"""CroweLM Ensemble — fan one question out across several CroweLM tiers in
parallel and fuse their answers into one authoritative response.

This activates two pieces of previously-dormant infrastructure:

  * ``cli/parallel_dispatcher.py`` — the fan-out/fusion engine that was written
    to consume ``RouteDecision.companions`` but never wired into a surface.
  * its ``ensemble_synthesis`` fusion mode — shipped here as a real capability
    (it had been a reserved ``NotImplementedError``).

Why this beats a git-worktree fan-out (the usual "parallel agents" pattern):
worktrees parallelize *edits*; this parallelizes *reasoning* across a diverse
model stack, then synthesizes — turning CroweLM's 12-tier ladder into an
ensemble where tiers check and complement each other.

The orchestration is provider-agnostic by dependency injection: ``resolve`` /
``make_invoke`` / ``make_synth`` default to the real provider stack but are
overridable, so the whole path is testable with fakes (no network, no creds).

Run standalone:
    python -m cli.ensemble "your question" --models supreme,oracle,titan
Or via the agent CLI:
    crowe-logic ensemble "your question" --models supreme,oracle,titan
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from typing import Callable, Optional

from cli.parallel_dispatcher import (
    DispatchOutcome,
    DispatchResult,
    build_synthesis_input,
    dispatch,
    DEFAULT_ENSEMBLE_SYNTH_PROMPT,
)


# ---------------------------------------------------------------------------
# Synthesis strategies.
#
# >>> LEARNING-MODE CONTRIBUTION POINT <<<
# Which synthesis strategy to use is a genuine product decision with several
# valid answers, and your domain knowledge improves it. "merge" reuses the
# proven default; "judge" and "diff" mirror dual_mode. Consider adding a
# domain-specific strategy — e.g. a "mycology" council prompt that weights the
# answer most grounded in cultivation evidence, or a "cite" strategy that forces
# the synthesizer to attribute each retained claim. Add it to this dict and it
# becomes selectable via --strategy with no other code change.
# ---------------------------------------------------------------------------
STRATEGIES: dict[str, str] = {
    "merge": DEFAULT_ENSEMBLE_SYNTH_PROMPT,
    "judge": (
        "You are CroweLM's synthesis layer. Several peer CroweLM tiers answered "
        "the same question. Pick the single strongest answer, justify the choice "
        "in two sentences, then restate that answer in clean final form. Commit; "
        "do not hedge or say multiple answers are equally good."
    ),
    "diff": (
        "You are CroweLM's synthesis layer. Several peer CroweLM tiers answered "
        "the same question. Report: (1) where they agree, (2) where they diverge "
        "and which position is more defensible with one sentence of reasoning, "
        "(3) what all of them missed. Be terse; no filler."
    ),
}
DEFAULT_STRATEGY = "merge"


# Injectable adapter types (defaults bind to the real provider stack).
ResolveFn = Callable[[str], Optional[dict]]
MakeInvokeFn = Callable[[], Callable[[dict, str], DispatchResult]]
MakeSynthFn = Callable[[dict, str], Callable[[str, "list[DispatchResult]"], str]]


def run_ensemble(
    prompt: str,
    *,
    selectors: list[str],
    strategy: str = DEFAULT_STRATEGY,
    synth_selector: Optional[str] = None,
    timeout_s: float = 90.0,
    resolve: Optional[ResolveFn] = None,
    make_invoke: Optional[MakeInvokeFn] = None,
    make_synth: Optional[MakeSynthFn] = None,
) -> DispatchOutcome:
    """Resolve ``selectors`` to model configs, fan out ``prompt``, and synthesize.

    The first selector is the primary; the rest are companions. ``synth_selector``
    (default: the primary) is the tier that performs the fusion. Unresolvable
    selectors are dropped with a warning rather than aborting the run.
    """
    resolve = resolve or _default_resolve
    make_invoke = make_invoke or _default_make_invoke
    make_synth = make_synth or _default_make_synth

    resolved: list[dict] = []
    for sel in selectors:
        cfg = resolve(sel)
        if cfg is None:
            print(
                f"  [ensemble] skipping unknown model selector: {sel!r}",
                file=sys.stderr,
            )
            continue
        resolved.append(cfg)

    if not resolved:
        raise ValueError(f"no models resolved from selectors: {selectors!r}")

    primary, companions = resolved[0], resolved[1:]
    invoke = make_invoke()

    synth_cfg = resolve(synth_selector) if synth_selector else primary
    if synth_cfg is None:
        synth_cfg = primary
    strategy_prompt = STRATEGIES.get(strategy, STRATEGIES[DEFAULT_STRATEGY])
    synthesize = make_synth(synth_cfg, strategy_prompt)

    return dispatch(
        prompt,
        primary,
        invoke=invoke,
        companions=companions,
        timeout_s=timeout_s,
        fusion="ensemble_synthesis",
        synthesize=synthesize,
    )


# ---------------------------------------------------------------------------
# Auto-ensemble policy — connects the Synapse Router (which already emits
# companions for high-stakes domain/deep intents) to this ensemble path.
# ---------------------------------------------------------------------------


def selectors_from_decision(decision) -> list[str]:
    """Primary + companion tier selectors from a Synapse ``RouteDecision``."""
    selectors = [str(getattr(decision, "selected_label", "") or "")]
    for c in getattr(decision, "companions", ()) or ():
        selectors.append(str(c.get("label", c.get("name", ""))))
    return [s for s in selectors if s]


def should_auto_ensemble(decision, *, enabled: bool) -> bool:
    """True when auto-ensemble is on AND the router flagged companion tiers.

    The router only attaches companions for high-stakes intents (domain/deep),
    so this fires exactly on the turns worth the extra cost — and never unless
    explicitly enabled (it is slower and costs more tokens).
    """
    if not enabled:
        return False
    return bool(getattr(decision, "companions", ()) or ())


# ---------------------------------------------------------------------------
# Default adapters — bind to the real provider stack. Imports are lazy so this
# module stays importable (and testable) without pulling in the heavy CLI.
# The provider call mirrors cli/dual_mode.py exactly (a path proven in dual mode).
# ---------------------------------------------------------------------------


def _default_resolve(selector: str) -> Optional[dict]:
    from config.agent_config import resolve_model_config

    return resolve_model_config(selector)


def _capture_turn(cfg: dict, system_prompt: str, user_text: str) -> str:
    """Run one stateless provider turn, capturing the streamed text (no stdout)."""
    from rich.console import Console

    from cli.crowe_logic import _get_provider_for_dual
    from cli.renderer import StreamRenderer

    console = Console(file=io.StringIO(), force_terminal=False)
    provider = _get_provider_for_dual(cfg, system_prompt)
    provider.add_user_message(user_text)
    renderer = StreamRenderer(console=console, model_label=str(cfg.get("label", "?")))
    renderer.start()
    provider.stream_response(
        console=console,
        render_tool_card=lambda *a, **k: None,
        session_state={},
        _get_orchestrator=None,
        renderer=renderer,
    )
    renderer.finish(session_state={})
    return "".join(getattr(renderer, "_full_text_chunks", []))


def _default_make_invoke():
    def invoke(cfg: dict, prompt: str) -> DispatchResult:
        started = time.time()
        system = str(cfg.get("system_prompt") or "")
        text = _capture_turn(cfg, system, prompt)
        return DispatchResult(
            model_label=str(cfg.get("label", "?")),
            answer=text,
            latency_s=time.time() - started,
        )

    return invoke


def _default_make_synth(synth_cfg: dict, strategy_prompt: str):
    def synthesize(prompt: str, results: "list[DispatchResult]") -> str:
        synth_input = build_synthesis_input(prompt, results)
        return _capture_turn(synth_cfg, strategy_prompt, synth_input)

    return synthesize


# ---------------------------------------------------------------------------
# Standalone entry point.
# ---------------------------------------------------------------------------


def render_outcome(outcome: DispatchOutcome) -> str:
    """Human-readable summary: the fused answer + a per-tier latency/status line."""
    lines = [outcome.fused_answer.strip(), "", "—" * 8, "ensemble:"]
    for r in outcome.results:
        status = (
            "ok"
            if r.succeeded
            else f"FAIL ({type(r.error).__name__ if r.error else 'empty'})"
        )
        flag = "*" if r.is_primary else " "
        lines.append(f"  {flag} {r.model_label:<22} {status:<22} {r.latency_s:.1f}s")
    lines.append(f"  fusion={outcome.fusion}  wall={outcome.total_latency_s:.1f}s")
    return "\n".join(lines)


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="crowe-logic ensemble",
        description="Fan a question across CroweLM tiers and synthesize one answer.",
    )
    parser.add_argument("prompt", help="the question to ask the ensemble")
    parser.add_argument(
        "--models",
        "-m",
        default="supreme,oracle,prime",
        help="comma-separated tier selectors (first = primary). Default: supreme,oracle,prime",
    )
    parser.add_argument(
        "--strategy",
        "-s",
        default=DEFAULT_STRATEGY,
        choices=sorted(STRATEGIES),
        help=f"synthesis strategy (default: {DEFAULT_STRATEGY})",
    )
    parser.add_argument(
        "--synth", default=None, help="tier that synthesizes (default: primary)"
    )
    parser.add_argument(
        "--timeout", type=float, default=90.0, help="per-tier timeout seconds"
    )
    args = parser.parse_args(argv)

    selectors = [s.strip() for s in args.models.split(",") if s.strip()]
    try:
        outcome = run_ensemble(
            args.prompt,
            selectors=selectors,
            strategy=args.strategy,
            synth_selector=args.synth,
            timeout_s=args.timeout,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(render_outcome(outcome))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
