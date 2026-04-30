---
title: Three-Spec Integration Order
status: draft (pending user review)
date: 2026-04-30
author: Michael Crowe (with Claude)
relates-to: docs/superpowers/specs/2026-04-30-crowe-cortex-design.md, docs/superpowers/specs/2026-04-30-crowelm-quality-stack-design.md, docs/superpowers/specs/2026-04-30-routing-and-parallel-dispatch.md
---

# Three-Spec Integration Order

Three sibling design specs landed on 2026-04-30, each describing one slice of the next-generation Crowe Logic Foundry. Each spec is internally coherent. None of them says which of the others ships first. This plan fills that gap.

## The three specs

| Spec | Slice | New surface |
|---|---|---|
| Crowe Cortex | Surface (desktop app, renderer protocol, control plane) | Tauri 2 app, Rust core, BrandVeil, CSEP |
| CroweLM Quality Stack | Brain (guardrails, prompt loader, eval, LoRA gate) | `cli/guardrails/`, `config/prompt_loader.py`, `eval/`, LoRA scripts |
| Routing and Parallel Dispatch | Decision layer (intent classifier, fan-out, fusion, session continuity) | `config/router.py`, `cli/parallel_dispatcher.py`, `cli/session_runtime.py` extensions |

## Merge order

Strict order. Each phase's output is the next phase's input.

```
1. Cortex Phase 7.1 (engine extraction)
   |
   v
2. Quality Stack integration patch
   |
   v
3. Routing wire-in
```

### Why this order

1. **Cortex Phase 7.1 first.** Phase 7.1 extracts the Foundry engine from the Python sidecar into a Rust core. Both Quality Stack and Routing need to wire into that engine surface. Wiring before extraction means redoing the wire-in afterward.
2. **Quality Stack second.** Guardrails sit between the engine and the renderer; they need a stable engine surface to attach to. Prompt loader, eval harness, and LoRA gate are independent of routing decisions, so they can land before routing.
3. **Routing wire-in third.** The router and dispatcher are inert today (modules exist on `router-and-parallel` but no call site uses them). Wiring them after Cortex 7.1 + Quality Stack lets the dispatcher's output feed naturally into the guardrail pipeline.

### Why not parallel

The three specs share two high-collision files:

- `config/agent_config.py` is edited by the Quality Stack integration patch. Routing intentionally does not modify it (the parallel-sessions feedback rule flags it as the highest-collision surface). Sequencing prevents merge conflicts.
- `cli/crowe_logic.py` is rewritten by Cortex 7.1, then has new turn-loop logic added by the routing wire-in, then has its system-prompt construction edited by the Quality Stack patch. All three must not touch it concurrently.

## Ownership of shared touch points

| File | Owner | Other specs' interaction |
|---|---|---|
| `config/agent_config.py` | Quality Stack integration patch | Routing avoids; Cortex 7.1 may rename or split during extraction |
| `cli/crowe_logic.py` | Cortex 7.1 (rewrite) then Routing (wire-in) then Quality Stack (system-prompt edit) | Sequential, never concurrent |
| `cli/session_runtime.py` | Routing extends; Quality Stack may add steering helpers | Additive only; new fields preferred over edits |
| `config/router.py` | Routing | None |
| `cli/parallel_dispatcher.py` | Routing | None |
| `cli/guardrails/`, `cli/guardrail_pipeline.py` | Quality Stack | Routing's dispatcher feeds these |
| `eval/` | Quality Stack | Routing's classifier becomes part of the eval gate's intent regression check |

## Coordination convention

The user runs multiple Claude Code sessions in parallel. To prevent integration order from being renegotiated implicitly each time:

1. **One spec, one branch.** Each spec lives on a feature branch named after itself (`router-and-parallel` for routing; analogous names for Cortex 7.1 and Quality Stack work).
2. **No spec edits the next phase's owned files.** If your spec needs to touch a file owned by a downstream spec, surface it; do not just write the edit.
3. **Schedule the wire-in.** When a spec's wire-in is blocked on a downstream phase, schedule a remote agent to revisit when the phase is expected to land. Routing has one already: `trig_01H9nbXroTmgQXziMeyYAYLG`, fires 2026-05-14.
4. **Update this doc when order changes.** If a spec is reprioritized, edit the merge-order section and note the date.

## Open questions

1. Does Cortex Phase 7.1 own renaming "Crowe Logic Foundry" to "Crowe Cortex" across the codebase, or is that a separate phase after extraction?
2. Should the Quality Stack eval harness gate the routing wire-in PR (i.e., classifier accuracy must pass before wire-in lands), or is that a follow-up?
3. Who writes the migration plan for removing the legacy `classify_task` / `TASK_CLASS_ROUTES` / `route_for_auto` system from `agent_config.py` after routing is wired in?

## Status as of 2026-04-30

| Phase | Owning spec | Status |
|---|---|---|
| Cortex Phase 7.1 | crowe-cortex-design.md | Spec drafted, implementation not started |
| Quality Stack integration patch | quality-stack-integration-patch.md | Modules drafted in working tree (untracked); integration deferred |
| Routing wire-in | routing-and-parallel-dispatch.md | Modules committed on `router-and-parallel` (`1f9191b`); wire-in scheduled for 2026-05-14 |
