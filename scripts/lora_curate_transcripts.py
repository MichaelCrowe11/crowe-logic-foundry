#!/usr/bin/env python3
"""
Build per-variant LoRA training shards from graded session transcripts.

Reads `eval/transcripts/*.json`, scores each turn against the rubric, and
emits training data:

    - GOOD turns (score < 0.10) become positive examples in `data/training/curated/<variant>/good.jsonl`
    - BAD turns (score > 0.50) become "what not to do" examples paired with
      a corrected output (currently the corrected output must be added by a
      human; this script flags pairs that need correction)

Output format (NeMo SFT shape, compatible with scripts/fine_tune.py):

    {"conversations": [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."}
    ]}

Usage:
    python scripts/lora_curate_transcripts.py --variant eclipse
    python scripts/lora_curate_transcripts.py --all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from eval.replay import load_transcript  # noqa: E402
from eval.rubric import Rubric  # noqa: E402

TRANSCRIPTS_DIR = REPO_ROOT / "eval" / "transcripts"
TRAINING_DIR = REPO_ROOT / "data" / "training" / "curated"

GOOD_THRESHOLD = 0.10
BAD_THRESHOLD = 0.50


def curate_one_variant(variant: str) -> dict[str, Any]:
    """Process all transcripts for a single variant and write training shards."""
    rubric = Rubric()
    out_dir = TRAINING_DIR / variant
    out_dir.mkdir(parents=True, exist_ok=True)
    good_path = out_dir / "good.jsonl"
    bad_path = out_dir / "bad_needs_correction.jsonl"

    good_count = 0
    bad_count = 0
    skipped_count = 0

    with good_path.open("w", encoding="utf-8") as good_fp, bad_path.open(
        "w", encoding="utf-8"
    ) as bad_fp:
        for transcript_path in sorted(TRANSCRIPTS_DIR.glob("*.json")):
            transcript_id, transcript_variant, contexts = load_transcript(transcript_path)
            if transcript_variant != variant:
                continue
            for ctx in contexts:
                results = rubric.run(ctx)
                aggregate = Rubric.aggregate(results)
                if aggregate != aggregate:  # NaN
                    skipped_count += 1
                    continue

                example = {
                    "source_transcript": transcript_id,
                    "turn_index": ctx.turn_index,
                    "score": aggregate,
                    "conversations": [
                        {"role": "user", "content": ctx.user_message},
                        {"role": "assistant", "content": ctx.assistant_output},
                    ],
                }

                if aggregate <= GOOD_THRESHOLD:
                    good_fp.write(json.dumps(example) + "\n")
                    good_count += 1
                elif aggregate >= BAD_THRESHOLD:
                    example["needs_correction"] = True
                    example["failure_modes"] = sorted(
                        mid for mid, r in results.items() if not r.skipped and r.score > 0.5
                    )
                    bad_fp.write(json.dumps(example) + "\n")
                    bad_count += 1
                else:
                    skipped_count += 1

    return {
        "variant": variant,
        "good": good_count,
        "bad_needs_correction": bad_count,
        "skipped_unscored": skipped_count,
        "good_path": str(good_path),
        "bad_path": str(bad_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Curate transcripts into LoRA training shards")
    parser.add_argument("--variant", help="single variant slug")
    parser.add_argument("--all", action="store_true", help="process every variant")
    args = parser.parse_args()

    if not (args.variant or args.all):
        parser.error("specify --variant <slug> or --all")

    variants_to_process: list[str] = []
    if args.variant:
        variants_to_process.append(args.variant)
    else:
        # Discover variants by scanning transcripts
        seen: set[str] = set()
        for path in TRANSCRIPTS_DIR.glob("*.json"):
            try:
                _, variant, _ = load_transcript(path)
                seen.add(variant)
            except Exception:
                continue
        variants_to_process = sorted(seen)

    if not variants_to_process:
        print("No variants discovered in eval/transcripts/")
        return 1

    for variant in variants_to_process:
        result = curate_one_variant(variant)
        print(json.dumps(result, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
