---
title: Routing and Parallel Dispatch - Design Spec
status: draft (pending user review)
date: 2026-04-30
author: Michael Crowe (with Claude)
sibling-of: 2026-04-30-crowe-cortex-design.md, 2026-04-30-crowelm-quality-stack-design.md
relates-to: config/router.py, cli/parallel_dispatcher.py, cli/session_runtime.py
---

# Routing and Parallel Dispatch

Sibling to **Crowe Cortex** (the surface) and **CroweLM Quality Stack** (the brain). This spec covers the **dispatch layer**: how a single user turn picks a model tier, optionally fans out to a companion provider, fuses the results, and persists cross-provider continuity so the next turn can resume context.

Without this layer, Cortex picks tiers implicitly per turn, single-agent calls take the whole turn hostage when a provider hangs, and Azure thread IDs / DeepParallel calls live in disjoint universes. The Quality Stack guards what comes out; the Routing layer decides who runs and reconciles their answers.

---

## 1. Locked decisions

| Decision | Choice | Notes |
|---|---|---|
| Classifier strategy | **Heuristic-only** | An LLM-based router would add ~500ms TTFB on the 80% of prompts that have an obvious shape. Mis-route cost (e.g. domain prompt to Nano) is much smaller than the always-on TTFB tax. |
| Classifier output | **Single intent label** | One of `arithmetic`, `trivial`, `capability_question`, `vision`, `code`, `domain`, `deep`, `general`, `ambiguous`. Conservative escalation: ties go to the higher tier. |
| Router output shape | **`RouteDecision` dataclass** | Frozen dataclass with `intent`, `primary` (model_cfg), `fallbacks` (tuple), `companions` (tuple for parallel fan-out), `reason`. Serializable via `to_dict()` for logs and operator UI. |
| Side effects in router | **None** | Router does not mutate `MODEL_CHAIN`, `_model_state`, or any global. The current chain-walking pattern in `cli/crowe_logic.py` (`_advance_model`, `_model_state["chain_index"]`) is replaced by stateless `RouteDecision` consumption. |
| Dispatcher provider plumbing | **Caller-supplied `invoke` adapter** | Dispatcher does not import any provider module. Caller passes `invoke(model_cfg, prompt) -> DispatchResult`. Keeps the dispatcher unit-testable and prevents circular imports through `providers/_shared.py`. |
| Concurrency primitive | **`ThreadPoolExecutor` + `as_completed` with `timeout`** | Adequate for 1-5 concurrent providers. If we ever need >50 concurrent calls per turn (we won't), revisit. |
| Cancellation | **None** (best effort) | Python threads cannot be killed safely. On per-turn timeout the dispatcher stops waiting; abandoned in-flight calls are billed when they eventually return (caller's `cost_credits` accounting handles this). |
| Fusion modes (v1) | `primary_only`, `primary_with_fallback`, `present_both` | Three modes earn their keep on day one. `ensemble_synthesis` is reserved for a follow-up that uses an LLM judge over the answers; raises `NotImplementedError` until then. |
| Cross-provider continuity | **Two new fields in `session_runtime.py`** | `agent_threads: dict[agent_id, thread_id]` for Azure Foundry thread reuse; `external_traces: list[dict]` for a bounded log of non-primary provider calls (capped at 25 entries). |
| Coexistence with old router | **Both live for now** | `classify_task` / `TASK_CLASS_ROUTES` / `route_for_auto` in `config/agent_config.py` stay until call sites migrate. Removal is a separate follow-up; this spec does not touch the high-collision `agent_config.py`. |

---

## 2. Why now

The 2026-04-30 transcript with CroweLM Talon driving Azure Foundry agents and DeepParallel concurrently exposed five gaps:

1. **Implicit tier selection.** Same prompt, same model on Tuesday, different model on Wednesday because routing logic lived inside `crowe_logic.py`'s control flow. Inspectability and override were impossible.
2. **No timeout on provider calls.** A single Azure agent call hit `TTFB 337.0s` and held the whole turn. The orchestrator had no per-call timeout.
3. **Azure thread IDs were per-call.** When the user asked "continue this thought with the same agent", they had to manually pass `thread_id`. The orchestrator forgot the agent-to-thread mapping at turn boundaries.
4. **Parallel agent calls quietly merged.** When Azure returned a table and DeepParallel returned narrative for the same question, the orchestrator picked one without exposing that two answers existed. No audit trail.
5. **No declared fusion strategy.** The transcript implied `present_both` was wanted; the orchestrator did `primary_only` by default. The user could not request a different fusion.

This spec lands the routing decision (1), the timeout primitive (2), the cross-provider session glue (3), and the fusion modes (4, 5) in three new modules.

---

## 3. Modules

### 3.1 `config/router.py` (new, ~245 lines)

Stateless prompt classifier and tier resolver.

```python
from config.router import route_prompt

decision = route_prompt(user_input)
# decision.intent     -> "domain"
# decision.primary    -> model_cfg for "CroweLM Apex"
# decision.fallbacks  -> (Titan, Sovereign, Prime) model_cfgs, in order
# decision.companions -> (DeepParallel,) model_cfgs, possibly empty
# decision.reason     -> "intent=domain; primary=CroweLM Apex; 3 fallback(s); 1 companion(s)"
```

Key surfaces:

- `classify_prompt(text) -> str` - heuristic intent label.
- `route_prompt(text, *, chain=None, availability=None) -> RouteDecision` - main entry. `availability(model_cfg) -> bool` is the integration hook for Quality Stack's "is this provider configured?" check.
- `_INTENT_PREFERENCES` - intent to selector list mapping (`tuple[str, ...]` per intent).
- `_INTENT_COMPANIONS` - intent to fan-out companion mapping. Currently `domain` and `deep` get `DeepParallel` as a second opinion. Empty for all other intents (fan-out is opt-in per call site even when companions exist).

**Integration point**: `cli/crowe_logic.py` replaces the `_advance_model` / `_model_state["chain_index"]` walk with a single `route_prompt(user_input)` call. The fallback list comes from `decision.fallbacks` instead of mutating chain state.

### 3.2 `cli/parallel_dispatcher.py` (new, ~225 lines)

Fan-out + fusion. UI-agnostic.

```python
from cli.parallel_dispatcher import dispatch

outcome = dispatch(
    user_input,
    primary=decision.primary,
    companions=decision.companions,
    invoke=my_provider_adapter,
    timeout_s=45.0,
    fusion="present_both",
)
# outcome.fused_answer       -> rendered string
# outcome.results            -> list[DispatchResult] with per-target latency, cost, error
# outcome.successful_results -> filter for ones that produced answers
```

**Adapter contract**: `invoke(model_cfg, prompt) -> DispatchResult`. The caller wires this to whatever provider client they prefer. The dispatcher never imports a provider module, never knows about streaming, never speaks to renderers.

**Timeout semantics**: `timeout_s` is per-dispatch (not per-target). On timeout, in-flight futures are not cancelled (Python threads can't be killed safely); the dispatcher stops waiting and stamps unfinished targets with `error=FutureTimeoutError(...)`. If those calls eventually complete, the caller's cost meter still bills them.

**Fusion modes**:

| Mode | Behavior |
|---|---|
| `primary_only` | Return primary's answer if it succeeded, else first successful companion. Companion answers are dropped but companion results still appear in `outcome.results` for cost accounting. |
| `primary_with_fallback` | Same as `primary_only`. Distinct name because the *intent* differs (caller is using companions as warm fallbacks rather than dropped-by-default). |
| `present_both` | All successful answers concatenated as `### {model_label}\n\n{answer}` sections separated by `---`. The "is the agent right?" comparison mode. |
| `ensemble_synthesis` | Reserved. Raises `NotImplementedError`. |

### 3.3 `cli/session_runtime.py` (extended, +63 lines)

Added two state fields and four helpers. Existing helpers (`load_session_runtime`, `update_session_runtime`, `handle_local_control_command`) untouched.

| New field | Type | Purpose |
|---|---|---|
| `agent_threads` | `dict[str, str]` | `{agent_id: thread_id}` for Azure Foundry thread reuse across turns. |
| `external_traces` | `list[dict]` | Bounded log (cap 25) of `{provider, model, ts, prompt_hash, summary}` for non-primary provider calls. Prompt hashes only - no full prompts in session state. |

| New helper | Signature | Purpose |
|---|---|---|
| `remember_agent_thread` | `(session_id, agent_id, thread_id) -> None` | Persist after every successful Azure agent call. |
| `recall_agent_thread` | `(session_id, agent_id) -> str \| None` | Resume the same thread on the next turn. |
| `record_external_trace` | `(session_id, *, provider, model, prompt_hash, summary)` | Trim to 240 chars, cap list to last 25 entries. |
| `recent_external_traces` | `(session_id, *, limit=5) -> list[dict]` | For the operator UI / `/transcript` extensions. |

**Tool integration**: when `tools.azure_agents.azure_agent_invoke` is called without an explicit `thread_id`, it should consult `recall_agent_thread(session_id, agent_id)` first. After every call, `remember_agent_thread(session_id, agent_id, thread_id)`. This is a one-line change inside the tool wrapper, deferred until Cortex Phase 7.1 picks up the integration.

---

## 4. Test surface

Inline smoke tests run during development (live `.venv`, against `MODEL_CHAIN`):

- `classify_prompt` correctness across 11 representative prompts (one bug found and fixed during the run: substring matching of `"test "` against `"latest "` was misclassifying news prompts as code; switched to space-padded keyword matching).
- `route_prompt('mycelium colonization rate on grain spawn')` resolves to `CroweLM Apex` primary + 3 fallbacks + 1 companion (`DeepParallel`).
- `dispatch(..., fusion='present_both')` with stub `invoke` produces correctly labeled merged sections.
- Default session state contains the two new keys.

**Pytest follow-ups (deferred)**:

- `tests/test_router.py` - 30+ classifier cases covering each intent and each ambiguity boundary.
- `tests/test_parallel_dispatcher.py` - timeout behavior, exception trapping, all three fusion modes, primary-failure-with-companion-success path.
- `tests/test_session_runtime_continuity.py` - `agent_threads` round-trip, `external_traces` bounded growth, JSON serialization.

These three test files match the naming convention used by the existing `tests/` directory and by the Quality Stack branch's new tests (`test_guardrails_*`, `test_eval_rubric.py`, `test_telemetry.py`).

---

## 5. Coexistence with Quality Stack

| Quality Stack module | Routing module | Interaction |
|---|---|---|
| `config/prompt_loader.py` | `config/router.py` | Loader resolves the system prompt for a given `RouteDecision.primary`; router does not load prompts itself. Single direction: router -> primary -> loader. |
| `cli/guardrail_pipeline.py` | `cli/parallel_dispatcher.py` | Guardrail runs **after** dispatch on the fused answer. For `present_both` fusion, guardrail can run per-section before merging or once on the merged output (Cortex Phase 7.1 decides which). |
| `config/telemetry.py` | both | Router emits `routing.decision` event; dispatcher emits `dispatch.outcome` event with per-target latency and cost. Telemetry sink is configured at app boot. |
| `eval/` | `config/router.py` | Eval harness exercises the classifier against a golden corpus of intent labels. Becomes part of the LoRA gate's intent-quality regression check. |

No file conflicts with the in-flight Quality Stack work. Router and dispatcher live in their own files; the only edited file (`cli/session_runtime.py`) is not in the Quality Stack's modified set.

---

## 6. Coexistence with the old `classify_task` system

`config/agent_config.py` currently exports `classify_task`, `TASK_CLASS_ROUTES`, `TASK_CLASS_FALLBACKS`, `route_for_auto`, and `route_candidates_for_auto`. Those are the original Auto-tier routing system used by the `crowelm-auto` model entry.

This spec **does not** modify `agent_config.py` (the high-collision surface). The two systems coexist:

- The old system is invoked when the active model is `crowelm-auto`.
- The new `route_prompt` will be invoked at the start of every turn (regardless of active model) once `cli/crowe_logic.py` is updated. Until that wiring lands, the new modules are inert: they exist on disk and pass tests, but no production call site uses them.

**Removal of the old system** is a follow-up that should be done after Cortex Phase 7.1 (engine extraction) so the renaming churn happens in the Rust core rather than the Python sidecar. Until then, marking the old functions with a deprecation comment in `agent_config.py` is the only change needed there, and that change should be made in coordination with the parallel session that owns Quality Stack.

---

## 7. Open questions

1. **Companion fan-out as default vs opt-in.** The current router populates `companions` for `domain` and `deep` intents. The dispatcher only fans out when fusion mode is `present_both` or `ensemble_synthesis`. Is this the right split, or should `companions` be empty by default and the call site append them when it wants fan-out?

2. **Cost accounting on abandoned futures.** When a companion call times out and is abandoned, the cost meter should still bill it when (if) it returns. The current dispatcher returns immediately on timeout; the eventual completion is invisible to the caller. Need a callback or background settlement queue. Defer to Cortex Phase 7.1.

3. **Per-target timeout vs per-dispatch timeout.** Currently `timeout_s` is per-dispatch (the whole `as_completed` loop). For very different providers (Azure agent: 45s, DeepParallel local: 5s), per-target timeouts would be more honest. Add `timeout_per_target: dict[provider, float]` in v2 if data shows it matters.

4. **Streaming fan-out.** The dispatcher returns a full answer per target. For `present_both` over slow providers, users wait for the slowest target before seeing anything. A streaming variant that emits sections as they arrive would be a large surgery. Defer until we have a `present_both` user demand.

5. **Heuristic vs LLM router escalation.** `_INTENT_PREFERENCES` is hand-curated. A drift-detection job (Quality Stack eval harness) should track classifier accuracy on real traffic; if a class falls below 90% F1, escalate that class to a small LLM classifier. Hand-off plan: out of scope for this spec.

---

## 8. Rollback

All three modules are additive in their first incarnation:

- `config/router.py` and `cli/parallel_dispatcher.py` are new files. Delete to remove.
- `cli/session_runtime.py` adds two state fields and four helpers; the helpers are unused by any existing call site. The fields are filtered out by the existing `update_session_runtime` allowlist if removed from `_default_session_state`. Net behavior: rolling these back is a one-commit revert with no production impact.

The branch name `router-and-parallel` matches the spec name. Suggested merge form: squash to a single commit titled `Add routing and parallel-dispatch layer (sibling to Quality Stack)` with this spec referenced in the commit body.

---

## 9. Wire-in status

2026-04-30: Cortex Phase 7.1 still pending; wire-in deferred. Next check: 2026-05-14 via `trig_01H9nbXroTmgQXziMeyYAYLG`.
