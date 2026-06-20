# Design — Agentic Coding Eval Harness (head-to-head vs frontier)

**Date:** 2026-06-20
**Status:** Approved design; ready for implementation plan
**Scope:** Sub-project SP-0 of the "make crowe-logic rival frontier agents" program

---

## 1. Problem & framing

The goal is to make `crowe-logic`'s **agentic harness quality** rival frontier
coding agents. The foundry is a *harness + multi-model router*, not a model that
can be trained, so "rivals frontier" cannot mean beating a vendor on a knowledge
benchmark — a router's knowledge score is just whichever backend it called (the
existing `bench/README.md` already concedes this for Track A).

The only honest, falsifiable definition for a harness is: **on the same coding
tasks, with the same underlying model, does crowe-logic's control structure
(plan → act → verify → self-correct) complete tasks as reliably as a clean
frontier-style agent loop?**

That requires a yardstick before any improvement work. This spec defines that
yardstick: a head-to-head agentic coding eval harness. The harness is built
*once*; the gap-closing levers are separate sub-projects, each gated on a
measured delta from this harness.

### Current-state gaps (from code recon)

The live loop is `providers/_shared.py:513` (`BaseOpenAIProvider.stream_response`,
mirrored in `providers/azure_openai.py:420` and `providers/anthropic.py:303`).
Relative to a frontier coding agent it is missing:

- **No verification loop.** It stops as soon as the model emits no tool calls.
  "Verify after writes" is prose in the system prompt
  (`config/agent_config.py:1358`), not enforced logic.
- **No self-correction.** On tool error or failing tests it appends the error and
  reacts; there is no structured retry/debug strategy.
- **Planning is bolted on.** "Specification Mode" (`cli/autonomy.py:165`) is a
  separate one-shot command, not woven into execution.
- **Unbounded context.** History grows without compaction; long (40+ round)
  tasks degrade.
- **`MAX_ROUNDS = 20`** (`providers/_shared.py:351`) is low for real coding work.
- **All ~113 tools always in schema** — token bloat and worse tool selection.

These become the backlog (§7), ordered by measured impact, not by this list.

## 2. Program shape (the iterative loop)

```
build eval harness (ONCE)
      │
      ▼
run suite → baseline gap  ◄────────────┐
      │                                 │
      ▼                                 │
pick biggest MEASURED gap               │
      │                                 │
      ▼                                 │
implement ONE lever ───────────────────┘
(re-measure: did pass@1 move toward baseline?)

exit when crowe-logic pass@1 ≈ harness-isolated baseline
```

Only SP-0 (this harness) is fully designed now. Each lever in §7 gets its own
spec → plan → build cycle, prioritized from the baseline data this harness
produces.

## 3. Architecture

A new **isolated** subpackage `bench/agentic/`, alongside the existing Track A/B
scoreboard. It reuses `bench/`'s reporting conventions but does **not** modify
the existing `runner.py` / `scoring.py` / `report.py` (no regression risk to the
current scoreboard).

```
bench/agentic/
  __init__.py
  tasks/<task_id>/
      seed/               # initial repo state (committed files)
      prompt.txt          # what the agent is told (agent sees this)
      verify.sh           # exit 0 = pass; agent NEVER sees this
      meta.json           # {lang, difficulty, tags, timeout_s, max_rounds}
  agents/
      base.py             # AgentRunner interface
      crowe_logic.py      # adapter: drives cli/headless.py in a workdir
      reference.py        # clean plan→act→verify loop (Opus via foundry anthropic provider)
  sandbox.py              # fresh tmp copy of seed/ per run; isolation + cleanup
  runner.py               # for each (task × agent): sandbox → run → verify → record
  score.py                # pass@1 + secondary metrics
  report.py               # scoreboard.md: crowe-logic vs baseline + the gap
  README.md               # how to run, how to add a task, honesty framing
```

### Component contracts

**`AgentRunner` (agents/base.py)** — the pluggable interface both harnesses
implement. Holds the variable we are measuring (the control structure) behind one
boundary so crowe-logic and the reference loop are scored identically.

```python
@dataclass
class AgentResult:
    workdir: Path            # the mutated copy the agent worked in
    transcript: list[dict]   # full message/tool trace for replay
    rounds: int
    tool_calls: int
    wall_s: float
    tokens: int | None
    cost_usd: float | None
    self_verified: bool      # did the agent run tests itself before finishing?
    error: str | None        # crash/timeout reason, else None

class AgentRunner(Protocol):
    name: str
    def run(self, *, prompt: str, workdir: Path, model: str,
            tools: list[str], max_rounds: int, timeout_s: int) -> AgentResult: ...
```

**`crowe_logic.py`** drives `cli/headless.py` **as a subprocess** (decoupled,
crash-isolated from the runner), `cwd = workdir`, autonomy = `execute`, the agreed
tool subset, the task's budgets. It parses the headless streaming-JSON event
stream for rounds / tool calls / tokens and sets `self_verified` if a test-running
tool was invoked. Subprocess (not in-process) so an agent crash or hang is a
recorded task failure, never a runner failure.

**`reference.py`** is a minimal, dependency-light **plan → act → verify** loop
calling Opus through the foundry's own `providers/anthropic.py`, given the *same*
tool subset and budgets. It is deliberately clean and small — it doubles as the
reference implementation the §7 levers will mirror.

**`sandbox.py`** copies `tasks/<id>/seed/` into a fresh tmp dir per run, returns
the path, and guarantees teardown. One task can never corrupt another or the real
repo.

**`runner.py`** orchestrates: load tasks → for each (task × agent) make a sandbox
→ `AgentRunner.run(...)` → run `verify.sh` in a timed subprocess → record an
append-only row. Prints a cost estimate before dispatch (mirrors existing
`bench.runner`).

## 4. Task format

Offline, deterministic, **Python/pytest first** (matches the local toolchain and
scores deterministically). Each task is self-contained:

- `seed/` — a small repo containing a **bug or missing feature** and a **failing
  test suite**.
- `prompt.txt` — natural-language task, e.g. *"`parse_duration` rejects valid
  ISO-8601 inputs. Make the tests pass."*
- `verify.sh` — runs `pytest -q`; **exit 0 = pass**. Hidden from the agent.
- `meta.json` — `{lang, difficulty, tags, timeout_s, max_rounds}`.

**Starting suite:** ~12 hand-authored tasks, committed for reproducibility,
spanning easy (one-line fix) → medium (multi-file) → hard (read several files +
add a module). Authored to be unambiguous (the failing test defines "done").

## 5. Scoring & baseline

**Primary metric:** `pass@1` — `verify.sh` exits 0.

**Secondary metrics** (every run, for diagnosing *why* a gap exists): rounds used,
tool calls, wall-time, tokens/cost, and **`self_verified`** (did the agent run the
tests itself before declaring done?). `self_verified` is the leading indicator for
the verification-loop lever.

**Baselines — two, answering two questions:**

| Baseline | Holds constant | Question answered |
|---|---|---|
| **Harness-isolated** (primary) | Same model (Opus) both sides; same tools; same budgets | "Is our **harness** as good as a clean frontier loop?" — the actual target |
| **Absolute reference** (context) | Published frontier-agent / Claude Code pass rates on the same tasks, recorded manually | "Where do we sit on an external scale?" |

The primary baseline is `agents/reference.py` on Opus via the foundry's anthropic
provider. Running both harnesses on the **same model** isolates the control
structure from raw model IQ — a harness win/loss can't be confounded with a model
win/loss. The absolute reference is informational only (no automation owed in
SP-0; recorded in the scoreboard as an external column when available).

**Fairness controls enforced by the runner:** identical seed/workdir, identical
tool subset, identical round/time budgets, isolated sandbox per run, deterministic
verification. Any deviation is a harness bug, not a result.

**Exit condition for the overall program:** crowe-logic `pass@1` within a small
margin (target: ≤ ~5 percentage points, to be finalized once variance is known)
of the harness-isolated baseline across the suite.

## 6. Error handling

- Agent crash or timeout → task **fail** with `error` reason recorded; never
  silently dropped.
- Provider/API error inside an agent → one retry, then fail-with-reason.
- `verify.sh` timeout → fail (`timeout_s` from `meta.json`).
- Sandbox guarantees isolation; a failed run cannot affect other tasks or the repo.
- Results are append-only JSONL so a partial/interrupted sweep is never lost.

## 7. Gap-closing backlog (ordered AFTER baseline)

Hypothesized order, to be confirmed/reordered by the numbers:

1. **Verification loop** — run the project's tests before declaring done; failing
   output feeds back and the loop continues. (Hypothesis: biggest single lever.)
2. **Self-correction on failure** — structured retry/debug on tool error or red
   tests, not naive feedback.
3. **Plan/todo tracking** — weave Specification Mode into the live loop as tracked
   steps.
4. **Context compaction** — summarize/prune history for long tasks.
5. **Budget + tool-subset tuning** — raise `MAX_ROUNDS` for coding; expose a
   curated coding tool subset instead of all ~113.

Each is a separate spec → plan → build, gated on a measured delta from this
harness.

## 8. Testing the harness itself

- **Fixture tasks:** one trivially-passing and one trivially-failing task prove
  the scorer reports green/red correctly end-to-end.
- **Unit tests:** sandbox isolation (mutating a workdir never touches `seed/` or
  the repo), task loading/validation (malformed `meta.json` is rejected), score
  aggregation, report generation.
- **A stub `AgentRunner`** (deterministic, no model call) used in tests so the
  harness is testable without spending tokens.
- Runner under `pytest`; conforms to the repo's existing test conventions
  (`tmp_path`, `monkeypatch`).

## 9. Out of scope (YAGNI)

- Non-Python languages (add after the Python pipeline is proven).
- Automating the absolute/external baseline (recorded manually in SP-0).
- Any change to the live `crowe-logic` loop — that is SP-1+, gated on this
  harness's first numbers.
- Refactoring the `cli/crowe_logic.py` monolith beyond what the adapter needs.

## 10. Success criteria for SP-0

1. `python -m bench.agentic.runner` runs all tasks through both crowe-logic and
   the reference baseline, isolated and reproducible.
2. Produces `scoreboard.md` with per-task and aggregate pass@1 for both, plus the
   gap and secondary metrics.
3. Harness self-tests pass (fixtures + unit tests, no tokens needed).
4. The first real scoreboard gives an honest baseline gap that orders the §7
   backlog.
