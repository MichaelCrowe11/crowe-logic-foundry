# Foundry v-next — Architecture

Status: PROPOSED · 2026-06-11
Scope: crowe-logic-foundry 0.5 → 1.0 ("Foundry v-next")
Inputs: full-repo structural survey (main @ 3e8547d), in-flight branch
`feat/crowe-code-blocks` (+5), Cortex `feat/mobile-m0`, Quality Stack spec
(2026-04-30), crowe-stream v0 protocol doc.

---

## 1. Where we are

The current system works and is well-tested (103 test files, clean
dependency graph, zero TODO debt), but its growth pattern has concentrated
intelligence in three monoliths:

| File | Lines | Owns |
|---|---|---|
| `cli/crowe_logic.py` | 4,263 | entry, routing, session, fallback, branding, transcript |
| `config/agent_config.py` | 1,931 | model chain, 50+ system prompts, env wiring, flags |
| `control_plane/__init__.py` | 1,766 | FastAPI app, auth, workspaces, billing |

Three consequences:

1. **The router is trapped in the CLI.** The gateway re-implements a
   subset; the MCP server gets a third behavior. Same question, three
   answers depending on the door you walked in through.
2. **The wire protocol is almost-but-not-quite shared with Cortex.**
   crowe-stream v0 (headless) and CSEP (Cortex) describe the same events
   with different vocabularies; guardrails still print to stderr instead
   of emitting events.
3. **Two binaries answer to `crowe-logic`** — `crowe-logic` is the
   published PyPI product (0.4.2), but a Node channel-gateway CLI
   (WhatsApp/Telegram messaging, a separate product) ships the same
   binary name and shadows it when npm-linked. Coin-flip identity on
   every machine we set up — and the PyPI product must win.

## 2. Design goals

- **One runtime, many frontends.** CLI, gateway, MCP, and Cortex all
  drive the same core loop through the same protocol. No behavior forks.
- **Smarter routing as a product feature, not a config file.** Routing
  decisions use task class + provider health + cost + plan, and we can
  explain every decision after the fact.
- **Cortex-native.** Foundry is the engine Cortex already shells out to;
  v-next makes that contract first-class instead of incidental.
- **No flag-day.** Every phase ships behind the existing CLI surface;
  nothing breaks for current users mid-migration.

## 3. Target shape

```
┌────────────┐ ┌────────────┐ ┌────────────┐ ┌─────────────┐
│  CLI (thin)│ │ Gateway    │ │ MCP server │ │ Cortex      │
│  cli/      │ │ (FastAPI)  │ │            │ │ (Tauri app) │
└─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └──────┬──────┘
      │              │              │               │
      └──────────────┴──────┬───────┴───────────────┘
                            ▼
              crowe-stream v1  (one event vocabulary,
              NDJSON + SSE framings, CSEP-aligned)
                            │
                ┌───────────▼───────────┐
                │   foundry-core        │
                │  turn loop · session  │
                │  guardrails · memory  │
                └───────────┬───────────┘
                ┌───────────▼───────────┐
                │   synapse router      │
                │  classify → score →   │
                │  select → fallback    │
                └───────────┬───────────┘
                ┌───────────▼───────────┐
                │   providers (10)      │
                │  BaseOpenAIProvider   │
                │  + health ledger      │
                └───────────────────────┘
```

### 3.1 `foundry-core` — extract the turn loop

Pull the engine out of `cli/crowe_logic.py` into a `core/` package with
zero Rich/terminal imports:

- `core/turn.py` — the canonical agentic loop (compose → route → stream →
  tool dispatch → finalize). `cli/headless.py` already *is* this loop in
  embryo; it becomes the only loop, and the interactive CLI renders its
  events instead of owning a parallel one.
- `core/session.py` — session state, provider-thread continuity
  (`agent_threads`), trace ledger. Storage moves from ad-hoc JSON files to
  **SQLite** (`~/.crowe-logic/foundry.db`), mirroring Cortex's migration
  discipline (numbered migrations, v1 schema = sessions, turns, traces,
  router_decisions). JSON import shim reads old runtime files once.
- `core/guardrails/` — relocated pipeline (SecretScrubber, ScopeBudget,
  StyleFilter) that **emits events** (`guardrail.hit`, `error.surface`)
  instead of printing to stderr. The Quality Stack spec's CSEP intent,
  realized.

The CLI keeps: argument parsing, Rich rendering, iTerm integration,
branding. Target: `cli/crowe_logic.py` under 800 lines.

### 3.2 Synapse Router — routing as a first-class subsystem

Today `_BASE_MODEL_CHAIN` is an ordered list and `crowelm-auto` is a
per-turn classifier. v-next upgrades selection from "first healthy in
list" to **scored selection with an explainable record**:

```
score(tier, turn) =
    capability_match(task_class, tier)        # hard gate
  × plan_access(tier, principal)              # hard gate
  × health(tier)            # rolling TTFT/error ledger, per provider
  × cost_weight(tier, budget_mode)            # cheap | balanced | best
  × context_fit(tokens_needed, tier)          # hard gate
```

- **Chain registry leaves Python.** `config/model_chain.yaml` with a
  pydantic schema — provider, endpoint_env, key_env, tier type, aliases,
  capabilities (tools / vision / reasoning / video), context window,
  $/Mtok in+out, plan floor. `agent_config.py` shrinks to a loader.
- **Health ledger.** The `external_traces` bookkeeping already in
  session_runtime becomes a persistent per-provider table (rolling TTFT
  p50/p95, error rate, last failure). Routing reads it; `synapse-doctor`
  and `crowe-logic route` display it.
- **Decision records.** Every routed turn writes `router_decisions`
  (candidates considered, scores, winner, fallback path taken). `route
  --explain PROMPT` replays one. This is the "smarter" that compounds:
  decision records + outcomes become the eval set that tunes the weights.
- **Budget modes.** `--budget cheap|balanced|best` (and per-plan
  defaults) so the same chain serves free-tier mycelium traffic and
  Supreme-tier reasoning without separate code paths.

### 3.3 crowe-stream v1 — one protocol, CSEP-aligned

v0's event set (`ready/token/tool/done/error`) is unified with Cortex's
CSEP vocabulary and extended:

| Event | Purpose |
|---|---|
| `ready` | provider connected; now carries `{tier, provider, decision_id}` |
| `token` | answer delta |
| `reasoning` | reasoning-stream delta (Cortex already renders these) |
| `tool` | invoke/result, with `policy: safe\|gated\|dangerous` |
| `guardrail` | scrub/scope/style hits (replaces stderr prints) |
| `telemetry` | TTFT, token counts, cost estimate — mid-stream tick |
| `route` | fallback hop happened, and why |
| `done` / `error` | terminal |

Framings: NDJSON (headless/CLI), SSE (gateway), Tauri events (Cortex
sidecar). One serializer in `core/stream.py`; the three transports are
adapters. Contract tests pin the schema (golden event fixtures) so
Foundry and Cortex can't drift — the same trick `nav.ts` uses to keep
the tab bar and desktop rail honest.

### 3.4 Packaging — end the name collision

Split the single distribution into a uv workspace:

```
crowe-foundry-core        # turn loop, router, providers, tools, stream
crowe-foundry-gateway     # FastAPI control plane (depends: core)
crowe-foundry-cli         # Rich terminal frontend (depends: core)
```

- **`crowe-logic` stays the Python CLI** — it is the published PyPI
  product (released through 0.4.2) and keeps its name and PyPI listing;
  the workspace split is invisible to `pipx install crowe-logic` users
  (the CLI package depends on core and gateway extras).
- The Node *channel-gateway* CLI (WhatsApp/Telegram messaging — a
  different product that currently also installs as `crowe-logic`) takes
  a distinct binary name in its own repo so it can never shadow the PyPI
  product on a dev machine again.
- `pyproject.toml` version (0.3.0) and release version (0.4.2) are
  already out of sync — single-source the version from one place as part
  of the split.
- The npm wrapper in this repo keeps working: it shells to `crowe-logic
  headless` and speaks crowe-stream v1.

### 3.5 Tools & agents — manifests over lists

The decorator registry stays (it's good). Changes:

- **Capability manifests.** Each `agents/*.yaml` declares tool families +
  a policy tier per family (`safe`/`gated`/`dangerous`), matching the
  policy classification Cortex already applies to `notebook_*`. Dispatch
  enforces it centrally instead of per-frontend.
- **Schema budget.** The 128-tool-schema cap (3f17984) becomes a
  first-class constraint: the router knows each tier's schema budget and
  the manifest picker packs to fit, preferring the active agent's
  families.
- **Notebook family lands with core.** The in-flight
  `feat/crowe-code-blocks` branch (notebook_* via Cortex kernel host,
  Crowe Code block bridge) merges before the extract begins — it touches
  `cli/crowe_logic.py` and would conflict with the split.

### 3.6 Memory — one interface, two tiers

`crowe_synapse_engine/memory.py` and session_runtime currently overlap.
v-next: a single `core/memory.py` interface with two tiers — **session**
(SQLite, always on) and **semantic** (optional embedding store, feeds the
KB/mycelium mounts Cortex grew in `dc2eb83`). The provider-thread
continuity map is session-tier. Cortex's mycelium and Foundry's memory
stop being parallel inventions and share the mount vocabulary from the
context provider registry (`648f752`).

## 4. What gets deleted

- The parallel interactive loop in `cli/crowe_logic.py` (headless loop
  becomes canonical).
- stderr guardrail printing.
- Ad-hoc JSON session files (after one-shot import).
- The gateway's private routing subset (it calls the Synapse Router).
- X-402/wallet/settlement code — already removed on the in-flight branch;
  confirm it never comes back with the merge.

## 5. Migration phases

Each phase is shippable and reversible; no phase changes the user-visible
CLI contract until Phase 4.

| Phase | Deliverable | Risk | Gate |
|---|---|---|---|
| 0 | Merge `feat/crowe-code-blocks`; push Air's 2 local commits | low | tests green |
| 1 | Extract `core/` (turn loop, session, guardrails-as-events); CLI renders events | med | golden-transcript tests: CLI output byte-identical |
| 2 | Synapse Router: YAML chain registry + health ledger + decision records; `route --explain` | med | shadow mode first — score, log, but follow old chain; flip when decisions agree >95% or beat on TTFT |
| 3 | crowe-stream v1 + SQLite store; gateway and MCP move onto core; Cortex consumes v1 | med | contract tests shared with crowe-cortex repo |
| 4 | Workspace split + `foundry` binary + version single-sourcing | low | pipx/npm install matrix on both Macs |

## 6. Open questions

1. **Router learning loop** — decision records give us the dataset; do we
   tune weights by hand first (yes, Phase 2) and defer learned weights to
   a later eval-harness phase? (Proposed: yes.)
2. **Gateway DB** — control plane implies Postgres in production while
   core uses SQLite locally. Keep the split (core = SQLite, control
   plane = Postgres) or unify on Postgres-only for hosted? (Proposed:
   keep the split; core must work offline.)
3. **DeepParallel exposure** — stays internal-only, or becomes a routable
   tier with an honest 150–250s latency warning event in v1? (`route`
   event makes the warning surfaceable now.)
4. **Where do agents/*.yaml live long-term** — foundry-core or a shared
   spec repo Cortex also reads? Cortex's persona registry wants the same
   data.

## 7. Non-goals

- No rewrite of providers — `BaseOpenAIProvider` is earning its keep.
- No new frontend frameworks; Cortex is the GUI.
- No protocol break for current npm-wrapper consumers during 0.x.
