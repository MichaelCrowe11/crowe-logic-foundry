# CroweLM Routing Reliability & Optimization — Design

- **Date:** 2026-05-30
- **Repo:** `crowe-logic-foundry`
- **Branch:** `feat/ollama-nexus-failover-azure-gpt55`
- **Status:** Approved (design); pending implementation plan
- **Scope:** Spec 1 of 3 — routing reliability. Spec 2 = full-roster deployment +
  scale-to-zero (Azure Foundry). Spec 3 = CLI cosmetic redesign. Built in that order.

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
- **Multi-provider is the priority.** The router spans providers as first-class peers:
  **Azure AI Foundry** (`AZURE_CORE`, the primary deploy + routing surface) and
  **NVIDIA NIM** (`NVIDIA_NIM_ENDPOINT`, live key). Provider *diversity* is a resilience
  feature, not an afterthought.
- **Local is Mike-only.** Local Ollama weights (`crowelm-unified-v2`, `gemma-4-mycelium`,
  `mike-clone`) are a **personal lane gated out of the customer routing path** — never a
  backstop tier for shipped routing.
- **Default routing:** default-on; `CROWE_LOGIC_AUTO_ROUTE=0` disables; `/model <alias>` pins a tier.
- **TTFT budget:** ~5s balanced — kills 18s hangs while tolerating normal cold starts.
- **Backstop:** strategic **multi-provider** ladder — healthiest fast Foundry/NIM tier →
  mid tiers → … → Supreme only as absolute last resort. No local in the customer path.
- **Provider diversity policy:** **same-provider-first, then cross.** On failure, try
  same-provider fallbacks first (warm/cheaper); cross to another provider only when the
  whole provider looks down.
- **Supreme backing:** repoint `CroweLM Supreme` from the unset `AZURE_ANTHROPIC` endpoint
  to a **live Azure frontier** (`gpt-5.5` primary, `gpt-5.4-pro` same-provider fallback).
  Restore Claude-Opus-on-Azure later when `AZURE_ANTHROPIC_*` creds exist (follow-up).
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
  candidate ladder** (not raw `MODEL_CHAIN`), **same-provider-first then cross-provider**.
- Flip `_auto_route_enabled()` to default-on (env `=0` disables).
- Replace the silent "Model failed — switching to…" banner with an intentional badge that
  states the reason (timeout / error / unavailable) and the target tier.
- Gate the **Mike-only local lane**: local-provider tiers are excluded from customer
  auto-routing; selectable only under a personal flag/identity.

### `config/agent_config.py` (edit)

- Repoint `CroweLM Supreme` from `AZURE_ANTHROPIC` (unset) to `gpt-5.5` on `AZURE_CORE`
  (`provider: azure_openai`), with `gpt-5.4-pro` as same-provider fallback. Leave a comment
  marking the Anthropic-on-Azure restoration as a follow-up.

### `providers/` (edit — gpt-5 param correctness)

- Ensure the Azure OpenAI provider sends `max_completion_tokens` (not `max_tokens`) and
  omits unsupported `temperature` for the gpt-5 family, so gpt-5 tiers don't 400 → false
  fallback. Add a focused test.

## The strategic backstop ladder (multi-provider)

Built from **empirically-live** tiers (see Deployment Readiness Findings), ordered
same-provider-first then cross-provider. No local tier in the customer path.

```
FAST_BACKSTOP =
    # fast floor — Azure Foundry first (warm, primary surface)
    grok-4-1-fast-non-r (Azure)            # ✅ 200 @ 0.73s
    → gpt-5.4-nano / gpt-5.4-mini (Azure)  # ✅ 200 @ ~1.1s
    # cross-provider fast floor (NIM) — used when Azure provider looks down
    → Talon Nano (NVIDIA NIM)              # nemotron-3-nano-30b
    # mid tiers
    → Llama-4-Scout / Kimi-K2-6 (Azure)    # ✅ 200 @ ~0.8s
    → DeepSeek-V4-Flash (Azure)            # ✅ 200 @ 1.6s
    → CroweLM Talon (NVIDIA NIM)           # cross-provider mid
    # frontier — absolute last resort
    → CroweLM Supreme = gpt-5.5 (Azure)    # repointed; was unset AZURE_ANTHROPIC
```

**Provider-diversity policy:** within a tier band, exhaust same-provider candidates before
crossing providers; cross immediately if the health registry shows the whole provider down.

**Local lane (Mike-only):** `crowelm-unified-v2`, `gemma-4-mycelium`, `mike-clone` are
reachable via Ollama but are **excluded from `_auto_route_available`** for customer routing.
They're selectable only under a personal flag/identity gate, never as a backstop.

## Deployment readiness findings (probed 2026-05-30)

Live resource: `crowelm-prod-eastus2.cognitiveservices.azure.com` (`AZURE_CORE`). The
data-plane deployment-list endpoint 404s (AI-Services resource, not OpenAI data-plane), so
liveness was probed per-deployment with correct per-family params.

| Backend | Provider | Result | Role in ladder |
|---|---|---|---|
| `grok-4-1-fast-non-r` | Azure Foundry | ✅ 200 @ 0.73s | fast floor (primary) |
| `gpt-5.4-nano` / `gpt-5.4-mini` | Azure Foundry | ✅ 200 @ ~1.1s | fast floor |
| `gpt-5.5` | Azure Foundry | ✅ 200 @ 1.1s | **Supreme (repointed)** |
| `gpt-5.4-pro` | Azure Foundry | ⚠️ 400 (stricter params) | Supreme fallback — verify in impl |
| `Llama-4-Scout` / `Kimi-K2-6` | Azure Foundry | ✅ 200 @ ~0.8s | mid |
| `DeepSeek-R1-0528 / V3-1 / V4-Flash / V4-Pro` | Azure Foundry | ✅ 200 @ 0.9–1.7s | mid / reasoning |
| `model-router` (Azure-native) | Azure Foundry | ✅ 200 @ 1.7s | candidate routing primitive (see note) |
| Talon family | NVIDIA NIM | live key | cross-provider floor/mid |

**Env gaps that drive the bug:** `AZURE_ANTHROPIC_*` (Supreme), `WATSONX_*` (Sovereign/Nano),
`OPENROUTER_API_KEY` are all **unset**. `AZURE_CORE_*` and `NVIDIA_API_KEY` are set.

**Latent provider-layer bug:** the gpt-5 family rejects `max_tokens`/custom `temperature`
(400) and requires `max_completion_tokens`. The provider layer must send family-correct
params or gpt-5 tiers silently fail and trigger needless fallback. Fixing this is in-scope.

**Azure-native `model-router`:** a live `model-router` deployment exists. Out of scope for
this spec (our heuristic router stays the primary), but noted as a future option to A/B the
heuristic router against Azure's.

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

- **Spec 2 — Full-roster deployment + scale-to-zero** on Azure Foundry: deploy as many
  CroweLM roster + curated catalog models as possible on token-billed/serverless SKUs ($0
  idle), with managed-compute-only models handled via deploy-on-demand + idle reaper. This
  routing spec *consumes* whatever Spec 2 deploys (the health registry picks it up
  automatically); it does not itself deploy anything.
- **Spec 3 — CLI cosmetic redesign:** session boxes, stats line, badge visual styling,
  toolset block. This spec only changes routing behavior and the *content* of the hedge
  signal, not its final visual treatment.
- Adopting the Azure-native `model-router` deployment as the primary router (future A/B).
- Unified router/gateway rewrite (Approach C).

## Files touched

| File | Change |
|---|---|
| `config/health.py` | **new** — HealthRegistry + circuit breaker |
| `config/router.py` | edit — multi-provider `FAST_BACKSTOP` ladder, health-aware backstop |
| `config/agent_config.py` | edit — repoint Supreme → `gpt-5.5` (Azure); same-provider fallback `gpt-5.4-pro` |
| `providers/_ttft_watchdog.py` | extend — `ttft_guard(budget)` |
| `providers/` (azure_openai) | edit — gpt-5 family param correctness (`max_completion_tokens`) |
| `cli/crowe_logic.py` | edit — wire guard, default-on routing, same-provider-then-cross fallback, local-lane gate, explicit badge |
| `tests/test_health.py` | **new** |
| `tests/test_router.py` | extend — multi-provider backstop ladder cases |
| `tests/test_ttft_watchdog.py` | extend — `ttft_guard` cases |
| `tests/` (provider params) | **new/extend** — gpt-5 sends `max_completion_tokens` |
