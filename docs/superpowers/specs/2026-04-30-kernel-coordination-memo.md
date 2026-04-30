---
title: Agentic-Loop Kernel - Coordination Memo
status: planning notes
date: 2026-04-30
companion-of: 2026-04-30-crowelm-quality-stack-design.md
relates-to: cli/parallel_dispatcher.py, config/router.py, 2026-04-30-crowelm-engine-extraction.md
---

# Kernel Coordination Memo

This memo captures findings from reading the parallel session's
`cli/parallel_dispatcher.py`, `config/router.py`, and the engine extraction
plan, before adding a unified agentic-loop kernel to the Quality Stack.

## What already exists

- **`cli/parallel_dispatcher.dispatch()`** fans out a prompt to a primary plus
  optional companions over threads, collects results, and fuses per a
  `FusionMode` choice (primary_only, primary_with_fallback, present_both,
  ensemble_synthesis). Global timeout default 45s. Caller passes a custom
  `invoke(model_cfg, prompt) -> DispatchResult` adapter so it is provider
  agnostic.
- **`config/router.classify_prompt()`** + **`route_prompt()`** classify a
  user prompt into 9 intent tiers (arithmetic to deep reasoning) using
  heuristics, then resolve to the cheapest available variant from
  MODEL_CHAIN. Returns up to 3 fallbacks plus optional parallel companions
  (e.g. DeepParallel for domain queries). No LLM call. Sub-millisecond.
- **`cli/guardrails/scope.ScopeBudget`** (Quality Stack) already enforces
  reasoning-to-output ratio with a `ScopeBudgetExceeded` exception.
- **`cli/guardrails/narration.ReasoningNarrationDetector`** (Quality Stack)
  already detects narration density.

## What does not exist

- Multi-dimensional turn budget (tokens AND tools AND dollars). ScopeBudget
  is reasoning-to-output ratio only.
- Per-stream TTFT watchdog with cross-variant fallback. parallel_dispatcher
  has a global timeout, not per-token latency tracking.
- Tool-call deduplication (this commit ships `cli/tool_cache.py`).
- A single agent loop that owns all of the above plus narration detection
  and the guardrail chain.

## The kernel slot in the engine extraction plan

The plan covers Phase 1 (CSEP, BrandVeil, Strangler-Fig wrapper, `cl-engine`
CLI) and lists future phases by name (Tauri, renderer, control plane,
Terminal pane, migrations). It does NOT reserve a kernel slot. CSEP event
vocabulary has placeholders for telemetry, cache hits, tool invokes,
errors, but nothing kernel-specific.

This means the kernel is mine to design, with no upstream collision today.

## Recommendation

**Build `cli/kernel/` next to `cli/guardrails/`. Do not build
`crowelm/kernel/`.**

Reasoning:

1. The kernel is a runtime-control concern, sibling to guardrails. Both
   live under `cli/`.
2. `crowelm/` is the Strangler-Fig public API. Importing kernel from there
   creates a circular-import risk; observer-hook from `crowelm/` into
   `cli/kernel/` is the safer direction.
3. When the Cortex extraction lands, `crowelm/` wraps the kernel via the
   observer pattern. No refactor needed on either side.

## Module layout

```
cli/kernel/
    __init__.py
    core.py          # AgenticLoopKernel: composes the pieces
    budget.py        # multi-dimensional TurnBudget extending ScopeBudget
    dedup.py         # delegates to cli/tool_cache.py
    ttft.py          # delegates to providers/_ttft_watchdog.py
    narration.py     # delegates to cli/guardrails/narration.py
    plan.py          # plan-and-execute scaffolding (optional, deferred)
```

The first three files are 80% delegation to modules that already exist.
`core.py` is the new thing: composes the pieces into one
`AgenticLoopKernel.run_turn(...)` entry point.

## Threading the GuardrailChain through dispatch()

Add an optional `guardrail_chain` kwarg to `parallel_dispatcher.dispatch()`.
On entry, scrub the prompt once. On exit, scrub each result's `answer`
field. Backward-compatible. Telemetry is auto-recorded on the chain.

```python
def dispatch(
    prompt, primary, *,
    invoke, companions=(), timeout_s=45.0,
    fusion="primary_only",
    guardrail_chain=None,            # NEW
):
    if guardrail_chain:
        prompt = guardrail_chain.scrub_output(prompt)
    # ... existing fan-out ...
    if guardrail_chain:
        for result in all_results:
            if result.answer:
                result.answer = guardrail_chain.scrub_output(result.answer)
    return outcome
```

Alternatives (wrap the invoke callback) scatter logic across call sites and
are not preferred.

## Sequencing

The kernel is item 6 in the architecture list. Items 1, 2, 3, 4, 5 land
first because they are smaller and the kernel composes them. Once items
1 to 5 are stable in production, lift the composition into `cli/kernel/`.

## Open questions

1. Multi-dimensional budget shape: do we track dollars per turn, per
   session, per project? My recommendation: per-turn caps with optional
   per-session aggregation; dollars at the variant level via existing
   cost_model.py.
2. CSEP event types for kernel concerns: budget-warn, budget-exceeded,
   ttft-timeout, tool-deduped, narration-detected. Coordinate with the
   Cortex spec authors before wire-format lands.
3. Failure recovery semantics on `ScopeBudgetExceeded`: re-prompt the
   model with the interrupt message, OR cut the turn and surface the
   summary back to the user? My recommendation: surface to user, do not
   silently re-prompt - the user paid for the budget excess and should see
   what happened.
