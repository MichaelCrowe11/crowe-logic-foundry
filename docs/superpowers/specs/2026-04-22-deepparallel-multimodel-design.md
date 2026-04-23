# DeepParallel Multimodel: Heterogeneous 8-Chain Reasoning with Judge Synthesis

**Date:** 2026-04-22
**Status:** Approved for implementation
**Owner:** Michael Crowe / Crowe Logic Inc
**Supersedes:** n/a (additive to existing `tools/deepparallel.py`)

## Context

`tools/deepparallel.py` currently produces "8 reasoning chains" by instructing a single local model (`Mcrowe1210/DeepParallel:latest`, Llama 3.2 3.2B Q4_K_M) to apply eight personas in one completion. This is a prompt-engineering split, not parallel inference. All eight chains share the same weights, the same training data, and therefore highly correlated failure modes.

This spec defines a sibling tool, `multimodel_parallel_query`, that runs eight genuinely parallel inferences across heterogeneous backends (Kimi K2.6 on Ollama Cloud, GLM 5.1 via CROWE_OPEN_ENDPOINT, local DeepParallel), then synthesizes the outputs through a judge model (Claude Opus 4.7 / CroweLM Supreme). The existing tool is not modified and continues to serve hot-path callers (Studio shot-selector, bulk inference) that need cheap single-model reasoning.

## Goals

1. Produce a sibling tool that runs 8 parallel inferences across at least three distinct backends.
2. Preserve the eight-persona framing (analytical, creative, critical, synthesis, empirical, theoretical, practical, meta-cognitive) by mapping one persona per chain.
3. Synthesize chain outputs through a judge model that surfaces disagreement as signal, not noise.
4. Enforce a per-call budget ceiling before any request fires.
5. Log every call to a JSONL ledger sufficient to drive the valuation report in phase 7.
6. Ship named presets (`fast`, `cloud-balanced`, `deep`, `max`) so cost tiers are callable without code changes.
7. Keep the existing `deepparallel_query` path untouched and regression-free.

## Non-goals

1. Not wiring into the CroweLM tier router (Supreme/Titan/Apex). That is a later phase once evidence justifies it.
2. Not wiring into Studio pipelines. Studio continues to use `deepparallel_query`.
3. Not supporting vision or multimodal inputs. Text-only prompt and text-only output.
4. Not building a Prometheus exporter or live dashboard. The JSONL ledger plus a CLI summary command is enough for phase 1.
5. Not moving GLM 5.1 to Ollama Cloud. It stays on `CROWE_OPEN_ENDPOINT`. A separate future ticket can add an Ollama route if Zhipu publishes one.

## Assumptions locked

| Decision | Value | Rationale |
|---|---|---|
| Primary use case | Experimentation harness (CLI + module), followed by on-demand deep reasoning tool | Need evidence before committing to production integration |
| Backend mix default | Kimi K2.6 + GLM 5.1 + local DeepParallel | Three transports exercise the adapter pattern; local leg is free baseline |
| Synthesis default | Claude Opus 4.7 as judge | Strongest reasoner arbitrates; avoids paying Claude on every chain |
| 8-persona framing | One persona per chain | Preserves continuity with existing DeepParallel branding |
| Budget ceiling | $0.50 per call default, configurable | Prevents runaway spend during experimentation |
| Location | `crowe-logic-foundry/tools/multimodel_parallel.py` + `tools/parallel/` submodules | Sibling to existing tool, clean separation |
| Python version | 3.11 | Project standard |

## Architecture

```
Caller (CLI or Python)
     |
     v
Dispatcher             loads config, plans 8 (backend, persona) chains,
     |                 runs pre-dispatch budget gate
     |
     +---> Transport Adapters (async, pluggable)
     |       OllamaAdapter              local + cloud (:cloud suffix)
     |       OpenAICompatAdapter        CROWE_OPEN_ENDPOINT
     |       AnthropicAdapter           Claude, used by synthesis
     |
     v
Collector              asyncio.gather, per-chain timeout,
     |                 drop failures, require N_MIN=3 survivors
     |
     v
Synthesis Layer        judge (default) | vote | debate
     |
     v
Ledger (JSONL)  +  ParallelResult returned to caller
```

Each boundary is a separate module, under roughly 200 lines, independently testable with mocked transports.

## Module structure

```
crowe-logic-foundry/
  tools/
    multimodel_parallel.py          public API: multimodel_parallel_query, ParallelResult, ChainResult
    parallel/
      __init__.py
      backends.py                   transport adapters (Ollama, OpenAICompat, Anthropic)
      personas.py                   8 persona prompt fragments
      synthesis.py                  judge, vote, debate implementations
      ledger.py                     JSONL writer and reader
      configs.py                    named preset loader
      costs.py                      per-backend price table for budget estimation
  cli/commands/
    parallel.py                     lfcli parallel query, lfcli parallel ledger
  tests/
    test_multimodel_parallel.py
    test_backends.py
    test_synthesis.py
    test_ledger.py
    test_budget.py
  docs/superpowers/specs/
    2026-04-22-deepparallel-multimodel-design.md      (this file)
    2026-04-22-deepparallel-valuation.md              (written in phase 7)
```

## Public API

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ChainResult:
    backend: str                      # e.g., "kimi-k2.6:cloud"
    persona: str                      # e.g., "analytical"
    text: str                         # chain output (empty on error)
    cost_usd: float                   # measured or estimated
    latency_ms: int                   # wall time for this chain
    error: str | None = None          # transport or model error message

@dataclass(frozen=True)
class ParallelResult:
    synthesized_answer: str
    chains: tuple[ChainResult, ...]
    synthesis_metadata: dict          # {judge_model, disagreement_notes, confidence, fallback?}
    total_cost_usd: float
    total_latency_ms: int
    dropped_chains: tuple[str, ...]   # "backend:persona" strings that failed
    ledger_id: str                    # UUID for looking up this call later

def multimodel_parallel_query(
    prompt: str,
    config: str = "cloud-balanced",
    synthesis: str = "judge",         # "judge" | "vote" | "debate"
    budget_usd: float = 0.50,
    timeout_s: float = 60.0,
    system: str = "",
) -> ParallelResult: ...
```

The public API surface is one function plus two dataclasses. Internals (adapters, synthesis strategies, ledger) are not part of the public API and can evolve.

## Named configurations

Presets live in `tools/parallel/presets.json` so new mixes can be A/B tested without code changes.

| Preset | Composition | Est. cost/call | When to use |
|---|---|---|---|
| `fast` | 1x local DeepParallel (single call, all 8 personas in one prompt) | ~$0 | bulk / hot-loop, equivalent behavior to existing `deepparallel_query` |
| `cloud-balanced` | 3 Kimi + 3 GLM + 2 local | $0.15 to $0.30 | default for deep reasoning |
| `deep` | 4 Kimi + 4 GLM | $0.25 to $0.50 | maximum cloud diversity, no local |
| `max` | 2 Kimi + 2 GLM + 2 DeepSeek + 2 local | $0.35 to $0.60 | research, high-stakes, four transports |

Cost estimates are placeholder until phase 6 calibrates against real ledger data.

## Data flow (one call, `cloud-balanced` preset)

1. Caller invokes `multimodel_parallel_query(prompt, config="cloud-balanced")`.
2. Dispatcher loads the preset: 3 Kimi chains, 3 GLM chains, 2 local chains. Personas are assigned round-robin from the canonical 8-persona list.
3. Cost estimator multiplies configured `max_tokens` by per-backend price (from `costs.py`) and sums across planned chains. If the estimate exceeds `budget_usd`, `BudgetError` is raised before any request fires.
4. `asyncio.gather` dispatches eight concurrent transport calls. Each chain has a per-chain timeout equal to `timeout_s / 2` so a slow backend cannot stall the whole batch.
5. Collector iterates results. Successful chains go forward. Failed chains (transport error, 4xx, timeout) are recorded in `dropped_chains` and skipped. If fewer than three chains succeeded, the function returns early with `synthesis="skipped"` in metadata and no judge call.
6. Synthesis layer runs. For `judge` mode: Claude Opus 4.7 receives the original prompt plus every surviving chain output labeled by backend and persona. The judge returns a synthesized answer, a disagreement map, and a confidence score.
7. Ledger writes one JSONL record containing: prompt hash, config name, per-chain results, synthesis output, total cost, total latency, timestamp, and a UUID `ledger_id`.
8. `ParallelResult` is returned to the caller.

## Error handling

| Failure mode | Handling |
|---|---|
| Per-chain 5xx or timeout | Transport adapter retries 3x with exponential backoff and jitter, then marks chain as dropped |
| Per-chain 4xx | Immediate drop, no retry; error surfaced in `ChainResult.error` |
| Budget estimate exceeds ceiling | `BudgetError` raised before dispatch, zero spend |
| Fewer than 3 survivors | Return partial result with `synthesis_metadata.synthesis="skipped"`, no judge call |
| Judge fails | Fallback to labeled concatenation of surviving chains, `synthesis_metadata.fallback=True` |
| Backend env var missing | Adapter logs warning at startup, removes backend from available pool; presets auto-rebalance or fail at config load time |

Errors are never silent. Every failure appears either in the return value or the ledger, usually both.

## Observability

- **Ledger file:** `~/.crowe-logic/ledger/parallel/YYYY-MM-DD.jsonl`. One line per call, one file per day.
- **CLI summary:** `lfcli parallel ledger --since 1d` prints a table with call count, total cost, p50 and p95 latency, per-backend success rate, and a sampled disagreement-pattern list.
- **Per-call inspection:** `lfcli parallel show <ledger_id>` prints the full record including every chain's output.
- **Metrics exporter:** deferred to a later iteration. Ledger plus CLI is sufficient for phase 1 decision-making.

## Testing strategy

**Unit tests (pytest, offline, mocked transports):**

- `test_backends.py`: each adapter with `httpx_mock`; success, 4xx, 5xx-retry, timeout, connection error.
- `test_synthesis.py`: judge with canned chain outputs; vote with tied and untied inputs; debate with 2-round convergence.
- `test_budget.py`: estimator arithmetic; boundary at exact budget; over-budget raises before dispatch.
- `test_multimodel_parallel.py`: end-to-end with all adapters mocked; partial-survivor flows; dropped-chain accounting; ledger write.
- `test_ledger.py`: write and read round-trip; malformed line tolerance on read.

**Integration tests (pytest, online, opt-in):**

- Local DeepParallel only (free, run on every CI commit that touches this module).
- Live cloud integration behind `CROWE_RUN_LIVE_TESTS=1`: 50-token prompt against real Kimi Cloud and GLM endpoint, asserts cost stays under $0.02 and latency under 15s. Not run in default CI.

**Cost-regression test:** run `cloud-balanced` against recorded-fixture transports with a fixed prompt, assert ledger totals within tolerance band. Fails the build if a backend silently changes pricing.

## Execution phases

Each phase ends with a green test suite and one commit. Later phases do not depend on earlier ones being re-worked.

1. **Scaffold plus transport adapters.** Module skeleton, three adapters (Ollama, OpenAICompat, Anthropic), unit tests with mocked HTTP. Verifies each transport talks to its service correctly in isolation.
2. **Dispatcher plus cost gate plus persona rotation.** Chain planning, budget estimation, pre-dispatch rejection. All adapters mocked.
3. **Collector plus partial-result handling.** `asyncio.gather`, per-chain timeout, drop-on-failure, minimum-survivor threshold.
4. **Synthesis layer.** Judge mode first (Claude adapter). Vote and debate modes behind the same interface in the same phase.
5. **Ledger plus CLI.** JSONL writer, reader, `lfcli parallel query`, `lfcli parallel ledger`, `lfcli parallel show`.
6. **Live integration tests plus cost calibration.** Run `cloud-balanced` and `deep` against real endpoints with a curated prompt set. Measure actual cost and latency. Update `costs.py` price table with real numbers. Populate 20 to 50 ledger records for valuation analysis.
7. **Valuation report.** Written from real phase-6 ledger data. Lives at `docs/superpowers/specs/2026-04-22-deepparallel-valuation.md`. See outline below.

## Valuation report (phase 7 deliverable)

Separate document. Written after phase 6 because honest valuation requires real ledger numbers, not projected estimates.

**Structure:**

1. **Product definition.** What `multimodel_parallel` is, what category it competes in (structured reasoning tools, ensemble AI, judge-based synthesis systems), what it is not (not a single-model alternative to GPT-4 or Claude).
2. **Competitive landscape.** OpenAI o1 and o3 thinking, Claude extended thinking, Constitutional AI methods, research debate and consensus systems (e.g., Debate Game, Society of Minds). Where this product sits among them.
3. **Technical moat.** Four-line defense: heterogeneous ensembling, judge synthesis, cost-tiered configs, local-leg privacy and baseline.
4. **Use cases and target customers.** Internal Crowe Logic usage (strategic planning, research synthesis, compliance review). External segments: other agent-framework builders (license), verticals like legal research, medical synthesis, pharmaceutical reasoning.
5. **Cost model.** Real numbers from phase 6 ledger. Cost per call for each preset. If an eval set is available, cost per quality point.
6. **Revenue models.** Three candidates: internal efficiency (hours saved), SaaS (tiered per-call pricing), license (per-seat in third-party agent frameworks).
7. **Comparables and valuation range.** DCF with conservative usage projections. Comparable-multiple method if suitable public comparables exist.
8. **Risk register.** Cost runaway, vendor rate limits, synthesis failure modes, model deprecation, GLM 5.1 Ollama availability gap, Claude pricing changes affecting judge costs.

## Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Cost runaway during experimentation | High | Medium | Per-call budget ceiling, daily ledger cost rollup, hard-fail if daily spend exceeds configurable cap |
| Judge becomes single point of reasoning failure | Medium | High | Fallback to labeled concatenation on judge failure; debate mode as alternative that does not require a strong judge |
| Kimi or GLM rate limits in production | Medium | Medium | Per-backend independent rate limit tracking; presets degrade gracefully to local |
| Vendor deprecation of a listed model | Medium | Medium | Adapter abstraction means swap is a config change, not a code change |
| Ledger growth | Low | Low | Daily file rotation, 90-day retention policy |
| Regression in existing `deepparallel_query` | Low | High | New code is a separate module. Existing module is not touched. Integration tests continue to run against `deepparallel_query` unchanged |

## Open questions

None. All design decisions are locked as of approval on 2026-04-22.

## References

- `tools/deepparallel.py` v0.2.8 (existing single-model tool, unchanged by this work)
- `config/agent_config.py` (backend registrations; see entries for `kimi-k2.6:cloud`, `z-ai/glm-5.1`, `claude-opus-4-7`)
- `STUDIO_CHANGELOG.md` v0.7.0 (existing tool shipped in shot-selector)
