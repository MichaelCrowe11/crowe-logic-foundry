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
                    tests=q.get("tests", ""),
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
                        question=q.get("question", ""),
                        source_passage=q.get("source_passage", ""),
                        reference_answer=q.get("reference_answer", ""),
                        answer=r.answer,
                        tokens=r.tokens,
                        elapsed_ms=r.elapsed_ms,
                        reasoning_tokens=r.reasoning_tokens,
                        error=r.error,
                    )
    return results_dir


def _all_chat_tiers() -> list[str]:
    """All MODEL_CHAIN tier names except non-chat backends (embeddings/video/router)."""
    from config.agent_config import MODEL_CHAIN

    nonchat = {"Cohere-embed-v4", "text-embedding-3-large", "sora-2", "model-router"}
    return [c["name"] for c in MODEL_CHAIN if c.get("name") not in nonchat]


def resolve_tiers(*, all_tiers: bool, explicit):
    """Explicit tiers win; else all chat tiers if --all; else the flagship smoke set."""
    from bench import config

    if explicit:
        return list(explicit)
    if all_tiers:
        return _all_chat_tiers()
    return config.FLAGSHIP_TIERS


def _load_jsonl(path) -> list[dict]:
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def main() -> int:
    import argparse
    import datetime

    from bench import config
    from bench.report import build_scoreboard
    from bench.scoring import score_results_file

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
    print(
        f"Tiers: {len(tiers)} | est. runs: {runs} "
        f"(Track A: {n_a * len(tiers)}, Track B: {n_b * len(tiers) * 2})."
    )

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = config.RESULTS_DIR / ts

    if args.track in ("a", "both"):
        qa = _load_jsonl(config.DATASETS_DIR / "track_a" / "gsm8k.jsonl")[: args.limit]
        qa += _load_jsonl(config.DATASETS_DIR / "track_a" / "mmlu.jsonl")[: args.limit]
        # humaneval.jsonl is committed but intentionally not dispatched here:
        # code (pass@1) scoring is wired separately; include it once score_code lands.
        run_track_a(qa, tiers, out_dir)
    if args.track in ("b", "both"):
        qb = _load_jsonl(config.DATASETS_DIR / "track_b" / "mycology.jsonl")[
            : args.limit
        ]
        run_track_b(qb, tiers, out_dir)

    raw = out_dir / "raw.jsonl"
    if raw.exists():
        scored = out_dir / "scored.jsonl"
        score_results_file(raw, scored)
        (out_dir / "scoreboard.md").write_text(build_scoreboard(scored))
        print(f"scoreboard: {out_dir / 'scoreboard.md'}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
