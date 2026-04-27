#!/usr/bin/env python3
"""
Phase 1 receive-side verification for the CroweLM LoRA tuning corpus.

Reads the local crowe_unified_train.jsonl + crowe_unified_val.jsonl,
counts lines, schema-checks the first N rows, compares against the
expected counts in unified_training/UNIFIED_MANIFEST.json, and prints
a one-page readiness report.

Usage:
    .venv/bin/python scripts/lora_phase1_verify_local.py \\
        --root /Users/crowelogic/crowelm-unified-dataset

Exit codes:
    0  ready for upload to COS
    1  files missing
    2  count or schema mismatch
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterator

EXPECTED_FIELDS_INPUT = ("instruction", "input", "prompt", "question")
EXPECTED_FIELDS_OUTPUT = ("response", "output", "answer", "completion")
SAMPLE_ROWS = 50


def iter_lines(path: Path) -> Iterator[str]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield line


def count_and_sample(path: Path, sample_n: int) -> tuple[int, list[dict], int]:
    """Return (line_count, sampled_rows, malformed_count)."""
    count = 0
    malformed = 0
    samples: list[dict] = []
    for line in iter_lines(path):
        count += 1
        if count <= sample_n:
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError:
                malformed += 1
    return count, samples, malformed


def detect_schema(samples: list[dict]) -> tuple[str | None, str | None, int]:
    """Identify which input/output keys are used and how many rows match."""
    input_key = output_key = None
    matched = 0
    for row in samples:
        for ik in EXPECTED_FIELDS_INPUT:
            if ik in row and isinstance(row[ik], str) and row[ik].strip():
                input_key = input_key or ik
                break
        for ok in EXPECTED_FIELDS_OUTPUT:
            if ok in row and isinstance(row[ok], str) and row[ok].strip():
                output_key = output_key or ok
                break
        if input_key and output_key and input_key in row and output_key in row:
            matched += 1
    return input_key, output_key, matched


def fmt_size(n_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TB"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/Users/crowelogic/crowelm-unified-dataset",
                    help="Path to the crowelm-unified-dataset directory.")
    ap.add_argument("--strict", action="store_true",
                    help="Fail if line counts deviate from the manifest by more than 1%%.")
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    train_path = root / "unified_training" / "crowe_unified_train.jsonl"
    val_path = root / "unified_training" / "crowe_unified_val.jsonl"
    manifest_path = root / "unified_training" / "UNIFIED_MANIFEST.json"

    print("=" * 72)
    print("CroweLM LoRA Phase 1 Verify")
    print("=" * 72)
    print(f"root: {root}")
    print()

    if not manifest_path.exists():
        print(f"FAIL: manifest not found at {manifest_path}")
        return 1
    manifest = json.loads(manifest_path.read_text())

    expected_train = manifest.get("train_samples")
    expected_val = manifest.get("validation_samples")
    expected_total = manifest.get("total_samples")
    print("Manifest expectations:")
    print(f"  train_samples      = {expected_train:,}")
    print(f"  validation_samples = {expected_val:,}")
    print(f"  total_samples      = {expected_total:,}")
    print()

    missing = []
    for label, path in (("train", train_path), ("validation", val_path)):
        if not path.exists():
            missing.append((label, path))
    if missing:
        print("FAIL: required files not on disk yet.")
        for label, path in missing:
            print(f"  - {label}: {path}")
        print()
        print("Transfer the files from the Windows machine to the paths above")
        print("then re-run this script. See the chat for transfer mechanism options.")
        return 1

    print("Files present:")
    print(f"  train      {train_path}  ({fmt_size(train_path.stat().st_size)})")
    print(f"  validation {val_path}  ({fmt_size(val_path.stat().st_size)})")
    print()

    print(f"Counting lines and sampling first {SAMPLE_ROWS} rows of each...")
    train_count, train_samples, train_malformed = count_and_sample(train_path, SAMPLE_ROWS)
    val_count, val_samples, val_malformed = count_and_sample(val_path, SAMPLE_ROWS)

    print(f"  train rows:      {train_count:,}  (manifest expected {expected_train:,})")
    print(f"  validation rows: {val_count:,}  (manifest expected {expected_val:,})")
    if train_malformed or val_malformed:
        print(f"  malformed rows in samples: train={train_malformed}, val={val_malformed}")
    print()

    train_ik, train_ok, train_matched = detect_schema(train_samples)
    val_ik, val_ok, val_matched = detect_schema(val_samples)
    print("Schema detection (first 50 rows of each):")
    print(f"  train       input_key={train_ik!r:20s} output_key={train_ok!r:20s} matched={train_matched}/{len(train_samples)}")
    print(f"  validation  input_key={val_ik!r:20s} output_key={val_ok!r:20s} matched={val_matched}/{len(val_samples)}")
    print()

    issues = []

    if expected_train and abs(train_count - expected_train) / expected_train > 0.01:
        issues.append(f"train count drift > 1%% (got {train_count:,}, expected {expected_train:,})")
    if expected_val and abs(val_count - expected_val) / expected_val > 0.01:
        issues.append(f"validation count drift > 1%% (got {val_count:,}, expected {expected_val:,})")
    if train_malformed > 0 or val_malformed > 0:
        issues.append("malformed JSON rows present in sample")
    if not train_ik or not train_ok:
        issues.append("could not detect input/output keys in train samples")
    if train_matched < SAMPLE_ROWS // 2:
        issues.append(f"too few train rows match an instruction/response shape ({train_matched}/{SAMPLE_ROWS})")

    if issues:
        print("Issues:")
        for issue in issues:
            print(f"  - {issue}")
        print()
        print("VERDICT: not ready for upload. Resolve the issues above and re-run.")
        return 2 if args.strict else 0

    print("VERDICT: ready for upload to COS.")
    print()
    print("Detected schema:")
    print(f"  instruction field -> {train_ik!r}")
    print(f"  response field    -> {train_ok!r}")
    print()
    print("Next: scripts/lora_phase1_upload_to_cos.py (will be built after transfer mechanism is chosen).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
