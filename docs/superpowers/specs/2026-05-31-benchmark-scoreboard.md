# CroweLM Benchmark Scoreboard — Design Spec

**Status:** approved (design) 2026-05-31. Author: brainstorming session.

## Goal

Produce a publishable **CroweLM scoreboard** measuring every chat tier on (A) standard public benchmarks and (B) a mycology-domain benchmark grounded in Crowe's proprietary corpus. Track B's grounded-vs-bare delta is the marketable headline; Track A is an honestly-framed baseline.

## Why this is defensible (not just re-printing vendor scores)

Most CroweLM tiers are virtual tiers over commercial backends (Helio=gpt-5.4, Prime=claude-opus-4-6 via WatsonX, Hyphae=Kimi, etc.). A bare MMLU run on those re-measures the vendor's published number. The differentiator is **the crowelm dataset MCP**: when a tier answers WITH the MCP mounted, it is reading Crowe's proprietary corpus (478K+ words of cultivation text across `mushroom-cultivators-masterclass`, `michael-crowe-mushroom-cultivation-handbook`, `themushroomgrower-volumes`). The number that is *only* Crowe's is **grounded − bare** on domain questions drawn from that corpus.

## Architecture

A thin orchestrator over the existing `cli/headless.py` JSON event stream. The harness does NOT reimplement inference or routing — it drives the real stack so the benchmark measures what users actually get.

**Driver contract (existing):** `cli/headless.py` reads `{messages:[{role,content}], model:<tier>, session}` from stdin or `--input`, emits newline-delimited JSON events: `ready`, `token{delta}`, `reasoning{delta}`, `segment_end`, `done{tokens, reasoning_tokens, elapsed_ms, ttft_ms}`, `error{message,kind}`. The accumulated `token` deltas are the answer; `done` carries metrics.

**Prerequisite change:** `cli/headless.py` currently has no tools/MCP toggle. Add `--tools/--no-tools` (default: tools on) so Track B can run each question grounded (MCP on) and bare (MCP off). This is the only change to existing files.

### New package: `bench/`

- `bench/datasets/`
  - `track_a/` — curated JSONL slices: `mmlu.jsonl`, `gsm8k.jsonl`, `humaneval.jsonl`. Each row: `{id, question, answer, type}` where `type ∈ {multiple_choice, numeric, code}`. Slices are small and N is configurable (sanity baseline, not exhaustive).
  - `track_b/mycology.jsonl` — generated from the corpus (see Eval-set generation). Each row: `{id, question, source_passage, source_doc, reference_answer}`.
- `bench/generate_mycology_set.py` — builds `track_b/mycology.jsonl`. Pulls passages via the `crowe-portfolio` MCP (`search_code`/dataset access) across the cultivation books, then uses a strong tier to draft Q&A pairs whose `reference_answer` is grounded in (and cites) `source_passage`. Human-spot-checkable; committed as data so runs are reproducible. Target 50–100 questions.
- `bench/runner.py` — core loop. For each (question × tier × condition):
  - build the headless payload, invoke `crowe-logic-command` as a subprocess, capture stdout JSON stream
  - assemble the answer from `token` deltas; record `done` metrics (tokens, reasoning_tokens, elapsed_ms, ttft_ms); capture `error` events as failures
  - Track A: one run per (question × tier).
  - Track B: TWO runs per (question × tier) — `--tools` (grounded) and `--no-tools` (bare).
  - Writes raw results to `bench/results/<timestamp>/raw.jsonl` (one row per run; never overwrites).
- `bench/scoring.py`
  - Track A: `multiple_choice`/`numeric` → exact/normalized match → accuracy %. `code` (HumanEval) → execute the provided unit tests in a subprocess sandbox → pass@1.
  - Track B: **LLM judge** — a fixed strong tier scores each answer 0–5 for factual alignment against `source_passage` (rubric in the prompt; judge tier pinned in config for reproducibility). PLUS reuse `eval/rubric.py` quality metrics (secret-leakage, em-dash density, reasoning-ratio, etc.) on every answer.
- `bench/report.py` — reads a results dir, emits `bench/results/<timestamp>/scoreboard.md`:
  - Track A table: tier × benchmark → accuracy, with each tier labelled by backend family.
  - Track B headline table: tier × {grounded score, bare score, **delta**} on the mycology set, sorted by delta.
  - Footer: total tokens + rough cost estimate, run config (tiers, N, judge tier, timestamp).

### Safety rails (cost/runtime — 68 tiers × 2 conditions is large)

- `runner.py` defaults to a **smoke run**: a small fixed subset of tiers (the ~5 flagships) × small N. The full 68-tier sweep requires explicit `--all` (or `--tiers <list>`).
- `--limit N` caps questions per benchmark. `--track a|b|both`.
- Every run prints an upfront estimate (runs = questions × tiers × conditions) and total tokens after, so spend is never a surprise.
- Results are timestamped and append-only; reruns never clobber prior scoreboards.

## Honesty guards (this is published; accuracy of claims matters)

- Track A report labels every tier with its backend family and frames numbers as "this configuration's score," never as a novel CroweLM result.
- Track B is framed strictly as **grounded-vs-bare delta** — the platform's contribution over the base model — not as the tier beating others outright (unless bare-vs-bare also shows it).
- Backend attributions come from `MODEL_CHAIN` ground truth (e.g. Prime/Sovereign = IBM WatsonX), consistent with the prompt-wiring fix.
- The mycology eval set is committed so anyone can inspect the questions and sources; judge tier and prompt are pinned.

## Testing

- `bench/runner.py`: unit-test the headless-event parser against a recorded fixture event stream (no live API) — assert answer assembly + metric extraction + error handling.
- `bench/scoring.py`: unit-test Track A matchers (multiple_choice/numeric/code) with known inputs; unit-test the judge-response parser against a fixture judge output (no live judge call).
- `bench/report.py`: golden-file test — fixture `raw.jsonl` → expected `scoreboard.md` shape.
- The `--no-tools` headless flag: unit-test that it disables tool registration (assert the tool list is empty in that mode).
- No live API calls in the test suite; a `--smoke` integration path is documented for manual verification.

## Out of scope (recorded)

- Track B "fine-tuned vs frontier head-to-head" (Gemma 4 Mycelium bare vs frontier bare on mycology) is a natural follow-on once the harness exists, but the first deliverable is the grounded-vs-bare delta across the chain.
- Continuous/CI benchmarking, public leaderboard website — later.

## File-touch summary

- New: `bench/` package (runner, scoring, report, generate_mycology_set, datasets/, results/), `bench/tests/`.
- Modified: `cli/headless.py` (add `--tools/--no-tools`).
- Reused unchanged: `eval/rubric.py`, the `crowe-portfolio` MCP, `MODEL_CHAIN`.
