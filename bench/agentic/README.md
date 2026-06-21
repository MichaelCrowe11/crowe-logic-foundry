# Agentic Coding Eval Harness (SP-0)

Head-to-head **pass@1** scoring of the `crowe-logic` agent loop against a clean
reference plan→act→verify loop, on the **same model**. Isolated from the
existing `bench/` scoreboard; this package never modifies the live agent.

## Why this exists (the honesty framing)

`crowe-logic` is a *harness + multi-model router*, not a trainable model. So
"rivals frontier" cannot mean beating a vendor on a knowledge benchmark — a
router's knowledge score is just whichever backend it called. The only honest,
falsifiable question for a harness is:

> On the same coding tasks, with the **same underlying model**, does
> crowe-logic's control structure (plan → act → verify → self-correct) complete
> tasks as reliably as a clean frontier-style agent loop?

This harness measures exactly that, and produces the baseline gap that orders
the gap-closing backlog (verification loop, self-correction, plan tracking,
context compaction, budget/tool-subset tuning).

## Two baselines

| Baseline | Holds constant | Question answered |
|---|---|---|
| **Harness-isolated** (primary) | Same model (Opus) both sides; same tools; same budgets | "Is our **harness** as good as a clean frontier loop?" |
| **Absolute reference** (context) | Published frontier-agent pass rates, recorded manually | "Where do we sit on an external scale?" |

The primary baseline is `agents/reference.py` on the same model the crowe-logic
side uses. Running both on the **same model** isolates the control structure
from raw model IQ — a harness win/loss can't be confounded with a model
win/loss.

## How to run

Token-free pipeline self-test (no model calls):

```bash
.venv/bin/python -m pytest tests/agentic/ -q
```

Token-free smoke over the real suite (stub runners; proves loading + scoreboard
at scale, spends nothing):

```bash
.venv/bin/python -c "from bench.agentic.runner import run_suite; \
from bench.agentic.agents.stub import StubRunner; \
print(run_suite('bench/agentic/tasks', \
  [StubRunner(name='crowe-logic'), StubRunner(name='reference')], \
  'bench/agentic/results-smoke', 'stub'))"
```

Real baseline run (spends tokens — needs Anthropic creds + the real model id):

```bash
.venv/bin/python -m bench.agentic.runner \
  --tasks bench/agentic/tasks --results bench/agentic/results --model <opus-id>
```

Output: `<results>/raw.jsonl` (append-only rows) and `<results>/scoreboard.md`
(aggregate pass@1 per agent, the harness-isolated gap, and a per-task matrix).

## How to add a task

Create `tasks/<id>/`:

- `seed/` — a small repo with a **bug or stub** and a **failing test suite**.
- `prompt.txt` — natural-language task. State the desired behavior; never the fix.
- `verify.sh` — `#!/bin/sh` then `python3 -m pytest -q`. **Exit 0 = pass.** The
  agent never sees this file.
- `meta.json` — `{lang, difficulty, tags, timeout_s, max_rounds}`.

Then confirm the seed genuinely fails and a correct fix passes:

```bash
(cd tasks/<id>/seed && /path/to/.venv/bin/python -m pytest -q)   # expect failures
```

`run_verify` puts the harness interpreter's `bin/` on PATH, so `python3` inside
`verify.sh` resolves to the pytest-equipped interpreter (the bare macOS
`python3` has none).

## Starter suite (12 tasks)

`parse_duration`, `flatten`, `dedupe`, `csv_column_sum` (easy) ·
`retry`, `merge_intervals`, `roman_to_int`, `lru_cache`, `balanced`,
`word_wrap` (medium) · `topo_sort`, `dijkstra` (hard).

`tasks/_fixtures/` holds a trivially-passing and trivially-failing task used by
the harness self-tests; `_`-prefixed dirs are excluded from the suite.

## Layout

```
bench/agentic/
  agents/{base,stub,crowe_logic,reference}.py   # runners + the AgentRunner contract
  sandbox.py        # fresh tmp copy of seed/ per run; guaranteed teardown
  tasks_io.py       # task loading + meta.json validation
  verify.py         # timed verify.sh subprocess (exit 0 = pass)
  score.py          # JSONL row schema + pass@1 aggregation
  report.py         # scoreboard.md (gap + per-task matrix)
  runner.py         # (task x agent) -> sandbox -> run -> verify -> record
  tasks/<id>/...    # committed task suite
```
