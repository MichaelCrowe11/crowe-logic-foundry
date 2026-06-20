# Factory.ai Droid — Comparison & Evolution Plan

**Date:** 2026-06-19
**Thesis:** *Evolve, don't copy.* crowe-logic is not behind Droid on capability —
it's behind on **activation**. Several powerful subsystems were written and never
wired in ("overlooked and not compiled"), and several installed dependencies are
unused. The plan is to surface that latent capital and complete it into
distinctively-Crowe workflows, rather than reimplement Droid feature-for-feature.

---

## 1. Honest comparison (crowe-logic vs Droid)

| Dimension | Droid | crowe-logic | Verdict |
|---|---|---|---|
| Terminal agent loop | ✓ multi-turn, approval-gated | ✓ multi-turn, streaming | **par** |
| Tool count | ~50–200 | **218** across 32 modules | **ahead** |
| Model routing | manual `/model` + reasoning dial; **no auto-router** | **Synapse Router** (heuristic, per-turn, 12-tier) | **ahead** (we auto-route) |
| Multi-vendor models | ✓ one subscription, BYOK | ✓ Azure/Anthropic/WatsonX/NVIDIA/Ollama/openai_compat | **par** |
| Headless / scriptable | ✓ `droid exec` (json, session id) | ✓ `cli/headless.py` (line-JSON events) | **par** |
| OpenAI-compatible endpoint | (via SDKs) | ✓ `cli/openai_bridge.py` — **but dark** | **latent** |
| Spec-first / plan mode | ✓ Specification Mode (read-only, separate plan model) | ✗ | **gap** |
| Graduated autonomy dial | ✓ `--auto` default/low/med/high | ✗ (per-tool only) | **gap** |
| Parallel / delegation | git-worktree fan-out; **in-mission parallel is experimental** | `parallel_dispatcher` + `dual_mode` + DeepParallel — **mostly dark** | **latent (and arguably deeper)** |
| Per-turn tool caching | (n/a) | `cli/tool_cache.py` — **dark** | **latent** |
| File-config convention | ✓ `.factory/` (AGENTS.md, droids, memory) | partial (`agents/*.yaml`, session runtime) | **gap** |
| Persistent memory | honest "none" + hooks→markdown | session-scoped only | **gap** |

**Takeaway:** Droid's real, copyable edges are *workflow primitives* (spec-mode,
autonomy dial, config convention). But for **parallelism — their most-hyped
feature — we already own more than they ship**; it was just never turned on.

> Sources: docs.factory.ai (spec-mode, droid-exec, missions, byok, choosing-your-model),
> factory.ai/news/terminal-bench, Latent.Space founder interview. Factory's
> Terminal-Bench #1 (58.75%) and "harness beats model" are their own un-audited
> claims; in-mission parallel agents are experimental by their own admission.

---

## 2. Latent-asset inventory ("overlooked and not compiled")

Discovered by scanning which `cli/` modules are imported by nothing and which
installed packages are imported nowhere.

### Dormant code (written, unwired)
| Asset | State | What it is |
|---|---|---|
| `cli/parallel_dispatcher.py` | `referenced_by=0` | Fan-out/fusion engine; built to consume `RouteDecision.companions`. `ensemble_synthesis` mode was `NotImplementedError`. |
| `cli/tool_cache.py` | `referenced_by=0` | Per-turn tool-call memoization; written to fix a real observed double-call bug (`deepparallel_query` twice in one turn). |
| `cli/openai_bridge.py` | `referenced_by=0` | FastAPI OpenAI-compatible endpoint wrapping the agent loop — lets any OpenAI client drive crowe-logic. Uses fastapi/uvicorn (already deps). |
| `cli/dual_mode.py` | `referenced_by=1` | 530-line dual-model orchestrator with synthesis (`_run_synthesis_turn`, `SYNTH_PROMPTS`). Barely wired. |
| `tools/deepparallel.py` | tool-only | 8-cluster multi-lineage orchestration — hand-rolls its own retry/backoff. |

### Installed-but-unused dependencies
| Package | Installed | Imported in | Opportunity |
|---|---|---|---|
| `tenacity` | yes | **0 files** | Replace hand-rolled retry in `deepparallel.py`/providers with declarative `@retry`. |
| `anyio` / `aiohttp` | yes | 0 files | Async fan-out path for the dispatcher (beyond threads). |
| `pydantic` | yes | 24 files | Already used — extend to structured tool/route schemas. |
| `networkx` | **absent** | — | Not needed; avoid adding to mimic competitors. |

---

## 3. Shipped this pass — CroweLM Ensemble (latent → live)

**Activated `parallel_dispatcher` + completed its `ensemble_synthesis` fusion**:
fan one question across N CroweLM tiers in parallel, then a synthesizer tier
fuses them into one authoritative answer. This is *reasoning* ensemble across the
12-tier stack — complementary to (and arguably deeper than) Droid's worktree fan-out.

- `cli/parallel_dispatcher.py`: implemented `ensemble_synthesis` (was a reserved
  `NotImplementedError`); added `synthesize` adapter, `build_synthesis_input`,
  `DEFAULT_ENSEMBLE_SYNTH_PROMPT`. Fixed a latent ordering bug (results now
  reassemble in stable submission order, honoring the module's own docstring).
- `cli/ensemble.py`: `run_ensemble()` orchestration with dependency-injected
  provider adapters (default = real stack via `resolve_model_config` +
  `_get_provider_for_dual`, mirroring dual_mode's proven call). Strategies:
  `merge` / `judge` / `diff`. Standalone `python -m cli.ensemble`.
- `crowe-logic ensemble "<q>" -m supreme,oracle,prime -s merge`: new CLI command.
- Tests: `tests/test_parallel_dispatcher.py` (11) + `tests/test_ensemble.py` (7),
  all fakes — **18 pass, no network**. Both entry points register; existing
  dispatch/router tests unaffected.

**Verification boundary (honest):** the orchestration + fusion + command path are
verified with injected fakes. A **live** multi-tier run needs provider credentials
(Azure / funded tiers) and was not executed in this environment.

---

## 4. Punch-list — remaining latent assets to activate (prioritized)

1. **Auto-fan-out in chat** — wire the Synapse Router to emit `companions`, so
   high-stakes turns automatically ensemble (the dispatcher is now ready). The
   router was designed for this; only the wire is missing.
2. **Activate `tool_cache`** — wrap `_execute_tool_call` with per-turn memoization
   (kills the documented double-call waste). Small, pure, high-ROI.
3. **Light up `openai_bridge`** — expose `crowe-logic serve` so any OpenAI client
   (Cortex, LangChain, SDKs) drives the agent. Deps already present.
4. **Consolidate retry on `tenacity`** — replace hand-rolled backoff in
   `deepparallel.py` and providers; installed, unused.
5. **Specification Mode (evolve, not copy)** — a read-only autonomy tier + plan
   model + approval gate, built on a reusable tool-permission gate that also gives
   us the graduated-autonomy dial. The one genuine Droid idea worth adapting.
