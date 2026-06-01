"""Benchmark harness configuration — the single place to tune a run."""

import os
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
DATASETS_DIR = BENCH_DIR / "datasets"
RESULTS_DIR = BENCH_DIR / "results"

# Smoke-run default: marketable flagship tiers (model `name` from MODEL_CHAIN).
FLAGSHIP_TIERS = [
    "gpt-5.4",
    "gpt-5.4-pro",
    "claude-opus-4-6",
    "Kimi-K2-6",
    "DeepSeek-R1",
]

# Pinned judge for Track B scoring (strongest available; reproducible).
# Overridable via BENCH_JUDGE_TIER when the pinned judge is rate-limited or
# offline — lets an operator re-score against a live tier without editing code.
JUDGE_TIER = os.environ.get("BENCH_JUDGE_TIER", "gpt-5.4-pro").strip() or "gpt-5.4-pro"

# Truncation limit for stored answers (chars).
MAX_STORED_ANSWER_CHARS = 8000
