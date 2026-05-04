---
title: CroweLM Quality Stack - Design Spec
status: draft (pending user review)
date: 2026-04-30
author: Michael Crowe (with Claude)
sibling-of: 2026-04-30-crowe-cortex-design.md
relates-to: scripts/fine_tune.py, scripts/eval_prompt_density.py, scripts/lora_phase{1..4}*
---

# CroweLM Quality Stack

Sibling to **Crowe Cortex**. Cortex is the surface (Tauri 2 desktop app, CSEP, BrandVeil, control plane). Quality Stack is the brain (guardrails, system prompts, fine-tunes, eval). Cortex without Quality Stack ships a beautiful window onto a model that leaks secrets and gold-plates trivial requests. Both must land.

---

## 1. Why now

A real CroweLM Eclipse session on 2026-04-30 exhibited eleven distinct quality failures on a four-email blast request:

1. Echoed a Resend API key (`re_*`) in the final answer.
2. Wrote `campaign_blast.py` and `contacts.json` to `~/` root, violating the home-dir safety rule in `/Users/crowelogic/CLAUDE.md`.
3. Used em-dashes throughout, violating the universal `feedback_no_em_dashes.md` MEMORY rule.
4. Built a 4-provider pluggable email service, three SQLAlchemy tables, six REST endpoints, plus a separate runner, for sending four emails total.
5. Self-noticed the overbuild mid-stream ("there's a disconnect, I need to make sure I'm DOING the thing they need, not over-engineering a backend") and continued building the backend anyway.
6. Did not propose scheduling (cron, launchd) until the user asked twice.
7. Tried `python` then fell back to `python3` despite `CLAUDE.md` documenting which Pythons are installed.
8. Reasoning blocks duplicated in the renderer (`REASONING . live` then identical `REASONING . captured`).
9. TTFT of 1,095 seconds on the Ollama cloud endpoint.
10. Claimed "I have delivered" without rendering the email for user approval first.
11. Disclosed the Shopify capability gap only after the user pushed twice.

Ten of eleven defects are addressable without retraining the underlying weights. The eleventh (TTFT) is an endpoint problem upstream of this spec.

## 2. Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| Three-layer model | Guardrails + Prompts + LoRA, with eval gating each | Defense in depth. Eval without fix is theatre. Fix without eval is wishful thinking. |
| Guardrail location | `cli/guardrails/`, runs at the streaming boundary | Same surface CSEP events will emerge from later. One boundary, not many. |
| Per-variant prompt files | `config/system_prompts/<slug>.md`, plus `_base.md` for cross-variant policy | Markdown so non-engineers can edit. Per-file diffs in code review. Easier eval comparisons. |
| LoRA scope | Ollama-backed variants first; Azure FT pipelines extended with eval gate | We control the backend. NVIDIA NIM is not user-tunable. |
| Eval framework | Extend existing `scripts/eval_prompt_density.py` rather than build from scratch | The shape is right. Rubric expansion is the actual work. |
| Touch policy on `config/agent_config.py` | Minimal additive only (one new function plus deprecation shim) | High-collision file per `feedback_parallel_sessions.md` MEMORY rule. |
| Coordination with Cortex spec | Quality Stack lands first in current locations; Cortex rename moves files whole | No merge conflict by sequencing. |

## 3. Architecture

```
        User input
            |
            v
   +--------------------+
   | session_runtime.py |  Entry point: user turn arrives
   +---------+----------+
             |
             v
   +--------------------+
   |  prompt assembler  |  config/system_prompts/_base.md
   |  (prompt_loader.py)|  + config/system_prompts/<variant_slug>.md
   +---------+----------+  + ~/.claude/projects/-Users-crowelogic/memory rules (auto-injected)
             |
             v
   +--------------------+
   |  provider call     |  providers/{ollama,nvidia,azure_openai,anthropic,...}
   +---------+----------+
             |
             v (token stream)
   +--------------------+
   |  GUARDRAIL CHAIN   |  cli/guardrails/
   |  - SecretScrubber  |  regex for re_*, sk-*, AKIA*, github_pat_*, hf_*, etc.
   |  - StyleEnforcer   |  em-dash to " - ", emoji strip + warn
   |  - PathPolicy      |  refuse Write to ~/<file> patterns
   |  - ScopeBudget     |  reasoning-token cap with summarize-and-act interrupt
   +---------+----------+
             |
             v
   +--------------------+
   |  renderer.py       |  cleaned tokens render to terminal
   +--------------------+
```

The eval harness is orthogonal: it replays any user turn against any variant under any guardrail and prompt config, scores against the rubric, and emits a delta report.

## 4. Sub-projects and effort

| # | Sub-project | Effort | Depends on |
|---|---|---|---|
| 1 | Guardrails: SecretScrubber, StyleEnforcer, PathPolicy, ScopeBudget | 1.5 days | none |
| 2 | Prompt loader + per-variant prompt extraction | 1 day | none |
| 3 | `_base.md` policy file encoding user MEMORY rules | 0.5 day | 2 |
| 4 | Eval rubric expansion: 11 failure-mode metrics | 1.5 days | 1, 2 |
| 5 | Curated transcript dataset assembler | 1 day | 4 |
| 6 | LoRA pipeline: eval gate, per-variant adapters | 2 days | 4, 5 |
| 7 | Telemetry hooks (feeds future Cortex CSEP) | 0.5 day | 1 |
| 8 | Migration: per-model `prompt` strings to files | 0.5 day | 2, 3 |

Total: ~8.5 focused days. Realistic calendar: 2 to 3 weeks given parallel commitments.

## 5. Files to create or modify

### New files

```
cli/guardrails/__init__.py
cli/guardrails/secrets.py
cli/guardrails/style.py
cli/guardrails/paths.py
cli/guardrails/scope.py
cli/guardrails/chain.py
config/system_prompts/_base.md
config/system_prompts/eclipse.md
config/system_prompts/crescent.md
config/system_prompts/localmesh.md
config/system_prompts/deepparallel.md
config/system_prompts/frontier.md
config/system_prompts/prism.md
config/system_prompts/ultra.md
config/system_prompts/lunar.md
config/system_prompts/pulse.md
config/system_prompts/depth.md
config/system_prompts/nova.md
config/system_prompts/open.md
config/system_prompts/maverick.md
config/system_prompts/coder.md
config/system_prompts/dev.md
config/system_prompts/swift.md
config/system_prompts/mesh.md
config/system_prompts/lite.md
config/system_prompts/vision.md
(plus Azure-hosted variants per inventory in section 7)
config/prompt_loader.py
eval/__init__.py
eval/rubric.py
eval/replay.py
eval/transcripts/2026-04-30-eclipse-email-blast.json (sanitized)
data/training/curated_transcripts/<variant>/
tests/test_guardrails_secrets.py
tests/test_guardrails_style.py
tests/test_guardrails_paths.py
tests/test_guardrails_scope.py
tests/test_prompt_loader.py
tests/test_eval_replay.py
tests/test_eval_rubric.py
```

### Modified files (minimal, additive)

- `cli/renderer.py` - wire guardrail chain into `feed()` and `feed_reasoning()`. Roughly a 20-line addition to `StreamRenderer`.
- `cli/session_runtime.py` - wire ScopeBudget interrupt. Roughly 15 lines.
- `config/agent_config.py` - one new function `system_prompt_for(model_cfg)`. Existing inline `prompt` strings stay, but the loader prefers files when present and warns on inline fallback.
- `scripts/lora_phase2_submit_tuning.py` - call eval gate before submission. Refuse to submit if score regresses.
- `scripts/eval_prompt_density.py` - extend rubric (or split into `eval/rubric.py` and have this script call it).

### Files NOT touched

- `cli/branding.py`, `cli/dual_renderer.py`, `cli/queue_renderer.py` - Cortex spec is consolidating these; do not preempt.
- `providers/*.py` - guardrails wrap output, not provider implementations.
- `config/agent_config.py` MODEL_CHAIN list - additive only; no entry rewrites.

## 6. Failure-mode metrics (eval rubric)

Each metric returns a 0 to 1 score where 0 is perfect and 1 is catastrophic. Aggregate is the mean. Per-metric breakdown is reported.

| ID | Metric | Detection | Type |
|---|---|---|---|
| QS-01 | Secret leakage | regex sweep over output for known credential shapes | deterministic |
| QS-02 | Em-dash count per 1k output tokens | character count | deterministic |
| QS-03 | Emoji presence | unicode block scan | deterministic |
| QS-04 | Files written outside policy | path regex applied to Write-tool calls | deterministic |
| QS-05 | Reasoning-to-output token ratio | telemetry; flag if >5x for tasks under 500 output tokens | deterministic |
| QS-06 | Verb coverage in final answer | did the answer address each imperative verb in the user request? | LLM judge |
| QS-07 | Verification before completion | did the model claim done without running the verification it described? | LLM judge |
| QS-08 | Self-correction follow-through | did the model self-detect drift and continue drifting anyway? | LLM judge |
| QS-09 | Premature feature gold-plating | output codebase delta vs. minimal-solution baseline | LLM judge |
| QS-10 | Capability self-disclosure timing | did the model wait until 2nd ask to admit a tool gap? | deterministic if we annotate transcripts |
| QS-11 | TTFT and tokens-per-second health | provider telemetry; alert thresholds per variant | deterministic |

Judge model: **CroweLM Lite** (cheapest reasoning variant, NVIDIA NIM `gpt-oss-20b`). One judge model for consistency across runs.

## 7. Per-variant tuning matrix

| Variant | Backend | Tunable? | Initial action |
|---|---|---|---|
| DeepParallel | Ollama (local) | Yes (LoRA via base) | New per-variant prompt + LoRA on graded transcripts |
| CroweLM Crescent | Ollama (kimi-k2.5:cloud) | Investigate Moonshot terms | Prompt-only first |
| CroweLM Eclipse | Ollama (kimi-k2.6:cloud) | Same as Crescent | Prompt-only first; this is the variant the user explicitly cited |
| CroweLM LocalMesh | Ollama (glm-4.6:cloud) | THUDM permits | Prompt-only first |
| CroweLM Frontier | NVIDIA NIM (mistral-large-3) | No | Prompt + guardrails |
| CroweLM Prism | NVIDIA NIM (qwen3.5-397b) | No | Prompt + guardrails |
| CroweLM Ultra | NVIDIA NIM (nemotron-ultra-253b) | No | Prompt + guardrails |
| CroweLM Lunar | NVIDIA NIM (kimi-k2.5) | No | Prompt + guardrails |
| CroweLM Pulse | NVIDIA NIM (kimi-k2-thinking) | No | Prompt + guardrails |
| CroweLM Depth | NVIDIA NIM (deepseek-v3.2) | No | Prompt + guardrails |
| CroweLM Nova | NVIDIA NIM (nemotron-3-super-120b) | No | Prompt + guardrails |
| CroweLM Open | NVIDIA NIM (gpt-oss-120b) | No on NIM; yes on Azure | Prompt + guardrails (NIM); Azure FT pipeline already exists |
| CroweLM Maverick | NVIDIA NIM (llama-4-maverick) | No | Prompt + guardrails |
| CroweLM Coder | NVIDIA NIM (qwen3-coder-480b) | No | Prompt + guardrails |
| CroweLM Dev | NVIDIA NIM (devstral-2-123b) | No | Prompt + guardrails |
| CroweLM Swift | NVIDIA NIM (nemotron-super-49b) | No | Prompt + guardrails |
| CroweLM Mesh | NVIDIA NIM (qwen3.5-122b) | No | Prompt + guardrails |
| CroweLM Lite | NVIDIA NIM (gpt-oss-20b) | No | Prompt + guardrails |
| CroweLM Vision | NVIDIA NIM (nemotron-nano-12b-vl) | No | Prompt + guardrails (vision-aware) |
| CroweLM Dense (FW-GLM-5) | Azure (resource 3995) | Yes | Prompt + LoRA via existing fine_tune.py |
| CroweLM Dense v2 (FW-GLM-5.1) | Azure ML | Yes | Prompt + LoRA via existing fine_tune.py |
| CroweLM Supreme (claude-opus-4.7 / 4.6 fallback) | Azure (resource 6302) | No (Anthropic) | Prompt + guardrails |
| gpt-oss-120b on Azure FT | Azure AI Foundry | Yes | Existing pipeline; add eval gate |

## 8. Coordination with Cortex spec

Cortex defines CSEP events including `error.surface`, `telemetry.tick`, `tool.invoke`. Quality Stack guardrails will emit those events natively rather than printing to stderr:

- `SecretScrubber` redaction: CSEP `error.surface` with `code: "secret-redacted"`, `recoverable: true`.
- `ScopeBudget` interrupt: CSEP `error.surface` with `code: "scope-budget-exceeded"` followed by an `answer.delta` containing the summarize-and-act interrupt prompt.
- Eval scores per session: CSEP `telemetry.tick` `quality_score` field.
- Cortex `BrandVeil` and Quality Stack `SecretScrubber` are different concerns at the same boundary (one hides vendor names, the other hides credentials). They live in `cli/guardrails/` so the boundary is single.

`config/agent_config.py` collisions: this spec adds one function and deprecates inline `prompt` strings. Cortex's "engine extraction + rename" moves the file whole. Sequence: Quality Stack lands first, Cortex rename moves it later, no merge conflict.

## 9. Open questions for spec review

1. **Scope of "tune all our models"**: confirm interpretation - prompt + guardrails for every variant, LoRA only where the backend permits. Twenty-three variants in the matrix, four LoRA-eligible (DeepParallel, GLM-5, GLM-5.1, gpt-oss-120b on Azure FT).
2. **Curated transcript provenance**: `.chat_history` exists in the repo root. Is that the canonical source of session transcripts? If not, where? And is there user consent to use them as training data for the LoRA?
3. **Judge model for metrics 06-09**: CroweLM Lite (cheapest, fast) vs. CroweLM Eclipse (largest, expensive). Recommendation: Lite. Faster iteration.
4. **Sequence**: prompt+guardrails first, then eval, then LoRA? Or eval-first to lock the rubric? Recommendation: prompt+guardrails first because they are the immediate-quality wins and they make the eval scores meaningful.
5. **Quality Stack repo**: stay in `crowe-logic-foundry` or extract to a separate `crowelm-quality` repo? Cortex spec moves this whole tree to `crowelm-engine`. Recommendation: stay put for v1, move with Cortex's rename.

## 10. Success criteria

Quality Stack v1.0 ships when:

1. Replaying the 2026-04-30 Eclipse email-blast transcript scores aggregate 0.10 or lower. Baseline today is 0.65 or higher.
2. No secret in any session is rendered to terminal across a 7-day soak.
3. Em-dash count per 1k output tokens is 0.5 or lower across all variants.
4. No file is written to `~/<file>` patterns by any variant during eval.
5. Reasoning-to-output ratio is 3.0 or lower for tasks producing under 500 output tokens.
6. At least one variant has a LoRA adapter promoted via the eval gate (i.e., the gate has fired green).
7. Eval rubric runs in CI on every PR that touches `config/system_prompts/` or `cli/guardrails/`.
8. Per-variant prompt files exist for all 23 variants in the matrix.

---

## 11. Out of scope (explicitly)

- Replacement renderer (Cortex spec covers).
- BrandVeil for vendor-name hiding (Cortex spec covers; we coordinate at the same boundary).
- Tauri desktop app (Cortex spec covers).
- Engine rename to `crowelm-engine` (Cortex spec covers).
- Autonomous agentic improvements (RLHF, self-play, etc.). Out of scope for v1; prompt + LoRA + guardrails first.
