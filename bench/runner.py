"""Benchmark runner: (question x tier x condition) -> append-only raw.jsonl.

Track A: one run per (question, tier) with tools on.
Track B: two runs per (question, tier) — grounded (tools on) and bare (tools off).
Results are appended to results_dir/raw.jsonl; runs never clobber prior output.
"""

from __future__ import annotations

import json
from pathlib import Path

from bench.headless_client import run_headless


def _write_row(fh, **row):
    fh.write(json.dumps(row) + "\n")
    fh.flush()


def run_track_a(questions, tiers, results_dir: Path) -> Path:
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / "raw.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for q in questions:
            for tier in tiers:
                r = run_headless(q["question"], tier, tools=True)
                _write_row(
                    fh,
                    track="a",
                    condition="default",
                    tier=tier,
                    question_id=q["id"],
                    qtype=q.get("type", ""),
                    expected=q.get("answer", ""),
                    answer=r.answer,
                    tokens=r.tokens,
                    elapsed_ms=r.elapsed_ms,
                    reasoning_tokens=r.reasoning_tokens,
                    error=r.error,
                )
    return results_dir


def run_track_b(questions, tiers, results_dir: Path) -> Path:
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / "raw.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for q in questions:
            for tier in tiers:
                for condition, tools in (("grounded", True), ("bare", False)):
                    r = run_headless(q["question"], tier, tools=tools)
                    _write_row(
                        fh,
                        track="b",
                        condition=condition,
                        tier=tier,
                        question_id=q["id"],
                        source_passage=q.get("source_passage", ""),
                        reference_answer=q.get("reference_answer", ""),
                        answer=r.answer,
                        tokens=r.tokens,
                        elapsed_ms=r.elapsed_ms,
                        reasoning_tokens=r.reasoning_tokens,
                        error=r.error,
                    )
    return results_dir
