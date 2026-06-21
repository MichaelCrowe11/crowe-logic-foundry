# Agentic Coding Eval Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `bench/agentic/` — a head-to-head agentic coding eval harness that scores `crowe-logic`'s control loop against a clean reference loop on the *same model*, producing a `scoreboard.md` with per-task and aggregate pass@1.

**Architecture:** An isolated subpackage alongside the existing `bench/` scoreboard (no edits to `runner.py`/`scoring.py`/`report.py`). Tasks are self-contained seed repos with a hidden `verify.sh`. A `sandbox` copies each task's `seed/` to a fresh tmp dir; pluggable `AgentRunner`s (crowe-logic via headless subprocess; a reference Opus loop; a deterministic stub for testing) mutate the copy; `verify.sh` scores pass/fail; results append to JSONL; `report.py` renders the scoreboard.

**Tech Stack:** Python 3.11, pytest, stdlib only for the harness (`subprocess`, `shutil`, `tempfile`, `json`, `dataclasses`). The reference agent uses the already-vendored `anthropic` client. No new dependencies.

## Global Constraints

- Python 3.10+ floor (repo standard); target 3.11. Use `from __future__ import annotations`.
- **Do not modify** `bench/runner.py`, `bench/scoring.py`, `bench/report.py`, or the live agent loop (`providers/_shared.py`, `cli/crowe_logic.py`). SP-0 is measurement-only.
- **No tokens spent in any unit test.** The stub `AgentRunner` covers the full pipeline. Real-model runners are exercised only by an explicit, manually-invoked run.
- Stdlib-only for harness control flow; the only third-party import is `anthropic` inside `reference.py` (already a repo dep).
- Results are **append-only JSONL** — never clobber a prior sweep.
- Follow repo test conventions: `tmp_path`, `monkeypatch`, no reliance on disk state outside the sandbox.
- Run the venv interpreter directly: `/Users/crowelogic/Projects/crowe-logic-foundry/.venv/bin/python` (the `.zshrc` PATH hook does not fire in non-interactive shells).

---

### Task 1: Package skeleton + result dataclasses

**Files:**
- Create: `bench/agentic/__init__.py`
- Create: `bench/agentic/agents/__init__.py`
- Create: `bench/agentic/agents/base.py`
- Test: `tests/agentic/test_base_contract.py`

**Interfaces:**
- Produces: `AgentResult` dataclass and `AgentRunner` Protocol (consumed by every later task).

```python
# bench/agentic/agents/base.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class AgentResult:
    workdir: Path
    transcript: list[dict]
    rounds: int
    tool_calls: int
    wall_s: float
    tokens: int | None
    cost_usd: float | None
    self_verified: bool
    error: str | None


class AgentRunner(Protocol):
    name: str

    def run(
        self,
        *,
        prompt: str,
        workdir: Path,
        model: str,
        tools: list[str],
        max_rounds: int,
        timeout_s: int,
    ) -> AgentResult: ...
```

- [ ] **Step 1: Write the failing test**

```python
# tests/agentic/test_base_contract.py
from pathlib import Path

from bench.agentic.agents.base import AgentResult


def test_agent_result_is_constructable():
    r = AgentResult(
        workdir=Path("/tmp/x"), transcript=[], rounds=3, tool_calls=5,
        wall_s=1.2, tokens=100, cost_usd=0.01, self_verified=True, error=None,
    )
    assert r.rounds == 3
    assert r.self_verified is True
    assert r.error is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/agentic/test_base_contract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.agentic'`

- [ ] **Step 3: Write minimal implementation** — create the three `__init__.py` files (empty) and `base.py` with the code from the Interfaces block above.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/agentic/test_base_contract.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bench/agentic/__init__.py bench/agentic/agents/ tests/agentic/test_base_contract.py
git commit -m "feat(bench/agentic): package skeleton + AgentResult/AgentRunner contract"
```

---

### Task 2: Sandbox (isolated per-run workdir)

**Files:**
- Create: `bench/agentic/sandbox.py`
- Test: `tests/agentic/test_sandbox.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `sandbox(seed: Path) -> contextmanager[Path]` — copies `seed/` to a fresh tmp dir, yields it, removes it on exit (even on exception).

```python
# bench/agentic/sandbox.py
from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def sandbox(seed: Path) -> Iterator[Path]:
    """Copy seed/ into a fresh tmp dir, yield it, guarantee teardown."""
    seed = Path(seed)
    if not seed.is_dir():
        raise ValueError(f"seed dir not found: {seed}")
    tmp = Path(tempfile.mkdtemp(prefix="agentic-bench-"))
    work = tmp / "work"
    shutil.copytree(seed, work)
    try:
        yield work
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/agentic/test_sandbox.py
from pathlib import Path

import pytest

from bench.agentic.sandbox import sandbox


def _seed(tmp_path: Path) -> Path:
    s = tmp_path / "seed"
    s.mkdir()
    (s / "a.txt").write_text("original")
    return s


def test_sandbox_copies_seed(tmp_path):
    seed = _seed(tmp_path)
    with sandbox(seed) as work:
        assert (work / "a.txt").read_text() == "original"


def test_mutating_workdir_never_touches_seed(tmp_path):
    seed = _seed(tmp_path)
    with sandbox(seed) as work:
        (work / "a.txt").write_text("mutated")
        (work / "new.txt").write_text("added")
    assert (seed / "a.txt").read_text() == "original"
    assert not (seed / "new.txt").exists()


def test_sandbox_cleans_up_on_exception(tmp_path):
    seed = _seed(tmp_path)
    captured = {}
    with pytest.raises(RuntimeError):
        with sandbox(seed) as work:
            captured["work"] = work
            raise RuntimeError("boom")
    assert not captured["work"].exists()


def test_missing_seed_raises(tmp_path):
    with pytest.raises(ValueError):
        with sandbox(tmp_path / "nope"):
            pass
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/agentic/test_sandbox.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.agentic.sandbox'`

- [ ] **Step 3: Write `sandbox.py`** from the Interfaces block.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/agentic/test_sandbox.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add bench/agentic/sandbox.py tests/agentic/test_sandbox.py
git commit -m "feat(bench/agentic): per-run isolated sandbox with guaranteed teardown"
```

---

### Task 3: Task loading + validation

**Files:**
- Create: `bench/agentic/tasks_io.py`
- Test: `tests/agentic/test_tasks_io.py`

**Interfaces:**
- Produces:
  - `@dataclass TaskMeta(lang: str, difficulty: str, tags: list[str], timeout_s: int, max_rounds: int)`
  - `@dataclass Task(task_id: str, seed: Path, prompt: str, verify: Path, meta: TaskMeta)`
  - `load_tasks(root: Path) -> list[Task]` — loads every `root/<id>/` dir; raises `ValueError` on a malformed `meta.json` or missing required file.

```python
# bench/agentic/tasks_io.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_REQUIRED_META = ("lang", "difficulty", "tags", "timeout_s", "max_rounds")


@dataclass
class TaskMeta:
    lang: str
    difficulty: str
    tags: list[str]
    timeout_s: int
    max_rounds: int


@dataclass
class Task:
    task_id: str
    seed: Path
    prompt: str
    verify: Path
    meta: TaskMeta


def _load_one(task_dir: Path) -> Task:
    meta_path = task_dir / "meta.json"
    prompt_path = task_dir / "prompt.txt"
    verify_path = task_dir / "verify.sh"
    seed_path = task_dir / "seed"
    for p in (meta_path, prompt_path, verify_path, seed_path):
        if not p.exists():
            raise ValueError(f"{task_dir.name}: missing {p.name}")
    raw = json.loads(meta_path.read_text())
    missing = [k for k in _REQUIRED_META if k not in raw]
    if missing:
        raise ValueError(f"{task_dir.name}: meta.json missing keys {missing}")
    meta = TaskMeta(
        lang=raw["lang"], difficulty=raw["difficulty"], tags=list(raw["tags"]),
        timeout_s=int(raw["timeout_s"]), max_rounds=int(raw["max_rounds"]),
    )
    return Task(
        task_id=task_dir.name, seed=seed_path,
        prompt=prompt_path.read_text().strip(), verify=verify_path, meta=meta,
    )


def load_tasks(root: Path) -> list[Task]:
    root = Path(root)
    dirs = sorted(d for d in root.iterdir() if d.is_dir() and (d / "meta.json").exists())
    return [_load_one(d) for d in dirs]
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/agentic/test_tasks_io.py
import json
from pathlib import Path

import pytest

from bench.agentic.tasks_io import load_tasks


def _make_task(root: Path, tid: str, meta: dict):
    d = root / tid
    (d / "seed").mkdir(parents=True)
    (d / "seed" / "code.py").write_text("x = 1\n")
    (d / "prompt.txt").write_text("do the thing")
    (d / "verify.sh").write_text("#!/bin/sh\nexit 0\n")
    (d / "meta.json").write_text(json.dumps(meta))


_GOOD = {"lang": "python", "difficulty": "easy", "tags": ["fix"], "timeout_s": 60, "max_rounds": 20}


def test_load_valid_task(tmp_path):
    _make_task(tmp_path, "t1", _GOOD)
    tasks = load_tasks(tmp_path)
    assert len(tasks) == 1
    assert tasks[0].task_id == "t1"
    assert tasks[0].prompt == "do the thing"
    assert tasks[0].meta.max_rounds == 20


def test_malformed_meta_rejected(tmp_path):
    bad = dict(_GOOD); del bad["timeout_s"]
    _make_task(tmp_path, "t1", bad)
    with pytest.raises(ValueError, match="missing keys"):
        load_tasks(tmp_path)


def test_tasks_loaded_in_sorted_order(tmp_path):
    _make_task(tmp_path, "b", _GOOD)
    _make_task(tmp_path, "a", _GOOD)
    assert [t.task_id for t in load_tasks(tmp_path)] == ["a", "b"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/agentic/test_tasks_io.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `tasks_io.py`** from the Interfaces block.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/agentic/test_tasks_io.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add bench/agentic/tasks_io.py tests/agentic/test_tasks_io.py
git commit -m "feat(bench/agentic): task loader with meta.json validation"
```

---

### Task 4: Verification runner (`verify.sh` in a timed subprocess)

**Files:**
- Create: `bench/agentic/verify.py`
- Test: `tests/agentic/test_verify.py`

**Interfaces:**
- Produces: `@dataclass VerifyResult(passed: bool, exit_code: int | None, timed_out: bool, output: str)` and `run_verify(verify_sh: Path, workdir: Path, timeout_s: int) -> VerifyResult`.
- Contract: exit 0 → `passed=True`; non-zero → `passed=False`; timeout → `passed=False, timed_out=True`. Runs `verify.sh` with `cwd=workdir`. The verify script is copied into the workdir first so it can reference task-relative paths.

```python
# bench/agentic/verify.py
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VerifyResult:
    passed: bool
    exit_code: int | None
    timed_out: bool
    output: str


def run_verify(verify_sh: Path, workdir: Path, timeout_s: int) -> VerifyResult:
    dest = Path(workdir) / "_verify.sh"
    shutil.copyfile(verify_sh, dest)
    dest.chmod(0o755)
    try:
        proc = subprocess.run(
            ["/bin/sh", str(dest)],
            cwd=str(workdir),
            capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as e:
        return VerifyResult(False, None, True, (e.output or "") if isinstance(e.output, str) else "")
    finally:
        dest.unlink(missing_ok=True)
    return VerifyResult(
        passed=proc.returncode == 0, exit_code=proc.returncode,
        timed_out=False, output=(proc.stdout + proc.stderr)[-4000:],
    )
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/agentic/test_verify.py
from pathlib import Path

from bench.agentic.verify import run_verify


def _script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "verify.sh"
    p.write_text("#!/bin/sh\n" + body + "\n")
    return p


def test_exit_zero_passes(tmp_path):
    work = tmp_path / "w"; work.mkdir()
    res = run_verify(_script(tmp_path, "exit 0"), work, timeout_s=10)
    assert res.passed and res.exit_code == 0 and not res.timed_out


def test_nonzero_fails(tmp_path):
    work = tmp_path / "w"; work.mkdir()
    res = run_verify(_script(tmp_path, "exit 3"), work, timeout_s=10)
    assert not res.passed and res.exit_code == 3


def test_timeout_fails(tmp_path):
    work = tmp_path / "w"; work.mkdir()
    res = run_verify(_script(tmp_path, "sleep 5"), work, timeout_s=1)
    assert not res.passed and res.timed_out


def test_runs_in_workdir(tmp_path):
    work = tmp_path / "w"; work.mkdir()
    (work / "marker").write_text("yes")
    res = run_verify(_script(tmp_path, "test -f marker"), work, timeout_s=10)
    assert res.passed
```

- [ ] **Step 2: Run to verify they fail.** `.venv/bin/python -m pytest tests/agentic/test_verify.py -v` → module not found.
- [ ] **Step 3: Write `verify.py`** from the Interfaces block.
- [ ] **Step 4: Run to verify they pass.** Expected: 4 passed.
- [ ] **Step 5: Commit**

```bash
git add bench/agentic/verify.py tests/agentic/test_verify.py
git commit -m "feat(bench/agentic): timed verify.sh subprocess runner"
```

---

### Task 5: Stub AgentRunner (token-free pipeline driver)

**Files:**
- Create: `bench/agentic/agents/stub.py`
- Test: `tests/agentic/test_stub_runner.py`

**Interfaces:**
- Consumes: `AgentResult` (Task 1).
- Produces: `StubRunner(name, mutate)` — a deterministic runner. `mutate: Callable[[Path], None]` applies a fixed edit to the workdir so a paired `verify.sh` can score it green or red. Used by every runner/report test so no tokens are spent.

```python
# bench/agentic/agents/stub.py
from __future__ import annotations

from pathlib import Path
from typing import Callable

from bench.agentic.agents.base import AgentResult


class StubRunner:
    def __init__(self, name: str = "stub", mutate: Callable[[Path], None] | None = None,
                 self_verified: bool = False, error: str | None = None):
        self.name = name
        self._mutate = mutate
        self._self_verified = self_verified
        self._error = error

    def run(self, *, prompt, workdir, model, tools, max_rounds, timeout_s) -> AgentResult:
        if self._mutate is not None:
            self._mutate(Path(workdir))
        return AgentResult(
            workdir=Path(workdir), transcript=[{"role": "stub", "prompt": prompt}],
            rounds=1, tool_calls=1, wall_s=0.0, tokens=0, cost_usd=0.0,
            self_verified=self._self_verified, error=self._error,
        )
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/agentic/test_stub_runner.py
from pathlib import Path

from bench.agentic.agents.stub import StubRunner


def test_stub_applies_mutation(tmp_path):
    def fix(work: Path):
        (work / "patched.txt").write_text("ok")
    r = StubRunner(name="fixer", mutate=fix)
    res = r.run(prompt="p", workdir=tmp_path, model="m", tools=[], max_rounds=5, timeout_s=10)
    assert (tmp_path / "patched.txt").read_text() == "ok"
    assert res.error is None and res.workdir == tmp_path


def test_stub_can_report_error(tmp_path):
    r = StubRunner(name="crasher", error="boom")
    res = r.run(prompt="p", workdir=tmp_path, model="m", tools=[], max_rounds=5, timeout_s=10)
    assert res.error == "boom"
```

- [ ] **Step 2: Run to verify they fail.** Module not found.
- [ ] **Step 3: Write `stub.py`** from the Interfaces block.
- [ ] **Step 4: Run to verify they pass.** Expected: 2 passed.
- [ ] **Step 5: Commit**

```bash
git add bench/agentic/agents/stub.py tests/agentic/test_stub_runner.py
git commit -m "feat(bench/agentic): deterministic StubRunner for token-free tests"
```

---

### Task 6: Scoring + JSONL records

**Files:**
- Create: `bench/agentic/score.py`
- Test: `tests/agentic/test_score.py`

**Interfaces:**
- Consumes: `AgentResult` (Task 1), `VerifyResult` (Task 4).
- Produces:
  - `make_row(task_id, agent_name, model, result: AgentResult, verify: VerifyResult) -> dict` — the JSONL row schema.
  - `append_row(path: Path, row: dict) -> None` — append-only, flushed.
  - `aggregate(rows: list[dict]) -> dict` — `{agent_name: {"pass_at_1": float, "n": int, "self_verified_rate": float, "avg_rounds": float}}`.

```python
# bench/agentic/score.py
from __future__ import annotations

import json
from pathlib import Path

from bench.agentic.agents.base import AgentResult
from bench.agentic.verify import VerifyResult


def make_row(task_id: str, agent_name: str, model: str,
             result: AgentResult, verify: VerifyResult) -> dict:
    return {
        "task_id": task_id, "agent": agent_name, "model": model,
        "passed": bool(verify.passed),
        "exit_code": verify.exit_code, "timed_out": verify.timed_out,
        "rounds": result.rounds, "tool_calls": result.tool_calls,
        "wall_s": round(result.wall_s, 3), "tokens": result.tokens,
        "cost_usd": result.cost_usd, "self_verified": result.self_verified,
        "error": result.error, "verify_output": verify.output,
    }


def append_row(path: Path, row: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
        fh.flush()


def aggregate(rows: list[dict]) -> dict:
    by_agent: dict[str, list[dict]] = {}
    for r in rows:
        by_agent.setdefault(r["agent"], []).append(r)
    out: dict[str, dict] = {}
    for agent, rs in by_agent.items():
        n = len(rs)
        out[agent] = {
            "n": n,
            "pass_at_1": sum(1 for r in rs if r["passed"]) / n if n else 0.0,
            "self_verified_rate": sum(1 for r in rs if r["self_verified"]) / n if n else 0.0,
            "avg_rounds": sum(r["rounds"] for r in rs) / n if n else 0.0,
        }
    return out
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/agentic/test_score.py
from pathlib import Path

from bench.agentic.agents.base import AgentResult
from bench.agentic.verify import VerifyResult
from bench.agentic.score import make_row, append_row, aggregate


def _res(self_verified=True, rounds=2):
    return AgentResult(workdir=Path("/w"), transcript=[], rounds=rounds, tool_calls=3,
                       wall_s=1.0, tokens=10, cost_usd=0.0, self_verified=self_verified, error=None)


def _ok():
    return VerifyResult(passed=True, exit_code=0, timed_out=False, output="ok")


def _fail():
    return VerifyResult(passed=False, exit_code=1, timed_out=False, output="bad")


def test_make_row_shape():
    row = make_row("t1", "stub", "opus", _res(), _ok())
    assert row["task_id"] == "t1" and row["passed"] is True and row["self_verified"] is True


def test_append_row_is_appending(tmp_path):
    p = tmp_path / "raw.jsonl"
    append_row(p, make_row("t1", "a", "m", _res(), _ok()))
    append_row(p, make_row("t2", "a", "m", _res(), _fail()))
    assert len(p.read_text().splitlines()) == 2


def test_aggregate_pass_at_1():
    rows = [
        make_row("t1", "crowe", "opus", _res(), _ok()),
        make_row("t2", "crowe", "opus", _res(), _fail()),
        make_row("t1", "ref", "opus", _res(), _ok()),
    ]
    agg = aggregate(rows)
    assert agg["crowe"]["pass_at_1"] == 0.5
    assert agg["ref"]["pass_at_1"] == 1.0
    assert agg["crowe"]["n"] == 2
```

- [ ] **Step 2: Run to verify they fail.** Module not found.
- [ ] **Step 3: Write `score.py`** from the Interfaces block.
- [ ] **Step 4: Run to verify they pass.** Expected: 3 passed.
- [ ] **Step 5: Commit**

```bash
git add bench/agentic/score.py tests/agentic/test_score.py
git commit -m "feat(bench/agentic): JSONL row schema + pass@1 aggregation"
```

---

### Task 7: Report (scoreboard.md)

**Files:**
- Create: `bench/agentic/report.py`
- Test: `tests/agentic/test_report.py`

**Interfaces:**
- Consumes: aggregate output (Task 6) and the raw rows.
- Produces: `render_scoreboard(rows: list[dict], crowe_agent: str = "crowe-logic", baseline_agent: str = "reference") -> str` — markdown with (a) an aggregate table including a **gap** row (`baseline pass@1 − crowe pass@1`, in percentage points) and (b) a per-task pass/fail matrix.

```python
# bench/agentic/report.py
from __future__ import annotations

from bench.agentic.score import aggregate


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def render_scoreboard(rows: list[dict], crowe_agent: str = "crowe-logic",
                      baseline_agent: str = "reference") -> str:
    agg = aggregate(rows)
    agents = sorted(agg)
    lines = ["# Agentic Coding Eval — Scoreboard", ""]
    lines += ["| agent | n | pass@1 | self-verified | avg rounds |",
              "|---|---|---|---|---|"]
    for a in agents:
        m = agg[a]
        lines.append(f"| {a} | {m['n']} | {_pct(m['pass_at_1'])} "
                     f"| {_pct(m['self_verified_rate'])} | {m['avg_rounds']:.1f} |")
    if crowe_agent in agg and baseline_agent in agg:
        gap = (agg[baseline_agent]["pass_at_1"] - agg[crowe_agent]["pass_at_1"]) * 100
        lines += ["", f"**Harness-isolated gap (baseline − crowe-logic):** {gap:+.0f} pp"]
    # per-task matrix
    task_ids = sorted({r["task_id"] for r in rows})
    lines += ["", "## Per-task pass@1", "",
              "| task | " + " | ".join(agents) + " |",
              "|---|" + "|".join(["---"] * len(agents)) + "|"]
    seen = {(r["task_id"], r["agent"]): r["passed"] for r in rows}
    for t in task_ids:
        cells = ["✅" if seen.get((t, a)) else "❌" for a in agents]
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 1: Write the failing test**

```python
# tests/agentic/test_report.py
from pathlib import Path

from bench.agentic.agents.base import AgentResult
from bench.agentic.verify import VerifyResult
from bench.agentic.score import make_row
from bench.agentic.report import render_scoreboard


def _row(task, agent, passed):
    res = AgentResult(workdir=Path("/w"), transcript=[], rounds=2, tool_calls=1,
                      wall_s=1.0, tokens=1, cost_usd=0.0, self_verified=passed, error=None)
    vr = VerifyResult(passed=passed, exit_code=0 if passed else 1, timed_out=False, output="")
    return make_row(task, agent, "opus", res, vr)


def test_scoreboard_has_gap_and_matrix():
    rows = [
        _row("t1", "crowe-logic", True), _row("t2", "crowe-logic", False),
        _row("t1", "reference", True), _row("t2", "reference", True),
    ]
    md = render_scoreboard(rows)
    assert "pass@1" in md
    assert "gap" in md.lower()
    assert "+50 pp" in md  # reference 100% - crowe 50%
    assert "## Per-task pass@1" in md
    assert "t1" in md and "t2" in md
```

- [ ] **Step 2: Run to verify it fails.** Module not found.
- [ ] **Step 3: Write `report.py`** from the Interfaces block.
- [ ] **Step 4: Run to verify it passes.** Expected: 1 passed.
- [ ] **Step 5: Commit**

```bash
git add bench/agentic/report.py tests/agentic/test_report.py
git commit -m "feat(bench/agentic): scoreboard.md with gap + per-task matrix"
```

---

### Task 8: Runner (orchestrator) + fixture tasks (end-to-end, token-free)

**Files:**
- Create: `bench/agentic/runner.py`
- Create: `bench/agentic/tasks/_fixtures/pass_trivial/{seed/code.py,prompt.txt,verify.sh,meta.json}`
- Create: `bench/agentic/tasks/_fixtures/fail_trivial/{seed/code.py,prompt.txt,verify.sh,meta.json}`
- Test: `tests/agentic/test_runner_e2e.py`

**Interfaces:**
- Consumes: `load_tasks` (T3), `sandbox` (T2), `AgentRunner` (T1), `run_verify` (T4), `make_row`/`append_row` (T6), `render_scoreboard` (T7).
- Produces: `run_suite(tasks_root: Path, runners: list[AgentRunner], results_dir: Path, model: str) -> Path` — for each `(task × runner)`: sandbox → `runner.run(...)` → `run_verify(...)` → append row; then writes `scoreboard.md`. Returns `results_dir`. Catches per-run exceptions into a fail row (`error=...`) so one crash never aborts the sweep.

```python
# bench/agentic/runner.py
from __future__ import annotations

import time
from pathlib import Path

from bench.agentic.agents.base import AgentResult
from bench.agentic.report import render_scoreboard
from bench.agentic.sandbox import sandbox
from bench.agentic.score import append_row, make_row
from bench.agentic.tasks_io import load_tasks
from bench.agentic.verify import VerifyResult, run_verify


def _crash_result(workdir: Path, err: str) -> AgentResult:
    return AgentResult(workdir=workdir, transcript=[], rounds=0, tool_calls=0,
                       wall_s=0.0, tokens=None, cost_usd=None, self_verified=False, error=err)


def run_suite(tasks_root, runners, results_dir, model: str) -> Path:
    tasks_root = Path(tasks_root); results_dir = Path(results_dir)
    raw = results_dir / "raw.jsonl"
    tasks = load_tasks(tasks_root)
    rows: list[dict] = []
    for task in tasks:
        for runner in runners:
            with sandbox(task.seed) as work:
                t0 = time.monotonic()
                try:
                    result = runner.run(
                        prompt=task.prompt, workdir=work, model=model,
                        tools=[], max_rounds=task.meta.max_rounds,
                        timeout_s=task.meta.timeout_s,
                    )
                except Exception as e:  # crash isolation: record, never abort
                    result = _crash_result(work, f"{type(e).__name__}: {e}")
                    result.wall_s = time.monotonic() - t0
                if result.error:
                    verify = VerifyResult(False, None, False, result.error)
                else:
                    verify = run_verify(task.verify, work, task.meta.timeout_s)
                row = make_row(task.task_id, runner.name, model, result, verify)
                append_row(raw, row)
                rows.append(row)
    (results_dir / "scoreboard.md").write_text(render_scoreboard(rows))
    return results_dir


if __name__ == "__main__":  # pragma: no cover
    import argparse
    from bench.agentic.agents.crowe_logic import CroweLogicRunner
    from bench.agentic.agents.reference import ReferenceRunner

    ap = argparse.ArgumentParser(description="Run the agentic coding eval suite.")
    ap.add_argument("--tasks", default="bench/agentic/tasks")
    ap.add_argument("--results", default="bench/agentic/results")
    ap.add_argument("--model", default="claude-opus-4-8")
    args = ap.parse_args()
    out = run_suite(args.tasks, [CroweLogicRunner(), ReferenceRunner()], args.results, args.model)
    print(f"scoreboard: {out / 'scoreboard.md'}")
```

Fixture files (exact contents):

```python
# bench/agentic/tasks/_fixtures/pass_trivial/seed/code.py
def add(a, b):
    return a + b
```
```
# bench/agentic/tasks/_fixtures/pass_trivial/prompt.txt
add(a, b) should return the sum. (Already correct — fixture.)
```
```sh
# bench/agentic/tasks/_fixtures/pass_trivial/verify.sh
#!/bin/sh
python -c "from code import add; assert add(2,3)==5"
```
```json
# bench/agentic/tasks/_fixtures/pass_trivial/meta.json
{"lang":"python","difficulty":"trivial","tags":["fixture"],"timeout_s":30,"max_rounds":5}
```
```python
# bench/agentic/tasks/_fixtures/fail_trivial/seed/code.py
def add(a, b):
    return a - b
```
```
# bench/agentic/tasks/_fixtures/fail_trivial/prompt.txt
add(a, b) should return the sum but returns the difference. Fix it.
```
```sh
# bench/agentic/tasks/_fixtures/fail_trivial/verify.sh
#!/bin/sh
python -c "from code import add; assert add(2,3)==5"
```
```json
# bench/agentic/tasks/_fixtures/fail_trivial/meta.json
{"lang":"python","difficulty":"trivial","tags":["fixture"],"timeout_s":30,"max_rounds":5}
```

- [ ] **Step 1: Write the failing end-to-end test** (uses only StubRunner — no tokens)

```python
# tests/agentic/test_runner_e2e.py
from pathlib import Path

from bench.agentic.agents.stub import StubRunner
from bench.agentic.runner import run_suite

FIX = Path("bench/agentic/tasks/_fixtures")


def test_e2e_green_and_red(tmp_path):
    # A runner that fixes the bug (subtract -> add) passes both fixtures;
    # a no-op runner passes pass_trivial and fails fail_trivial.
    def fix(work: Path):
        (work / "code.py").write_text("def add(a, b):\n    return a + b\n")
    fixer = StubRunner(name="reference", mutate=fix, self_verified=True)
    noop = StubRunner(name="crowe-logic", mutate=None)

    out = run_suite(FIX, [fixer, noop], tmp_path, model="stub")
    rows = [__import__("json").loads(l) for l in (out / "raw.jsonl").read_text().splitlines()]
    by = {(r["task_id"], r["agent"]): r["passed"] for r in rows}

    assert by[("pass_trivial", "reference")] is True
    assert by[("fail_trivial", "reference")] is True
    assert by[("pass_trivial", "crowe-logic")] is True
    assert by[("fail_trivial", "crowe-logic")] is False  # no-op leaves bug

    sb = (out / "scoreboard.md").read_text()
    assert "pass@1" in sb and "gap" in sb.lower()


def test_crashing_runner_is_recorded_not_raised(tmp_path):
    class Boom:
        name = "boom"
        def run(self, **kw):
            raise RuntimeError("kaboom")
    out = run_suite(FIX, [Boom()], tmp_path, model="stub")
    rows = [__import__("json").loads(l) for l in (out / "raw.jsonl").read_text().splitlines()]
    assert all(r["passed"] is False for r in rows)
    assert any("kaboom" in (r["error"] or "") for r in rows)
```

- [ ] **Step 2: Run to verify it fails.** `.venv/bin/python -m pytest tests/agentic/test_runner_e2e.py -v` → module not found.
- [ ] **Step 3: Create the fixture files and `runner.py`** exactly as above. `chmod +x` is not needed (verify runs them via `/bin/sh`).
- [ ] **Step 4: Run to verify it passes.** Expected: 2 passed. **This is the token-free end-to-end proof (SP-0 success criteria #1–#3).**
- [ ] **Step 5: Commit**

```bash
git add bench/agentic/runner.py bench/agentic/tasks/_fixtures tests/agentic/test_runner_e2e.py
git commit -m "feat(bench/agentic): suite runner + fixtures, token-free end-to-end green/red"
```

---

### Task 9: crowe-logic adapter (drives headless as subprocess)

**Files:**
- Create: `bench/agentic/agents/crowe_logic.py`
- Test: `tests/agentic/test_crowe_logic_adapter.py`

**Interfaces:**
- Consumes: `AgentResult` (T1).
- Produces: `CroweLogicRunner(name="crowe-logic")` implementing `AgentRunner`. Also a pure, separately-tested parser `parse_events(events: list[dict]) -> dict` returning `{"rounds", "tool_calls", "tokens", "self_verified", "error"}` so the parsing logic is unit-tested without a subprocess.
- Event mapping (verified against `cli/headless.py` docstring): `segment_end` → +1 round; `tool` → +1 tool_call, and `self_verified=True` if its `name`/`args` indicate a pytest/test run; `done` → `tokens`; `error` → `error`.

```python
# bench/agentic/agents/crowe_logic.py
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from bench.agentic.agents.base import AgentResult

# Tool signatures that count as the agent verifying its own work.
_TEST_MARKERS = ("pytest", "verify.sh", "python -m pytest", "unittest")


def _looks_like_test_run(name: str, args: str) -> bool:
    blob = f"{name} {args}".lower()
    return any(m in blob for m in _TEST_MARKERS)


def parse_events(events: list[dict]) -> dict:
    rounds = tool_calls = 0
    tokens = None
    self_verified = False
    error = None
    for ev in events:
        et = ev.get("type")
        if et == "segment_end":
            rounds += 1
        elif et == "tool":
            tool_calls += 1
            if _looks_like_test_run(str(ev.get("name", "")), str(ev.get("args", ""))):
                self_verified = True
        elif et == "done":
            tokens = ev.get("tokens")
        elif et == "error":
            error = ev.get("message", "unknown error")
    return {"rounds": rounds, "tool_calls": tool_calls, "tokens": tokens,
            "self_verified": self_verified, "error": error}


class CroweLogicRunner:
    name = "crowe-logic"

    def run(self, *, prompt, workdir, model, tools, max_rounds, timeout_s) -> AgentResult:
        workdir = Path(workdir)
        payload = {"messages": [{"role": "user", "content": prompt}], "model": model}
        t0 = time.monotonic()
        events: list[dict] = []
        error = None
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "cli.headless"],
                input=json.dumps(payload), cwd=str(workdir),
                capture_output=True, text=True, timeout=timeout_s,
                env=_execute_env(),
            )
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except subprocess.TimeoutExpired:
            error = f"timeout after {timeout_s}s"
        wall_s = time.monotonic() - t0
        parsed = parse_events(events)
        if error and not parsed["error"]:
            parsed["error"] = error
        return AgentResult(
            workdir=workdir, transcript=events, rounds=parsed["rounds"],
            tool_calls=parsed["tool_calls"], wall_s=wall_s, tokens=parsed["tokens"],
            cost_usd=None, self_verified=parsed["self_verified"], error=parsed["error"],
        )


def _execute_env() -> dict:
    """Headless reads autonomy from cli.autonomy; force 'execute' for the bench."""
    import os
    env = dict(os.environ)
    env["CROWE_LOGIC_AUTONOMY"] = "execute"
    return env
```

> **Implementer note (integration to verify at build time):** confirm how `cli.autonomy.get_active_level()` resolves the level — if it does **not** read `CROWE_LOGIC_AUTONOMY`, set the level via the mechanism it *does* read (env var name, config file, or a leading control message in `messages`). This is the one runtime coupling the spec flags; the parser and everything else are already isolated and unit-tested.

- [ ] **Step 1: Write the failing parser tests** (no subprocess — pure function)

```python
# tests/agentic/test_crowe_logic_adapter.py
from bench.agentic.agents.crowe_logic import parse_events


def test_counts_rounds_and_tools():
    events = [
        {"type": "segment_end"},
        {"type": "tool", "name": "write_file", "args": "code.py"},
        {"type": "segment_end"},
        {"type": "done", "tokens": 1234},
    ]
    p = parse_events(events)
    assert p["rounds"] == 2 and p["tool_calls"] == 1 and p["tokens"] == 1234
    assert p["self_verified"] is False and p["error"] is None


def test_detects_self_verification():
    events = [{"type": "tool", "name": "run_shell", "args": "python -m pytest -q"}]
    assert parse_events(events)["self_verified"] is True


def test_surfaces_error_event():
    events = [{"type": "error", "message": "model failed"}]
    assert parse_events(events)["error"] == "model failed"
```

- [ ] **Step 2: Run to verify they fail.** Module not found.
- [ ] **Step 3: Write `crowe_logic.py`** from the Interfaces block.
- [ ] **Step 4: Run to verify they pass.** Expected: 3 passed.
- [ ] **Step 5: Commit**

```bash
git add bench/agentic/agents/crowe_logic.py tests/agentic/test_crowe_logic_adapter.py
git commit -m "feat(bench/agentic): crowe-logic headless adapter + event parser"
```

---

### Task 10: Reference agent (clean plan→act→verify loop on Opus)

**Files:**
- Create: `bench/agentic/agents/reference.py`
- Test: `tests/agentic/test_reference_runner.py`

**Interfaces:**
- Consumes: `AgentResult` (T1).
- Produces: `ReferenceRunner(name="reference")` implementing `AgentRunner`. A **minimal** loop, intentionally distinct from `providers/anthropic.py:stream_response` (which is the crowe-logic side under test). Uses the `anthropic` client directly for model access only, with two tools — `read_file` and `write_file` — scoped to the workdir, plus a `run_tests` tool that runs `pytest -q`. The loop: send prompt + tool schemas → on tool_use, execute against the workdir → feed results back → stop when the model emits no tool call or `max_rounds` is hit. Model access (endpoint/key) is read from the same env the foundry's anthropic provider uses, so the *model* is identical across both harnesses.
- The model call is funneled through one private method `_complete(messages, tools)` so tests can `monkeypatch` it and exercise the **loop logic with zero tokens**.

```python
# bench/agentic/agents/reference.py
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from bench.agentic.agents.base import AgentResult

_TOOLS = [
    {"name": "read_file", "description": "Read a file in the workdir.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                      "required": ["path"]}},
    {"name": "write_file", "description": "Write a file in the workdir.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "content": {"type": "string"}},
                      "required": ["path", "content"]}},
    {"name": "run_tests", "description": "Run pytest -q in the workdir; returns output.",
     "input_schema": {"type": "object", "properties": {}}},
]

_SYSTEM = (
    "You are a coding agent. Plan, then act using the tools, then VERIFY by "
    "running the tests before you finish. Only stop once tests pass. "
    "All paths are relative to the working directory."
)


def _exec_tool(name: str, args: dict, workdir: Path) -> str:
    if name == "read_file":
        p = (workdir / args["path"]).resolve()
        if workdir.resolve() not in p.parents and p != workdir.resolve():
            return "error: path escapes workdir"
        return p.read_text() if p.exists() else f"error: {args['path']} not found"
    if name == "write_file":
        p = (workdir / args["path"]).resolve()
        if workdir.resolve() not in p.parents:
            return "error: path escapes workdir"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"])
        return f"wrote {args['path']}"
    if name == "run_tests":
        proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=str(workdir),
                              capture_output=True, text=True, timeout=120)
        return (proc.stdout + proc.stderr)[-3000:]
    return f"error: unknown tool {name}"


class ReferenceRunner:
    name = "reference"

    def __init__(self, model: str | None = None):
        self._model_override = model

    def _complete(self, messages: list[dict], model: str):
        """One Anthropic completion. Isolated so tests monkeypatch it."""
        from providers.anthropic import AnthropicProvider  # reuse creds resolution
        import os
        from anthropic import Anthropic

        client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY")
                           or os.environ.get("AZURE_ANTHROPIC_API_KEY"))
        return client.messages.create(
            model=model, max_tokens=4096, system=_SYSTEM, tools=_TOOLS, messages=messages,
        )

    def run(self, *, prompt, workdir, model, tools, max_rounds, timeout_s) -> AgentResult:
        workdir = Path(workdir)
        model = self._model_override or model
        messages: list[dict] = [{"role": "user", "content": prompt}]
        rounds = tool_calls = 0
        self_verified = False
        error = None
        t0 = time.monotonic()
        try:
            while rounds < max_rounds:
                rounds += 1
                resp = self._complete(messages, model)
                tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
                messages.append({"role": "assistant", "content": resp.content})
                if not tool_uses:
                    break
                results = []
                for tu in tool_uses:
                    tool_calls += 1
                    if tu.name == "run_tests":
                        self_verified = True
                    out = _exec_tool(tu.name, dict(tu.input), workdir)
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out})
                messages.append({"role": "user", "content": results})
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        return AgentResult(
            workdir=workdir, transcript=messages, rounds=rounds, tool_calls=tool_calls,
            wall_s=time.monotonic() - t0, tokens=None, cost_usd=None,
            self_verified=self_verified, error=error,
        )
```

- [ ] **Step 1: Write the failing loop test** (monkeypatched `_complete` — no tokens)

```python
# tests/agentic/test_reference_runner.py
from pathlib import Path
from types import SimpleNamespace

from bench.agentic.agents.reference import ReferenceRunner


class _Block(SimpleNamespace):
    pass


def _tool_use(tid, name, inp):
    return _Block(type="tool_use", id=tid, name=name, input=inp)


def test_reference_loop_writes_then_verifies(tmp_path, monkeypatch):
    (tmp_path / "code.py").write_text("def add(a,b):\n    return a-b\n")
    runner = ReferenceRunner()
    calls = {"n": 0}

    def fake_complete(messages, model):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(content=[_tool_use(
                "1", "write_file", {"path": "code.py", "content": "def add(a,b):\n    return a+b\n"})])
        if calls["n"] == 2:
            return SimpleNamespace(content=[_tool_use("2", "run_tests", {})])
        return SimpleNamespace(content=[_Block(type="text", text="done")])

    monkeypatch.setattr(runner, "_complete", fake_complete)
    res = runner.run(prompt="fix add", workdir=tmp_path, model="opus",
                     tools=[], max_rounds=10, timeout_s=60)
    assert (tmp_path / "code.py").read_text().strip().endswith("return a+b")
    assert res.self_verified is True
    assert res.tool_calls == 2
    assert res.error is None
    assert res.rounds == 3  # write, test, final text-only stop


def test_reference_loop_stops_at_max_rounds(tmp_path, monkeypatch):
    runner = ReferenceRunner()
    monkeypatch.setattr(runner, "_complete", lambda m, model: SimpleNamespace(
        content=[_tool_use("x", "run_tests", {})]))  # never stops on its own
    res = runner.run(prompt="loop", workdir=tmp_path, model="opus",
                     tools=[], max_rounds=4, timeout_s=60)
    assert res.rounds == 4
```

- [ ] **Step 2: Run to verify they fail.** Module not found.
- [ ] **Step 3: Write `reference.py`** from the Interfaces block.
- [ ] **Step 4: Run to verify they pass.** Expected: 2 passed.
- [ ] **Step 5: Commit**

```bash
git add bench/agentic/agents/reference.py tests/agentic/test_reference_runner.py
git commit -m "feat(bench/agentic): reference plan-act-verify loop (Opus, monkeypatchable)"
```

---

### Task 11: Starter task suite (~12 real tasks) + README

**Files:**
- Create: `bench/agentic/tasks/<id>/{seed/,prompt.txt,verify.sh,meta.json}` ×12
- Create: `bench/agentic/README.md`
- Test: `tests/agentic/test_starter_suite_loads.py`

**Interfaces:**
- Consumes: `load_tasks` (T3).
- Produces: a committed suite spanning easy→hard, each with a failing pytest suite in `seed/` that `verify.sh` runs (`#!/bin/sh` / `python -m pytest -q`). README documents: how to run, how to add a task, and the honesty framing (copied from the spec §1 and §5 baseline table).

Suite (difficulty in `meta.json`): `parse_duration_iso8601` (easy, one-line), `flatten_nested_list` (easy), `lru_cache_decorator` (medium), `csv_column_sum` (easy, read+fix), `retry_with_backoff` (medium), `merge_intervals` (medium), `topo_sort_cycle_detect` (hard), `template_render_escape` (medium, multi-file), `rate_limiter_token_bucket` (medium), `json_path_get` (medium), `dijkstra_shortest_path` (hard), `event_emitter_once` (medium, multi-file). Each `seed/` contains the module with a bug/stub and `test_*.py` that currently fails; the prompt states the behavior, never names the fix.

- [ ] **Step 1: Write the failing test**

```python
# tests/agentic/test_starter_suite_loads.py
from pathlib import Path

from bench.agentic.tasks_io import load_tasks

ROOT = Path("bench/agentic/tasks")


def test_starter_suite_has_at_least_twelve_valid_tasks():
    tasks = [t for t in load_tasks(ROOT) if not t.task_id.startswith("_")]
    assert len(tasks) >= 12
    for t in tasks:
        assert t.prompt and t.meta.timeout_s > 0
        assert (t.seed).is_dir()
```

- [ ] **Step 2: Run to verify it fails.** `assert len >= 12` fails (only fixtures exist).
- [ ] **Step 3: Author the 12 task dirs and README.** For each: write `seed/<module>.py` (with the bug) + `seed/test_<module>.py` (failing), `prompt.txt`, `verify.sh` (`#!/bin/sh\npython -m pytest -q\n`), `meta.json`. Verify each task's seed tests genuinely fail before a fix: `for d in bench/agentic/tasks/*/; do echo "$d"; (cd "$d/seed" && python -m pytest -q || true); done`.
- [ ] **Step 4: Run to verify it passes.** Expected: 1 passed.
- [ ] **Step 5: Commit**

```bash
git add bench/agentic/tasks bench/agentic/README.md tests/agentic/test_starter_suite_loads.py
git commit -m "feat(bench/agentic): 12-task starter suite + README (honesty framing)"
```

---

### Task 12: Full suite green run + plan/spec status update

**Files:**
- Modify: `docs/superpowers/specs/2026-06-20-agentic-coding-eval-harness-design.md` (status line)
- (no new code)

- [ ] **Step 1:** Run the full harness test suite: `.venv/bin/python -m pytest tests/agentic/ -v`. Expected: all green (SP-0 success criteria #3).
- [ ] **Step 2:** Run the **token-free** end-to-end with the stub against the real 12-task suite to prove loading + scoreboard generation at scale:
  `.venv/bin/python -c "from pathlib import Path; from bench.agentic.runner import run_suite; from bench.agentic.agents.stub import StubRunner; print(run_suite('bench/agentic/tasks', [StubRunner(name='crowe-logic'), StubRunner(name='reference')], 'bench/agentic/results-smoke', 'stub'))"`
  Expected: `bench/agentic/results-smoke/scoreboard.md` exists with a 12-row per-task matrix.
- [ ] **Step 3:** Update the spec status from "Approved design; ready for implementation plan" to "Implemented (SP-0); baseline run pending creds + cost approval."
- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-06-20-agentic-coding-eval-harness-design.md
git commit -m "docs: SP-0 harness implemented; mark spec status"
```

---

## Self-Review

**Spec coverage:** §3 architecture → Tasks 1–11 (every file in the spec's tree is created; `__init__.py`, `agents/base.py`, `crowe_logic.py`, `reference.py`, `sandbox.py`, `runner.py`, `score.py`, `report.py`, `README.md`, `tasks/`). §4 task format → T3 + T11. §5 scoring/two baselines → T6 (`aggregate`, `self_verified_rate`), T7 (gap row), T10 (reference on same model). §6 error handling → T8 (crash isolation test), T4 (verify timeout), T9 (subprocess timeout), append-only JSONL in T6. §8 testing the harness → fixtures (T8), unit tests every task, stub runner (T5). §10 success criteria → T8 (#1–#3 token-free), T12 (#3 full green + #4 scoreboard).

**Deviations from spec, called out:** (a) `score.py` is named per spec; the spec also lists `score.py`+`report.py` separately — both present. (b) The reference loop uses the `anthropic` client directly rather than `stream_response`, because `stream_response` *is* the crowe-logic loop under test — reusing it would collapse the independent variable. This is the correct reading of the spec's isolation intent and is documented in T10. (c) The real `claude-opus-4-8` model id and Anthropic creds resolution (`ANTHROPIC_API_KEY`/`AZURE_ANTHROPIC_API_KEY`) must be confirmed against the foundry's actual provider config at build time — flagged in T9/T10 notes.

**Placeholder scan:** none — every code step has complete code; the 12 starter tasks are enumerated by id/difficulty with an exact authoring recipe (T11 keeps each task's code out of the plan deliberately, since authoring 12 buggy modules inline would bloat the plan; the recipe + the validation command make them unambiguous).

**Type consistency:** `AgentResult` fields used identically across T1/T5/T8/T9/T10; `VerifyResult` across T4/T6/T8; `make_row`/`aggregate`/`render_scoreboard` signatures consistent T6→T7→T8.

**Known build-time integration points (not plan gaps, runtime couplings to confirm):**
1. Autonomy `execute` mechanism for headless (T9 note).
2. Anthropic creds + exact Opus model id from foundry config (T9/T10).
3. The real-model baseline run itself spends tokens → out of SP-0's token-free scope; it is the post-plan "next step" gated on cost approval (spec §10 #4).
