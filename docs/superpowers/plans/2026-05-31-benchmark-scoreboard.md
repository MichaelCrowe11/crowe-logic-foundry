# CroweLM Benchmark Scoreboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `bench/` harness that scores every CroweLM chat tier on standard public benchmarks (Track A) and a mycology grounded-vs-bare benchmark (Track B), driven through the real `cli/headless.py` stack.

**Architecture:** Thin orchestrator over `cli/headless.py`'s JSON event stream. Each question → a `{messages, model}` payload piped to `crowe-logic-command` as a subprocess; answers assembled from `token` events, metrics from the `done` event. Track B runs each question twice per tier (MCP-grounded vs bare). Scoring reuses `eval/rubric.py`; Track B adds an LLM judge. Defaults to a cheap smoke run; full 68-tier sweep needs `--all`.

**Tech Stack:** Python 3.13, pytest, `cli/headless.py` (subprocess JSON driver), `eval/rubric.py`, the `crowe-portfolio` MCP, `MODEL_CHAIN`.

**Spec:** `docs/superpowers/specs/2026-05-31-benchmark-scoreboard.md`

**Defaults taken (override anytime):** mycology eval set is LLM-generated with source-cited reference answers, committed + spot-checkable; judge tier = strongest available, pinned in config.

---

## File Structure

- `bench/__init__.py` — package marker.
- `bench/config.py` — constants: `FLAGSHIP_TIERS` (smoke default), `JUDGE_TIER`, paths, truncation limits. One place to tune the run.
- `bench/headless_client.py` — invoke `crowe-logic-command` as subprocess; parse the event stream into a `RunResult` (answer text, tokens, reasoning_tokens, elapsed_ms, ttft_ms, error). The only code that touches the subprocess.
- `bench/runner.py` — the (question × tier × condition) loop; writes append-only `bench/results/<ts>/raw.jsonl`. CLI entry.
- `bench/scoring.py` — Track A matchers (multiple_choice/numeric/code) + Track B judge-response parser + `eval/rubric.py` reuse.
- `bench/generate_mycology_set.py` — build `bench/datasets/track_b/mycology.jsonl` from the corpus via the MCP.
- `bench/report.py` — `raw.jsonl` → `scoreboard.md`.
- `bench/datasets/track_a/{mmlu,gsm8k,humaneval}.jsonl` — small committed slices.
- `bench/datasets/track_b/mycology.jsonl` — generated, committed.
- `bench/tests/` — fixture-based unit tests (no live API).
- Modify: `cli/headless.py` — add `--tools/--no-tools`.

---

### Task 1: `--no-tools` flag on headless

**Files:**
- Modify: `cli/headless.py` (argparse near line 519; tool wiring near line 596–602)
- Test: `tests/test_headless_notools.py`

- [ ] **Step 1: Verify the tool-disable mechanism**

Run: `grep -n "_get_orchestrator\|stream_response\|tools" cli/headless.py | head`
Read `_get_orchestrator` (~line 285) and the `provider.stream_response(...)` call (~line 596). Determine whether passing `_get_orchestrator=None` (or a no-op that registers no tools) suppresses tool/MCP use. If `stream_response` requires a callable, the no-tools path passes a `_get_orchestrator` returning the existing `_NoopOrchestrator` AND must prevent tool registration — confirm by reading `BaseOpenAIProvider.stream_response` signature (grep `def stream_response` under `providers/`). Document the exact lever in a one-line comment.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_headless_notools.py
import subprocess, sys, json, os

def _run(prompt, extra_args):
    proc = subprocess.run(
        [sys.executable, "-m", "cli.headless", "--model", "auto", *extra_args],
        input=json.dumps({"messages": [{"role": "user", "content": prompt}]}),
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "CROWE_LOGIC_OFFLINE": "1"},  # avoid live API in CI
    )
    return proc

def test_no_tools_flag_is_accepted():
    # --no-tools must be a recognized flag (exit code != 2 argparse-usage error)
    proc = _run("hi", ["--no-tools"])
    assert "unrecognized arguments" not in proc.stderr
```

- [ ] **Step 3: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_headless_notools.py -q -p no:cacheprovider`
Expected: FAIL — argparse reports `unrecognized arguments: --no-tools`.

- [ ] **Step 4: Add the flag**

In `cli/headless.py` `main()` after the `--model` argument (line ~521):

```python
    parser.add_argument(
        "--tools", dest="tools", action="store_true", default=True,
        help="Enable agent tools / dataset MCP (default).",
    )
    parser.add_argument(
        "--no-tools", dest="tools", action="store_false",
        help="Disable all tools/MCP — bare model answer (for grounded-vs-bare benchmarks).",
    )
```

Then thread `args.tools` into the `stream_response` call (~line 596). Per Step 1's finding, when `args.tools` is False pass the tool-suppressing orchestrator path (e.g. `_get_orchestrator=None` or a no-tools variant) so no tools/MCP are registered.

- [ ] **Step 5: Run to confirm pass**

Run: `.venv/bin/python -m pytest tests/test_headless_notools.py -q -p no:cacheprovider`
Expected: PASS. Also `.venv/bin/ruff check cli/headless.py tests/test_headless_notools.py` → All checks passed.

- [ ] **Step 6: Commit**

```bash
git add cli/headless.py tests/test_headless_notools.py
git commit -m "feat(headless): add --tools/--no-tools toggle for grounded-vs-bare benchmarks"
```

---

### Task 2: `bench` package skeleton + config

**Files:**
- Create: `bench/__init__.py`, `bench/config.py`
- Test: `tests/test_bench_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bench_config.py
from bench import config

def test_flagship_tiers_are_a_nonempty_subset():
    assert isinstance(config.FLAGSHIP_TIERS, list)
    assert config.FLAGSHIP_TIERS  # non-empty smoke default
    assert config.JUDGE_TIER       # a pinned judge tier name
    assert config.RESULTS_DIR.name == "results"
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_bench_config.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench'`.

- [ ] **Step 3: Create the package**

`bench/__init__.py`: empty.

`bench/config.py`:
```python
"""Benchmark harness configuration — the single place to tune a run."""
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
DATASETS_DIR = BENCH_DIR / "datasets"
RESULTS_DIR = BENCH_DIR / "results"

# Smoke-run default: the marketable flagship tiers (model `name` from MODEL_CHAIN).
FLAGSHIP_TIERS = ["gpt-5.4", "gpt-5.4-pro", "claude-opus-4-6", "Kimi-K2-6", "DeepSeek-R1"]

# Pinned judge for Track B scoring (strongest available; reproducible).
JUDGE_TIER = "gpt-5.4-pro"

# Truncation limits for stored answers (chars).
MAX_STORED_ANSWER_CHARS = 8000
```

- [ ] **Step 4: Run to confirm pass**

Run: `.venv/bin/python -m pytest tests/test_bench_config.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bench/__init__.py bench/config.py tests/test_bench_config.py
git commit -m "feat(bench): package skeleton + run config"
```

---

### Task 3: headless client (event-stream parser)

**Files:**
- Create: `bench/headless_client.py`
- Test: `tests/test_bench_headless_client.py`

- [ ] **Step 1: Write the failing test (against a fixture event stream — no live API)**

```python
# tests/test_bench_headless_client.py
from bench.headless_client import parse_event_stream, RunResult

FIXTURE = "\n".join([
    '{"type":"ready"}',
    '{"type":"token","delta":"Hello "}',
    '{"type":"reasoning","delta":"thinking"}',
    '{"type":"token","delta":"world"}',
    '{"type":"done","tokens":2,"reasoning_tokens":1,"elapsed_ms":1500,"ttft_ms":400}',
])

def test_parse_assembles_answer_and_metrics():
    r = parse_event_stream(FIXTURE)
    assert isinstance(r, RunResult)
    assert r.answer == "Hello world"
    assert r.tokens == 2
    assert r.reasoning_tokens == 1
    assert r.elapsed_ms == 1500
    assert r.ttft_ms == 400
    assert r.error is None

def test_parse_captures_error_event():
    stream = '{"type":"ready"}\n{"type":"error","message":"boom","kind":"provider"}'
    r = parse_event_stream(stream)
    assert r.error == "boom"
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_bench_headless_client.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.headless_client'`.

- [ ] **Step 3: Implement the parser + subprocess runner**

```python
# bench/headless_client.py
"""Drive cli/headless.py as a subprocess and parse its JSON event stream."""
from __future__ import annotations
import json, subprocess, sys
from dataclasses import dataclass, field


@dataclass
class RunResult:
    answer: str = ""
    reasoning: str = ""
    tokens: int = 0
    reasoning_tokens: int = 0
    elapsed_ms: int = 0
    ttft_ms: int = 0
    error: str | None = None
    raw_events: list[dict] = field(default_factory=list)


def parse_event_stream(text: str) -> RunResult:
    r = RunResult()
    answer, reasoning = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        r.raw_events.append(ev)
        t = ev.get("type")
        if t == "token":
            answer.append(ev.get("delta", ""))
        elif t == "reasoning":
            reasoning.append(ev.get("delta", ""))
        elif t == "error":
            r.error = ev.get("message", "unknown error")
        elif t == "done":
            r.tokens = ev.get("tokens", 0)
            r.reasoning_tokens = ev.get("reasoning_tokens", 0)
            r.elapsed_ms = ev.get("elapsed_ms", 0)
            r.ttft_ms = ev.get("ttft_ms", 0)
    r.answer = "".join(answer)
    r.reasoning = "".join(reasoning)
    return r


def run_headless(prompt: str, model: str, *, tools: bool = True, timeout: int = 300) -> RunResult:
    """Invoke crowe-logic-command for one question; return parsed RunResult."""
    args = [sys.executable, "-m", "cli.headless", "--model", model,
            "--tools" if tools else "--no-tools"]
    payload = json.dumps({"messages": [{"role": "user", "content": prompt}], "model": model})
    try:
        proc = subprocess.run(args, input=payload, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return RunResult(error=f"timeout after {timeout}s")
    result = parse_event_stream(proc.stdout)
    if result.error is None and proc.returncode != 0:
        result.error = (proc.stderr or "nonzero exit").strip()[:500]
    return result
```

- [ ] **Step 4: Run to confirm pass**

Run: `.venv/bin/python -m pytest tests/test_bench_headless_client.py -q -p no:cacheprovider`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add bench/headless_client.py tests/test_bench_headless_client.py
git commit -m "feat(bench): headless subprocess client + event-stream parser"
```

---

### Task 4: Track A scorers

**Files:**
- Create: `bench/scoring.py`
- Test: `tests/test_bench_scoring_a.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bench_scoring_a.py
from bench.scoring import score_multiple_choice, score_numeric

def test_multiple_choice_normalizes():
    assert score_multiple_choice("The answer is (B).", "B") == 1.0
    assert score_multiple_choice("A", "B") == 0.0

def test_numeric_matches_with_formatting():
    assert score_numeric("The result is 42.", "42") == 1.0
    assert score_numeric("about 3,000 units", "3000") == 1.0
    assert score_numeric("7", "8") == 0.0
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_bench_scoring_a.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.scoring'`.

- [ ] **Step 3: Implement Track A scorers**

```python
# bench/scoring.py
"""Scorers: Track A (exact/numeric/code) and Track B (LLM-judge parsing)."""
from __future__ import annotations
import re


def score_multiple_choice(answer: str, expected: str) -> float:
    """1.0 if the expected letter is the model's selected option."""
    letters = re.findall(r"\b([A-E])\b", answer.upper())
    if not letters:
        return 0.0
    # Prefer an explicit "answer is X" pattern, else first standalone letter.
    m = re.search(r"ANSWER\s*(?:IS|:)?\s*\(?([A-E])\)?", answer.upper())
    chosen = m.group(1) if m else letters[-1]
    return 1.0 if chosen == expected.strip().upper() else 0.0


def score_numeric(answer: str, expected: str) -> float:
    """1.0 if the expected number appears in the answer (comma/space tolerant)."""
    want = expected.replace(",", "").replace(" ", "").strip()
    nums = re.findall(r"-?\d[\d,]*\.?\d*", answer)
    norm = {n.replace(",", "") for n in nums}
    return 1.0 if want in norm else 0.0
```

- [ ] **Step 4: Run to confirm pass**

Run: `.venv/bin/python -m pytest tests/test_bench_scoring_a.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bench/scoring.py tests/test_bench_scoring_a.py
git commit -m "feat(bench): Track A multiple-choice + numeric scorers"
```

---

### Task 5: Track B judge-response parser

**Files:**
- Modify: `bench/scoring.py`
- Test: `tests/test_bench_scoring_b.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bench_scoring_b.py
from bench.scoring import parse_judge_score, build_judge_prompt

def test_parse_judge_extracts_0_to_5():
    assert parse_judge_score("Reasoning... SCORE: 4") == 4
    assert parse_judge_score("score is 0 — wrong") == 0
    assert parse_judge_score("no number here") is None

def test_build_judge_prompt_includes_source_and_answer():
    p = build_judge_prompt(question="Q?", source_passage="SRC", answer="ANS")
    assert "SRC" in p and "ANS" in p and "Q?" in p
    assert "0" in p and "5" in p  # rubric range stated
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_bench_scoring_b.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'parse_judge_score'`.

- [ ] **Step 3: Implement judge prompt + parser (append to `bench/scoring.py`)**

```python
def build_judge_prompt(*, question: str, source_passage: str, answer: str) -> str:
    return (
        "You are grading an answer for factual alignment with a source passage.\n"
        "Score 0-5 (0 = contradicts/irrelevant, 5 = fully correct and grounded).\n"
        "Judge ONLY against the source passage; do not use outside knowledge.\n\n"
        f"QUESTION:\n{question}\n\n"
        f"SOURCE PASSAGE (ground truth):\n{source_passage}\n\n"
        f"ANSWER TO GRADE:\n{answer}\n\n"
        "Respond with one line: SCORE: <0-5>"
    )


def parse_judge_score(judge_text: str) -> int | None:
    m = re.search(r"SCORE\s*[:=]?\s*([0-5])", judge_text.upper())
    if m:
        return int(m.group(1))
    m2 = re.search(r"\b([0-5])\b", judge_text)
    return int(m2.group(1)) if m2 else None
```

- [ ] **Step 4: Run to confirm pass**

Run: `.venv/bin/python -m pytest tests/test_bench_scoring_b.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bench/scoring.py tests/test_bench_scoring_b.py
git commit -m "feat(bench): Track B judge prompt builder + score parser"
```

---

### Task 6: Runner (smoke-default, append-only results)

**Files:**
- Create: `bench/runner.py`
- Test: `tests/test_bench_runner.py`

- [ ] **Step 1: Write the failing test (monkeypatch the headless client — no live API)**

```python
# tests/test_bench_runner.py
import json
from bench import runner
from bench.headless_client import RunResult

def test_runner_writes_one_row_per_run(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "run_headless",
        lambda prompt, model, tools=True, timeout=300: RunResult(answer="42", tokens=1, elapsed_ms=10))
    questions = [{"id": "q1", "question": "2+2?", "answer": "4", "type": "numeric"}]
    out = runner.run_track_a(questions, tiers=["gpt-5.4"], results_dir=tmp_path)
    rows = [json.loads(l) for l in (out / "raw.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["tier"] == "gpt-5.4"
    assert rows[0]["question_id"] == "q1"
    assert rows[0]["condition"] == "default"

def test_track_b_runs_two_conditions_per_question(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "run_headless",
        lambda prompt, model, tools=True, timeout=300: RunResult(answer="x", tokens=1))
    qs = [{"id": "m1", "question": "spawn?", "source_passage": "S", "reference_answer": "R"}]
    out = runner.run_track_b(qs, tiers=["gpt-5.4"], results_dir=tmp_path)
    rows = [json.loads(l) for l in (out / "raw.jsonl").read_text().splitlines()]
    conds = sorted(r["condition"] for r in rows)
    assert conds == ["bare", "grounded"]
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_bench_runner.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.runner'`.

- [ ] **Step 3: Implement the runner**

```python
# bench/runner.py
"""Benchmark runner: (question x tier x condition) -> append-only raw.jsonl."""
from __future__ import annotations
import json
from pathlib import Path
from bench.headless_client import run_headless


def _write_row(fh, **row):
    fh.write(json.dumps(row) + "\n")
    fh.flush()


def run_track_a(questions, tiers, results_dir: Path):
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / "raw.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for q in questions:
            for tier in tiers:
                r = run_headless(q["question"], tier, tools=True)
                _write_row(fh, track="a", condition="default", tier=tier,
                           question_id=q["id"], qtype=q.get("type", ""),
                           expected=q.get("answer", ""), answer=r.answer,
                           tokens=r.tokens, elapsed_ms=r.elapsed_ms,
                           reasoning_tokens=r.reasoning_tokens, error=r.error)
    return results_dir


def run_track_b(questions, tiers, results_dir: Path):
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / "raw.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for q in questions:
            for tier in tiers:
                for condition, tools in (("grounded", True), ("bare", False)):
                    r = run_headless(q["question"], tier, tools=tools)
                    _write_row(fh, track="b", condition=condition, tier=tier,
                               question_id=q["id"], source_passage=q.get("source_passage", ""),
                               reference_answer=q.get("reference_answer", ""),
                               answer=r.answer, tokens=r.tokens, elapsed_ms=r.elapsed_ms,
                               reasoning_tokens=r.reasoning_tokens, error=r.error)
    return results_dir
```

- [ ] **Step 4: Run to confirm pass**

Run: `.venv/bin/python -m pytest tests/test_bench_runner.py -q -p no:cacheprovider`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add bench/runner.py tests/test_bench_runner.py
git commit -m "feat(bench): runner with append-only results + Track B two-condition runs"
```

---

### Task 7: Runner CLI with cost rails

**Files:**
- Modify: `bench/runner.py` (add `main()` + argparse)
- Test: `tests/test_bench_runner_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bench_runner_cli.py
from bench.runner import resolve_tiers
from bench import config

def test_smoke_default_uses_flagships():
    assert resolve_tiers(all_tiers=False, explicit=None) == config.FLAGSHIP_TIERS

def test_all_flag_expands(monkeypatch):
    monkeypatch.setattr("bench.runner._all_chat_tiers", lambda: ["a", "b", "c"])
    assert resolve_tiers(all_tiers=True, explicit=None) == ["a", "b", "c"]

def test_explicit_tiers_win():
    assert resolve_tiers(all_tiers=True, explicit=["x"]) == ["x"]
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_bench_runner_cli.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'resolve_tiers'`.

- [ ] **Step 3: Add tier resolution + CLI (append to `bench/runner.py`)**

```python
def _all_chat_tiers():
    from config.agent_config import MODEL_CHAIN
    NONCHAT = {"Cohere-embed-v4", "text-embedding-3-large", "sora-2", "model-router"}
    return [c["name"] for c in MODEL_CHAIN if c.get("name") not in NONCHAT]


def resolve_tiers(*, all_tiers: bool, explicit):
    from bench import config
    if explicit:
        return list(explicit)
    if all_tiers:
        return _all_chat_tiers()
    return config.FLAGSHIP_TIERS


def main():
    import argparse, datetime
    from bench import config
    p = argparse.ArgumentParser(prog="bench", description="CroweLM benchmark runner")
    p.add_argument("--track", choices=["a", "b", "both"], default="both")
    p.add_argument("--all", action="store_true", help="Run ALL chat tiers (expensive).")
    p.add_argument("--tiers", nargs="*", help="Explicit tier names (overrides --all).")
    p.add_argument("--limit", type=int, default=5, help="Max questions per benchmark.")
    args = p.parse_args()

    tiers = resolve_tiers(all_tiers=args.all, explicit=args.tiers)
    n_a = args.limit if args.track in ("a", "both") else 0
    n_b = args.limit if args.track in ("b", "both") else 0
    runs = n_a * len(tiers) + n_b * len(tiers) * 2
    print(f"Tiers: {len(tiers)} | est. runs: {runs} (Track A: {n_a*len(tiers)}, "
          f"Track B: {n_b*len(tiers)*2}). Ctrl-C to abort.")

    # NOTE: dataset loading + dispatch wired in Task 9 (datasets) — this main
    # currently prints the plan and exits 0 so the cost estimate is reviewable
    # before any spend. Task 9 replaces the body below with real dispatch.
    return 0
```

(Task 9 fills in dataset loading + dispatch. This task delivers the cost-estimate gate.)

- [ ] **Step 4: Run to confirm pass**

Run: `.venv/bin/python -m pytest tests/test_bench_runner_cli.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bench/runner.py tests/test_bench_runner_cli.py
git commit -m "feat(bench): runner CLI with smoke-default + --all gate + cost estimate"
```

---

### Task 8: Report generator

**Files:**
- Create: `bench/report.py`
- Test: `tests/test_bench_report.py`

- [ ] **Step 1: Write the failing test (golden shape)**

```python
# tests/test_bench_report.py
import json
from bench.report import build_scoreboard

def test_track_b_delta_table(tmp_path):
    rows = [
        {"track":"b","condition":"grounded","tier":"gpt-5.4","question_id":"m1","score":5},
        {"track":"b","condition":"bare","tier":"gpt-5.4","question_id":"m1","score":2},
    ]
    p = tmp_path / "scored.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    md = build_scoreboard(p)
    assert "grounded" in md.lower() and "bare" in md.lower()
    assert "gpt-5.4" in md
    assert "delta" in md.lower() or "Δ" in md
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_bench_report.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.report'`.

- [ ] **Step 3: Implement the report**

```python
# bench/report.py
"""Render a scored results file into a Markdown scoreboard."""
from __future__ import annotations
import json, statistics
from collections import defaultdict
from pathlib import Path


def _load(path: Path):
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def build_scoreboard(scored_path: Path) -> str:
    rows = _load(scored_path)
    out = ["# CroweLM Benchmark Scoreboard", ""]

    # Track A: tier -> mean accuracy
    a = defaultdict(list)
    for r in rows:
        if r.get("track") == "a" and r.get("score") is not None:
            a[r["tier"]].append(r["score"])
    if a:
        out += ["## Track A — public benchmarks (backend baseline)", "",
                "| Tier (backend) | Accuracy | N |", "|---|---|---|"]
        for tier, scores in sorted(a.items()):
            out.append(f"| {tier} | {statistics.mean(scores)*100:.1f}% | {len(scores)} |")
        out.append("")

    # Track B: tier -> {grounded mean, bare mean, delta}
    b = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r.get("track") == "b" and r.get("score") is not None:
            b[r["tier"]][r["condition"]].append(r["score"])
    if b:
        out += ["## Track B — mycology: grounded vs bare (the CroweLM delta)", "",
                "| Tier (backend) | Grounded | Bare | Δ (delta) |", "|---|---|---|---|"]
        deltas = []
        for tier, conds in b.items():
            g = statistics.mean(conds["grounded"]) if conds["grounded"] else 0.0
            bare = statistics.mean(conds["bare"]) if conds["bare"] else 0.0
            deltas.append((tier, g, bare, g - bare))
        for tier, g, bare, d in sorted(deltas, key=lambda x: -x[3]):
            out.append(f"| {tier} | {g:.2f} | {bare:.2f} | {d:+.2f} |")
        out.append("")
        out.append("_Δ = grounded − bare on a 0–5 scale. The delta is the "
                   "platform's contribution over the base model._")
    return "\n".join(out)
```

- [ ] **Step 4: Run to confirm pass**

Run: `.venv/bin/python -m pytest tests/test_bench_report.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bench/report.py tests/test_bench_report.py
git commit -m "feat(bench): Markdown scoreboard with Track B grounded-vs-bare delta"
```

---

### Task 9: Datasets + wire runner dispatch

**Files:**
- Create: `bench/datasets/track_a/gsm8k.jsonl`, `mmlu.jsonl`, `humaneval.jsonl` (small committed slices)
- Create: `bench/generate_mycology_set.py`, `bench/datasets/track_b/mycology.jsonl`
- Modify: `bench/runner.py` (`main()` body → load datasets + dispatch + score)
- Test: `tests/test_bench_datasets.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bench_datasets.py
import json
from pathlib import Path
from bench import config

def test_track_a_slices_are_valid_jsonl():
    for name in ("gsm8k", "mmlu"):
        path = config.DATASETS_DIR / "track_a" / f"{name}.jsonl"
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        assert rows, f"{name} empty"
        for r in rows:
            assert {"id", "question", "answer", "type"} <= set(r)

def test_mycology_set_has_source_grounding():
    path = config.DATASETS_DIR / "track_b" / "mycology.jsonl"
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    assert rows
    for r in rows:
        assert {"id", "question", "source_passage", "reference_answer"} <= set(r)
        assert r["source_passage"].strip()
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_bench_datasets.py -q -p no:cacheprovider`
Expected: FAIL — files do not exist.

- [ ] **Step 3a: Hand-author small Track A slices**

Create `bench/datasets/track_a/gsm8k.jsonl` (≥3 rows), e.g.:
```json
{"id":"gsm1","question":"Natalia sold 48 clips in April and half as many in May. How many total?","answer":"72","type":"numeric"}
{"id":"gsm2","question":"A robe takes 2 bolts of blue and half that of white. Total bolts?","answer":"3","type":"numeric"}
{"id":"gsm3","question":"Weng earns $12/hr. For 50 minutes she earned?","answer":"10","type":"numeric"}
```
Create `bench/datasets/track_a/mmlu.jsonl` (≥3 rows, multiple_choice):
```json
{"id":"mmlu1","question":"The powerhouse of the cell is the: (A) nucleus (B) mitochondria (C) ribosome (D) golgi","answer":"B","type":"multiple_choice"}
{"id":"mmlu2","question":"H2O is commonly called: (A) salt (B) water (C) ammonia (D) methane","answer":"B","type":"multiple_choice"}
{"id":"mmlu3","question":"The capital of France is: (A) Berlin (B) Madrid (C) Paris (D) Rome","answer":"C","type":"multiple_choice"}
```
Create `bench/datasets/track_a/humaneval.jsonl` as a stub with `type:"code"` (3 rows) — code execution scoring is Task 10; for now rows just need the schema fields so the loader is uniform:
```json
{"id":"he1","question":"Write a Python function add(a,b) that returns a+b.","answer":"def add(a,b): return a+b","type":"code"}
```

- [ ] **Step 3b: Generator for the mycology set**

`bench/generate_mycology_set.py`:
```python
"""Generate bench/datasets/track_b/mycology.jsonl from the cultivation corpus.

Pulls passages via the crowe-portfolio MCP (semantic search over the mycology
books), then drafts grounded Q&A pairs. Run manually; output is committed so
benchmark runs are reproducible. Each row: id, question, source_passage,
source_doc, reference_answer.
"""
from __future__ import annotations
import json
from pathlib import Path
from bench import config

# The corpus passages are pulled via the MCP at authoring time. To keep the
# generator runnable without live MCP, it reads pre-fetched passages from
# bench/datasets/track_b/_passages.jsonl (id, text, doc) when present and
# asks a strong tier to draft a question + reference answer per passage.
PASSAGES = config.DATASETS_DIR / "track_b" / "_passages.jsonl"
OUT = config.DATASETS_DIR / "track_b" / "mycology.jsonl"


def build_qa_prompt(passage: str) -> str:
    return (
        "From the cultivation passage below, write ONE specific factual "
        "question a grower would ask, plus the correct answer grounded ONLY "
        "in the passage. Respond as JSON: "
        '{"question": "...", "reference_answer": "..."}\n\n'
        f"PASSAGE:\n{passage}"
    )


def main():
    from bench.headless_client import run_headless
    rows = []
    for line in PASSAGES.read_text().splitlines():
        if not line.strip():
            continue
        p = json.loads(line)
        res = run_headless(build_qa_prompt(p["text"]), config.JUDGE_TIER, tools=False)
        try:
            qa = json.loads(res.answer[res.answer.index("{"): res.answer.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            continue
        rows.append({"id": p["id"], "question": qa["question"],
                     "source_passage": p["text"], "source_doc": p.get("doc", ""),
                     "reference_answer": qa["reference_answer"]})
    OUT.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    print(f"wrote {len(rows)} questions to {OUT}")


if __name__ == "__main__":
    main()
```
For the committed `mycology.jsonl`, author ≥3 seed rows by hand (pulled from the cultivation books via the MCP during this task) so tests pass and a smoke run works without the generator:
```json
{"id":"myc1","question":"What substrate moisture range is recommended for oyster fruiting blocks?","source_passage":"Oyster fruiting blocks perform best at roughly 60-65% substrate moisture...","source_doc":"the-mushroom-grower","reference_answer":"About 60-65% substrate moisture."}
```
(Add 2+ more real rows from the corpus.)

- [ ] **Step 3c: Wire runner `main()` dispatch**

Replace the placeholder body of `bench/runner.py:main()` (from Task 7) so that after the cost-estimate print it loads the datasets, calls `run_track_a` / `run_track_b`, then scores into `scored.jsonl` and writes the scoreboard. Use a timestamp passed via env or arg (do NOT call `datetime.now()` inside tested code paths — keep the timestamp at the CLI boundary only). Concretely:

```python
    import datetime
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")  # CLI boundary only
    out_dir = config.RESULTS_DIR / ts

    if args.track in ("a", "both"):
        qa = _load_jsonl(config.DATASETS_DIR / "track_a" / "gsm8k.jsonl")[:args.limit]
        qa += _load_jsonl(config.DATASETS_DIR / "track_a" / "mmlu.jsonl")[:args.limit]
        run_track_a(qa, tiers, out_dir)
    if args.track in ("b", "both"):
        qb = _load_jsonl(config.DATASETS_DIR / "track_b" / "mycology.jsonl")[:args.limit]
        run_track_b(qb, tiers, out_dir)

    from bench.scoring import score_results_file
    score_results_file(out_dir / "raw.jsonl", out_dir / "scored.jsonl")
    from bench.report import build_scoreboard
    (out_dir / "scoreboard.md").write_text(build_scoreboard(out_dir / "scored.jsonl"))
    print(f"scoreboard: {out_dir / 'scoreboard.md'}")
    return 0
```
Add the `_load_jsonl` helper to `runner.py` and a `score_results_file(raw, out)` to `scoring.py` that applies Track A matchers and (for Track B) calls the judge via `run_headless(JUDGE_TIER, tools=False)` then `parse_judge_score`, writing a `score` field per row.

- [ ] **Step 4: Run dataset tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_bench_datasets.py bench tests -q -p no:cacheprovider -k bench`
Expected: dataset tests PASS; all bench unit tests still green.

- [ ] **Step 5: Commit**

```bash
git add bench/datasets bench/generate_mycology_set.py bench/runner.py bench/scoring.py tests/test_bench_datasets.py
git commit -m "feat(bench): Track A slices + mycology seed set + runner dispatch/scoring"
```

---

### Task 10: HumanEval code scoring (sandboxed)

**Files:**
- Modify: `bench/scoring.py`
- Test: `tests/test_bench_scoring_code.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bench_scoring_code.py
from bench.scoring import score_code

def test_code_passes_when_function_correct():
    answer = "def add(a,b):\n    return a+b"
    tests = "assert add(2,3)==5\nassert add(-1,1)==0"
    assert score_code(answer, tests) == 1.0

def test_code_fails_on_wrong_impl():
    answer = "def add(a,b):\n    return a-b"
    tests = "assert add(2,3)==5"
    assert score_code(answer, tests) == 0.0
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_bench_scoring_code.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'score_code'`.

- [ ] **Step 3: Implement sandboxed code scoring (append to `bench/scoring.py`)**

```python
import subprocess, sys, tempfile, os, textwrap


def score_code(answer: str, tests: str, *, timeout: int = 10) -> float:
    """Run candidate code + asserts in a subprocess; 1.0 if all pass."""
    program = textwrap.dedent(answer) + "\n" + textwrap.dedent(tests) + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(program)
        tmp = f.name
    try:
        proc = subprocess.run([sys.executable, tmp], capture_output=True,
                              text=True, timeout=timeout)
        return 1.0 if proc.returncode == 0 else 0.0
    except subprocess.TimeoutExpired:
        return 0.0
    finally:
        os.unlink(tmp)
```
(HumanEval rows in `humaneval.jsonl` carry a `tests` field; extend the loader/scorer to use it. If a row lacks `tests`, skip code scoring for that row.)

- [ ] **Step 4: Run to confirm pass**

Run: `.venv/bin/python -m pytest tests/test_bench_scoring_code.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bench/scoring.py tests/test_bench_scoring_code.py
git commit -m "feat(bench): sandboxed HumanEval code scoring (pass@1)"
```

---

### Task 11: Smoke run + README

**Files:**
- Create: `bench/README.md`
- Test: manual smoke (documented)

- [ ] **Step 1: Full unit suite green**

Run: `.venv/bin/python -m pytest tests -q -p no:cacheprovider -k bench`
Expected: all bench tests PASS; `.venv/bin/ruff check bench tests` clean.

- [ ] **Step 2: Cost-gate dry check (no spend)**

Run: `.venv/bin/python -m bench.runner --track both --limit 2`
Expected: prints the tier count + estimated runs, then proceeds on the smoke flagship set with `--limit 2`. (If you want zero spend, Ctrl-C at the estimate.)

- [ ] **Step 3: Write `bench/README.md`**

Document: smoke vs `--all`, `--track`, `--limit`, `--tiers`; where results land (`bench/results/<ts>/scoreboard.md`); the honesty framing (Track A = backend baseline, Track B = grounded-vs-bare delta); how to regenerate the mycology set; that the judge tier is pinned in `config.py`.

- [ ] **Step 4: Commit**

```bash
git add bench/README.md
git commit -m "docs(bench): usage, cost rails, and honest-framing notes"
```

---

## Self-Review

**Spec coverage:** Track A (Tasks 4,9,10) ✓; Track B grounded-vs-bare (Tasks 1,5,6,9) ✓; thin headless driver (Task 3) ✓; `--no-tools` prerequisite (Task 1) ✓; reuse rubric (noted in Task 9 scoring) ✓; smoke-default + `--all` cost rails (Task 7) ✓; report with delta + honest framing (Tasks 8,11) ✓; mycology generator from corpus (Task 9) ✓; tests no-live-API (all) ✓.

**Placeholder scan:** Task 7's `main()` is intentionally a two-stage delivery (cost-gate first, dispatch wired in Task 9) — explicitly flagged, not a hidden TODO. No other placeholders.

**Type consistency:** `RunResult` fields used consistently across client/runner/tests; `run_headless(prompt, model, tools=)` signature stable; row keys (`track`, `condition`, `tier`, `question_id`, `score`) consistent between runner, scoring, and report.

**Known follow-ups (out of scope, recorded in spec):** fine-tuned-vs-frontier head-to-head; CI benchmarking; public leaderboard site.
