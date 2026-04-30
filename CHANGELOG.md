# Changelog

All notable changes to Crowe Logic Foundry are documented here.
Versions follow [Semantic Versioning](https://semver.org/).

## [0.3.0] — 2026-04-30

The Synapse release. Adds a confidence-gated routing layer in front of
the multi-tier model chain, rewrites the workflow-discipline rules that
caused reasoning models to spend most of a turn litigating prompt
interpretation, and ships an inspection CLI for live debugging.

### Added

- **Synapse Router** (`config/router.py`). Heuristic per-turn classifier
  with confidence calibration across 9 intent labels. Returns a
  `RouteDecision` with `confidence ∈ [0, 1]` and a `low_confidence` flag.
  Decisions below `LOW_CONFIDENCE_THRESHOLD` (0.60) are flagged for
  promotion review.
- **Per-turn auto-routing** behind `CROWE_LOGIC_AUTO_ROUTE=1`. When
  enabled, every user prompt classifies and silently swaps the active
  chain index to the routed tier. Prints a one-line "→ Synapse:
  <intent> → <Tier> (conf=X.XX)" badge on tier swaps and low-confidence
  decisions.
- **DeepParallel fallback** (`config/synapse_fallback.py`) behind
  `CROWE_LOGIC_SYNAPSE_FALLBACK=1`. When the heuristic confidence falls
  below threshold, the prompt is re-classified by a locally-hosted
  multi-chain reasoning model on Ollama (default
  `Mcrowe1210/DeepParallel:v2.2`). Override the model with
  `CROWE_LOGIC_SYNAPSE_FALLBACK_MODEL`, the base URL with
  `CROWE_LOGIC_SYNAPSE_FALLBACK_BASE_URL`, the timeout with
  `CROWE_LOGIC_SYNAPSE_FALLBACK_TIMEOUT_S`. Failures never raise; the
  heuristic decision is kept.
- **Tier-aware system-prompt overlays** (`_TIER_OVERLAYS` in
  `config/agent_config.py`). Each `MODEL_CHAIN` entry's `type` (fast,
  reasoning, vision, code) gets a behavior addendum applied
  automatically by `build_system_instructions()`. Caps internal
  reasoning per tier (fast: ~100 tokens, reasoning: ~600, vision: ~300).
- **Tier-aware runtime params** (`_TIER_RUNTIME_PARAMS`). Per-type
  temperature/top_p/max_tokens auto-applied in
  `BaseOpenAIProvider.stream_response`. Fast: T=0.20, max_tokens=768.
  Reasoning: T=0.60, max_tokens=4096. Vision: T=0.40, max_tokens=2048.
  Code: T=0.20, max_tokens=6144.
- **Response-quality detector** (`config/quality.py`). After every
  assistant turn, `assess_response()` runs cheap heuristics (length
  floor, refusal phrases, hedge-only openers, echoed-question,
  tautology via longest-common-substring). Shallow responses log a
  `synapse_shallow_response` telemetry event. Observe-only today; gates
  future adaptive promotion.
- **In-session tool-call dedupe** in `BaseOpenAIProvider`. Identical
  `(name, args)` calls within a single user turn return a `cached:
  true` result instead of re-executing. Cache resets on each new user
  message.
- **`crowe-logic route <prompt>`** CLI subcommand. Prints the routing
  decision (intent, confidence, selected tier, runtime params, reason)
  without invoking any model. `--json` for machine-readable output.
- **`crowe-logic synapse-doctor`** CLI subcommand. Prints live Synapse
  config (env flags, thresholds, intent confidence ceilings, tier
  preferences, runtime params, fallback config) plus a summary of the
  last N synapse_route + synapse_shallow_response events from
  `~/.crowe-logic/runtime/telemetry.jsonl`. Pure inspection, no model
  invoked.
- **Telemetry foundation** (`config/telemetry.py`). Append-only
  JSON-lines sink at `~/.crowe-logic/runtime/telemetry.jsonl` with
  50MB rotation. Records tool calls, model invocations, system events,
  Synapse decisions, and quality signals.
- **Prompt-aware route telemetry**. `synapse_route` events now include
  `prompt_preview` (first 200 chars) and `prompt_length` so a future
  replay harness can re-classify past prompts through the current
  router.

### Changed

- **Workflow-discipline rules in `SYSTEM_INSTRUCTIONS`** rewritten as
  short positive bullets with no internal contradictions. Removed
  "never narrate intent" and "any prose must describe what you JUST
  did" — the two rules whose unsatisfiable intersection caused
  reasoning models to spend the majority of every turn re-deriving the
  same hypothesis tree. Added explicit "Cap internal deliberation to
  ~200 tokens. Never re-derive the same hypothesis tree twice" rule.
- **`MAX_AUTO_CONTINUES`** lowered 2 → 1. Two created a redundant-call
  loop when the model re-issued identical tool calls after each nudge.
- **`AUTO_CONTINUE_NUDGE`** rewritten so it no longer echoes the "do
  not narrate intent" trigger phrase that the model would re-litigate
  on the next turn.
- **`BaseOpenAIProvider.__init__`** gains an optional `model_cfg`
  attribute. Provider factories assign it after construction so
  tier_runtime_params apply automatically.
- **`_apply_provider_instructions`** centralized — both cache-hit and
  fresh-construction paths flow through it, ensuring `model_cfg` and
  system instructions attach reliably either way.

### Test counts

- v0.2.5 baseline: 282 tests passing.
- v0.3.0 release: 416 tests passing (+134 new across 5 new test files).
- Zero regressions.

### Operator quickstart

```bash
# Inspect the live config (no model invoked)
crowe-logic synapse-doctor

# Test routing on any prompt
crowe-logic route "your prompt here"

# Drive a chat session with auto-routing
CROWE_LOGIC_AUTO_ROUTE=1 crowe-logic chat

# Same plus DeepParallel fallback for ambiguous prompts
CROWE_LOGIC_AUTO_ROUTE=1 \
CROWE_LOGIC_SYNAPSE_FALLBACK=1 \
crowe-logic chat
```

## [0.2.5] and earlier

See git history. Notable predecessors:

- **0.2.5**: Coerce tool-call argument types from LLM string → int/
  float/bool. Fixes intermittent `TypeError: '<' not supported between
  instances of 'int' and 'str'` when smaller models serialize all
  tool-call arguments as JSON strings regardless of declared schema.
- **0.2.4 and earlier**: GLM 5.1 deploy, public records, substrate
  domain, dashboard v3, knowledge plane (pgvector), four domain
  modules (mycology, vision, research, compound discovery), VS Code
  extension scaffolding.
