# CroweLM Routing Reliability & Optimization — Design

- **Date:** 2026-05-30
- **Repo:** `crowe-logic-foundry`
- **Branch:** `feat/ollama-nexus-failover-azure-gpt55`
- **Status:** Approved (design); pending implementation plan
- **Scope:** Spec 1 of 2. Spec 2 (CLI cosmetic redesign) follows after this lands.

## Problem

Live CroweLM CLI (`v0.3.0`) sessions exhibit four routing failures:

1. **Wrong model picked.** A trivial prompt ("how are you") lands on **CroweLM Supreme**
   (Anthropic Opus) instead of a fast tier.
2. **Slow TTFT.** Supreme cold-starts at ~18s time-to-first-token.
3. **Silent fallback.** Supreme times out / errors and the CLI silently degrades to
   **CroweLM Talon** with a generic "Model failed — switching to…" banner.
4. **No recovery.** Once degraded, a dead tier is retried turn after turn — no
   circuit breaking, no health awareness.

### Root cause

Three compounding bugs in the fallback path:

1. **Auto-routing is opt-in.** `_auto_route_enabled()` (`cli/crowe_logic.py:1428`) requires
   `CROWE_LOGIC_AUTO_ROUTE=1`. When off, every turn uses the chain head — which is
   **CroweLM Supreme** (first non-`auto` entry in `MODEL_CHAIN`, `config/agent_config.py:156`).
2. **The "nothing available" backstop dumps to the worst model.** When the cheap open
   tiers (Nano/Nexus/Apex) aren't reachable, `route_prompt` falls to
   `next(cfg for cfg in chain if provider != "auto")` (`config/router.py:401`) =
   **Supreme again**. The safety net is the slowest, most expensive tier.
3. **Fallback is linear and unguarded.** `_advance_model` (`cli/crowe_logic.py:136`) walks
   `MODEL_CHAIN` on failure with no TTFT budget, no circuit breaker, no recovery.

## Decisions (from brainstorming)

- **Approach:** B — health-aware routing layer (ship A's quick wins as its first increment).
- **Default routing:** default-on; `CROWE_LOGIC_AUTO_ROUTE=0` disables; `/model <alias>` pins a tier.
- **TTFT budget:** ~5s balanced — kills 18s hangs while tolerating normal cold starts.
- **Backstop:** strategic ladder — healthiest hosted fast tier → local Ollama floor →
  Nexus → … → Supreme only as absolute last resort.
- **Fail-open preserved:** if health logic itself errors, treat the tier as available so
  paying users are never hard-blocked.
- **Breaker cooldown:** ~60s, with a half-open probe before fully closing.

## Architecture

A health-aware layer between `route_prompt`'s candidate selection and the CLI's dispatch
loop. One new module, three touch-points.

```
route_prompt ──► candidate ladder (intent + health-filtered)
     │                    ▲
     │            config/health.py  ◄── records failures/TTFT, trips breakers
     ▼                    │
cli dispatch ──► ttft_guard(5s) ──► first token? ──yes──► stream ──► record_success
                          │
                         no/err ──► record_failure ──► advance ladder ──► explicit hedge badge
```

## Components & boundaries

### `config/health.py` (new)

`HealthRegistry` — the single source of truth for per-model usability.

- `is_available(model_cfg) -> bool` — config-present AND breaker not open.
- `record_success(name)` — closes a half-open breaker; clears failure count.
- `record_failure(name, reason)` — increments failures; trips breaker at threshold.
- `record_ttft(name, seconds)` — feeds latency stats; a budget breach counts as a soft failure.
- Circuit-breaker state machine per model: `closed → open (cooldown ~60s) → half-open (single probe) → closed/open`.
- TTL cache (~30s) on availability so a transient probe doesn't thrash.
- Pure and unit-testable; any real network probe is an **injected hook**, not hardwired.
- **Fail-open:** unexpected internal errors resolve to "available".

### `config/router.py` (edit)

- Add an explicit `FAST_BACKSTOP` selector ladder (see below).
- Replace the chain-head backstop at `router.py:401` so an empty candidate set resolves to
  `FAST_BACKSTOP`, **never** silently to Supreme.
- Default the `availability` callable to the shared `HealthRegistry`.

### `providers/_ttft_watchdog.py` (extend)

- `ttft_guard(budget=5.0)` — wraps the streaming first-token wait; returns on first token,
  raises `TTFTBudgetExceeded` on breach. Clock is injectable for tests (no real sleeps).

### `cli/crowe_logic.py` (edit)

- Wire `ttft_guard` into dispatch.
- On breach/error: `registry.record_failure(...)` then advance through the **routed
  candidate ladder** (not raw `MODEL_CHAIN`).
- Flip `_auto_route_enabled()` to default-on (env `=0` disables).
- Replace the silent "Model failed — switching to…" banner with an intentional badge that
  states the reason (timeout / error / unavailable) and the target tier.

## The strategic backstop ladder

```
FAST_BACKSTOP =
    (healthiest of: CroweLM Nano → CroweLM Talon → CroweLM Swift)
    → local Ollama floor (always-available)
    → CroweLM Nexus
    → … remaining hosted tiers …
    → CroweLM Supreme   # absolute last resort only
```

The **local Ollama floor** is the guarantee: it works even in the Azure/Anthropic dead-zone,
so "every prompt hits Supreme" cannot recur.

## Data flow

1. Turn starts → `route_prompt` classifies intent → builds primary + fallback ladder,
   filtered by `registry.is_available`.
2. Dispatch primary under `ttft_guard(5s)`.
3. First token within budget → stream → `record_success`.
4. Budget breach or error → `record_failure` (maybe trip breaker) → advance to next
   available candidate in the ladder → render explicit hedge badge.
5. Breaker open for a tier → skipped for the cooldown window; half-open probe afterward.

## Error handling

- **Fail-open** preserved: registry errors → treat as available; paying users never blocked.
- Badge + telemetry distinguish **timeout** vs **error** vs **unavailable** — a routine
  hedge no longer reads as a scary failure.
- Breaker cooldown stops repeated 18s hangs on a dead tier.

## Testing

- **Unit (`config/health.py`):** breaker transitions (closed→open→half-open→closed),
  TTL expiry, `record_ttft` soft-failure threshold, fail-open on internal error.
- **Unit (`config/router.py`):** empty candidate set resolves to `FAST_BACKSTOP` and never
  Supreme when fast tiers are down; intent → tier mapping unchanged for healthy tiers.
- **Unit (`providers/_ttft_watchdog.py`):** `ttft_guard` returns on first token; raises on
  budget breach — injected fake clock, no real sleeps.
- **Integration:** simulated slow primary hedges within budget; greeting → Nano not Supreme;
  `AUTO_ROUTE` default-on; `/model` pin overrides routing.
- **Regression:** existing router + dispatch tests stay green.

## Out of scope (this spec)

- CLI cosmetic redesign (Spec 2): session boxes, stats line, badge visual styling,
  toolset block. This spec only changes routing behavior and the *content* of the hedge
  signal, not its final visual treatment.
- Unified router/gateway rewrite (Approach C).

## Files touched

| File | Change |
|---|---|
| `config/health.py` | **new** — HealthRegistry + circuit breaker |
| `config/router.py` | edit — `FAST_BACKSTOP` ladder, health-aware backstop |
| `providers/_ttft_watchdog.py` | extend — `ttft_guard(budget)` |
| `cli/crowe_logic.py` | edit — wire guard, default-on routing, explicit badge |
| `tests/test_health.py` | **new** |
| `tests/test_router.py` | extend — backstop ladder cases |
| `tests/test_ttft_watchdog.py` | extend — `ttft_guard` cases |
