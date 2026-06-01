# CroweLM Benchmark Scoreboard

A harness that scores CroweLM chat tiers on two tracks, driven through the real
`crowe-logic` stack (`cli/headless.py`) so the numbers reflect what users
actually get — routing, providers, and all.

## The two tracks

- **Track A — public benchmarks** (MMLU / GSM8K / HumanEval slices).
  Honest framing: most tiers are virtual tiers over commercial backends, so a
  Track A number is a **backend baseline**, not a novel CroweLM result. The
  scoreboard labels tiers by backend.
- **Track B — mycology, grounded vs bare.** Each tier answers questions drawn
  from Crowe's cultivation corpus **twice**: once with the dataset MCP mounted
  (grounded) and once without (bare). The **delta = grounded − bare** is the
  platform's contribution over the base model — the number that is uniquely
  Crowe's.

## Running

```bash
# Smoke run (default): 5 flagship tiers, 5 questions/benchmark — cheap.
python -m bench.runner

# Scope:
python -m bench.runner --track a            # only Track A
python -m bench.runner --track b            # only Track B (grounded vs bare)
python -m bench.runner --limit 2            # fewer questions per benchmark
python -m bench.runner --tiers gpt-5.4 claude-opus-4-6   # explicit tiers

# Full sweep — ALL ~68 chat tiers. Expensive; opt in explicitly.
python -m bench.runner --all
```

The runner prints a cost estimate (`Tiers: N | est. runs: M ...`) before
dispatching. `--all` is the only way to hit the full chain; the default is the
flagship smoke set in `bench/config.py`.

## Output

Results land in `bench/results/<timestamp>/`:
- `raw.jsonl` — one row per (question × tier × condition) run, append-only.
- `scored.jsonl` — raw rows plus a `score` field.
- `scoreboard.md` — the Markdown scoreboard (Track A accuracy table + Track B
  grounded-vs-bare delta table).

## Scoring

- **Track A:** deterministic. `multiple_choice` and `numeric` match exactly;
  `code` (HumanEval) runs the row's `tests` in a sandboxed subprocess (pass@1).
- **Track B:** an LLM judge (pinned `JUDGE_TIER` in `config.py`) scores each
  answer 0–5 for factual alignment with the source passage. The judge tier and
  prompt are fixed for reproducibility.

## The mycology eval set

`bench/datasets/track_b/mycology.jsonl` holds grounded Q&A rows (question +
`source_passage` ground truth + `reference_answer`). The committed seed set is
hand-authored from the cultivation corpus and is inspectable.

To regenerate/expand from corpus passages:
1. Put passages in `bench/datasets/track_b/_passages.jsonl`
   (`{id, text, doc}` per line — pulled via the `crowe-portfolio` MCP).
2. `python -m bench.generate_mycology_set` — drafts a question + reference
   answer per passage via `JUDGE_TIER` and writes `mycology.jsonl`.
   (Output is committed so runs stay reproducible.)

## Configuration

All knobs live in `bench/config.py`:
- `FLAGSHIP_TIERS` — the smoke-run default tier set.
- `JUDGE_TIER` — pinned Track B judge.
- `DATASETS_DIR`, `RESULTS_DIR`, `MAX_STORED_ANSWER_CHARS`.

## Honesty guards

- Track A is framed as a backend baseline, with tiers labelled by backend
  family — never claiming a vendor's score as a CroweLM achievement.
- Track B reports the grounded-vs-bare delta, the platform's marginal
  contribution — not a head-to-head win claim.
- The mycology questions and their source passages are committed for
  inspection; the judge tier and prompt are pinned.
