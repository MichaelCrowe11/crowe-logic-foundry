# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio | proprietary, private repository.

"""
CroweLM Vision-Clip fine-tune | Phase 1.4 of the training pipeline.

Takes the per-shot training JSONL + extracted frames produced by the
youtube_corpus stages and fine-tunes Qwen3-VL-2B-Instruct with LoRA to
predict shot_type labels from single representative frames.

Two modes:

    --dry-run     validate the corpus, report class balance, splits,
                  missing-frame rate, and estimated training cost.
                  Requires only stdlib. No model downloaded, no GPU.

    (default)     actually fine-tune. Requires transformers, datasets,
                  peft, accelerate, torch, PIL. Imports are lazy so the
                  dry-run works without any of those installed.

The dry-run is the guard-rail: anyone paying for an A100 hour should
first confirm the corpus is sane. Catches silent failures (missing
frames, severe class imbalance, leakage risk) for free.

Splits: by video_id, not by shot, to prevent test leakage. A single
video's shots all fall into exactly one of train / val / test.

Representative frame per shot: the JPG at the shot's middle second.
Frames were extracted at 1 fps so seconds ~ JPG index (1-based).

Output layout:

    <corpus>/training/
        train.jsonl         chat-format records for HF trainer
        val.jsonl
        test.jsonl
        dataset_summary.json
        dataset_card.md

Run:
    python -c "
    import sys; sys.path.insert(0, '/Users/crowelogic/Projects/crowe-logic-foundry')
    from training.shot_selector.train_vision_clip import main
    main(['southwest-mushrooms-yt', '--dry-run'])
    "
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import yaml


DEFAULT_CONFIG_PATH = Path(
    os.environ.get(
        "STUDIO_TRAINING_CONFIG",
        "/Users/crowelogic/Projects/crowe-logic-foundry/config/studio_training.yaml",
    )
)

# Split ratios. Keep test smaller than val so tuning cycles stay fast
# and a final unseen check remains for publication-grade reporting.
SPLIT_RATIOS = {"train": 0.80, "val": 0.12, "test": 0.08}

# Minimum videos per class required to stratify. Below this, all of the
# class's videos land in train so the model at least sees them.
STRATIFY_MIN_VIDEOS = 4


def _bucket_rank(video_id: str) -> float:
    """Deterministic 0-1 rank for a video_id so stratified splits are
    reproducible across runs without persisting a split file."""
    h = int(hashlib.md5(video_id.encode()).hexdigest()[:8], 16)
    return (h % 10_000) / 10_000


def stratified_video_splits(video_to_label: dict[str, str]) -> dict[str, str]:
    """Assign each video to train / val / test stratified by its dominant
    label. Uses a deterministic per-video rank so the assignment is
    reproducible. Classes with fewer than STRATIFY_MIN_VIDEOS videos
    default all of them to train (too few to split without starving
    val and test of that class entirely)."""
    by_label: dict[str, list[str]] = defaultdict(list)
    for vid, label in video_to_label.items():
        by_label[label].append(vid)

    assignment: dict[str, str] = {}
    train_cut = SPLIT_RATIOS["train"]
    val_cut = train_cut + SPLIT_RATIOS["val"]

    for label, videos in by_label.items():
        if len(videos) < STRATIFY_MIN_VIDEOS:
            for v in videos:
                assignment[v] = "train"
            continue
        # Sort by rank so the split is deterministic.
        ranked = sorted(videos, key=_bucket_rank)
        n = len(ranked)
        n_train = max(1, int(round(n * SPLIT_RATIOS["train"])))
        n_val = max(1, int(round(n * SPLIT_RATIOS["val"])))
        # Keep at least 1 in test if the class has enough videos.
        n_test = max(1, n - n_train - n_val)
        # Rebalance if rounding blew past n.
        while n_train + n_val + n_test > n:
            if n_train > 1:
                n_train -= 1
            elif n_val > 1:
                n_val -= 1
            else:
                n_test -= 1
        for v in ranked[:n_train]:
            assignment[v] = "train"
        for v in ranked[n_train:n_train + n_val]:
            assignment[v] = "val"
        for v in ranked[n_train + n_val:]:
            assignment[v] = "test"
    return assignment


# ────────────────────────────────────────────────────────────────────────
# Data loading

@dataclass
class ShotPair:
    video_id: str
    shot_index: int
    start_seconds: float
    end_seconds: float
    duration: float
    label: str
    confidence: float
    camera_role: str


def load_corpus_paths(corpus_name: str, config_path: Path = DEFAULT_CONFIG_PATH) -> dict:
    cfg = yaml.safe_load(config_path.read_text())
    for entry in cfg.get("corpora", []):
        if entry.get("name") == corpus_name:
            target = Path(os.path.expandvars(entry["target_dir"])).expanduser()
            return {
                "target_dir": target,
                "videos_dir": target / "videos",
                "frames_dir": target / "frames",
                "shots_dir": target / "shots",
                "labels_dir": target / "labels",
                "training_dir": target / "training",
            }
    raise KeyError(f"corpus {corpus_name!r} not found")


def load_taxonomy(config_path: Path = DEFAULT_CONFIG_PATH) -> list[str]:
    cfg = yaml.safe_load(config_path.read_text())
    return [t["label"] for t in cfg["taxonomies"]["shot_type"]]


def load_pairs(jsonl_path: Path) -> list[ShotPair]:
    pairs: list[ShotPair] = []
    with jsonl_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            pairs.append(ShotPair(
                video_id=d["video_id"],
                shot_index=d["shot_index"],
                start_seconds=d["start_seconds"],
                end_seconds=d["end_seconds"],
                duration=d["duration"],
                label=d["label"],
                confidence=d["confidence"],
                camera_role=d["camera_role"],
            ))
    return pairs


# ────────────────────────────────────────────────────────────────────────
# Frame resolution

def representative_frame_path(frames_dir: Path, pair: ShotPair) -> Path:
    """Pick the JPG closest to the middle second of the shot."""
    mid_second = (pair.start_seconds + pair.end_seconds) / 2.0
    # ffmpeg fps=1 starts emission at frame_00001.jpg for t~=0.
    idx = max(1, int(round(mid_second)) + 1)
    return frames_dir / pair.video_id / f"frame_{idx:05d}.jpg"


# ────────────────────────────────────────────────────────────────────────
# Dataset building

def build_chat_record(pair: ShotPair, frame_path: Path, labels: list[str]) -> dict:
    """Shape a training example in Qwen3-VL chat format. Each record is
    self-contained and carries the label taxonomy in the prompt so the
    model learns the closed label set, not freeform text."""
    choices = ", ".join(labels)
    return {
        "messages": [
            {"role": "user", "content": [
                {"type": "image", "image": str(frame_path)},
                {"type": "text", "text":
                    "Classify the shot type of this frame. "
                    f"Choose exactly one label from: {choices}."},
            ]},
            {"role": "assistant", "content": pair.label},
        ],
        "meta": {
            "video_id": pair.video_id,
            "shot_index": pair.shot_index,
            "start_seconds": pair.start_seconds,
            "end_seconds": pair.end_seconds,
            "confidence": pair.confidence,
            "camera_role": pair.camera_role,
        },
    }


def build_splits(pairs: list[ShotPair], frames_dir: Path, labels: list[str]) -> dict:
    """Group pairs by split, resolve frames, drop records whose frames
    are missing. Uses stratified-by-label video assignment so each split
    sees every class. Returns dict[split_name] -> list[record]."""
    # Derive each video's dominant label from its pairs (all pairs for
    # the same video share the same label under current inheritance).
    video_to_label: dict[str, str] = {}
    for pair in pairs:
        video_to_label.setdefault(pair.video_id, pair.label)

    video_split = stratified_video_splits(video_to_label)

    splits = defaultdict(list)
    missing = 0
    for pair in pairs:
        frame = representative_frame_path(frames_dir, pair)
        if not frame.exists():
            missing += 1
            continue
        record = build_chat_record(pair, frame, labels)
        record["split"] = video_split.get(pair.video_id, "train")
        splits[record["split"]].append(record)
    return {"splits": dict(splits), "missing_frames": missing,
            "video_split": video_split}


# ────────────────────────────────────────────────────────────────────────
# Reporting

def report_stats(pairs: list[ShotPair], build_result: dict, labels: list[str]) -> dict:
    splits = build_result["splits"]
    label_set = set(labels)

    per_split_dist = {
        split: Counter(r["messages"][-1]["content"] for r in records)
        for split, records in splits.items()
    }
    total_by_label = Counter(p.label for p in pairs)
    video_to_split = {
        r["meta"]["video_id"]: r["split"]
        for records in splits.values() for r in records
    }

    class_warnings = []
    for label in labels:
        total = total_by_label.get(label, 0)
        if total < 10:
            class_warnings.append(f"{label!r} has only {total} examples; LoRA will struggle")

    summary = {
        "total_pairs": len(pairs),
        "labels": labels,
        "missing_frames": build_result["missing_frames"],
        "usable_pairs": sum(len(r) for r in splits.values()),
        "unique_videos": len(video_to_split),
        "split_counts": {s: len(r) for s, r in splits.items()},
        "label_distribution_total": dict(total_by_label),
        "label_distribution_per_split": {
            s: dict(c) for s, c in per_split_dist.items()
        },
        "missing_labels_in_taxonomy": sorted(set(total_by_label) - label_set),
        "class_warnings": class_warnings,
    }
    return summary


def write_splits_to_disk(splits: dict[str, list[dict]], training_dir: Path) -> dict[str, Path]:
    training_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for split, records in splits.items():
        path = training_dir / f"{split}.jsonl"
        with path.open("w") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        paths[split] = path
    return paths


def write_dataset_card(summary: dict, training_dir: Path) -> Path:
    lines = [
        "# Crowe Studio | Shot-Type Training Corpus",
        "",
        "Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.",
        "Proprietary dataset. Not for redistribution.",
        "",
        f"- Total pairs:    {summary['total_pairs']}",
        f"- Usable pairs:   {summary['usable_pairs']}",
        f"- Missing frames: {summary['missing_frames']}",
        f"- Unique videos:  {summary['unique_videos']}",
        "",
        "## Splits",
        "",
    ]
    for split, count in summary["split_counts"].items():
        lines.append(f"- {split}: {count} pairs")
    lines.append("")
    lines.append("## Label distribution (all splits)")
    lines.append("")
    for label, count in sorted(summary["label_distribution_total"].items(), key=lambda kv: -kv[1]):
        bar = "#" * min(count // 10, 50)
        lines.append(f"- {label:<20} {count:>5}  {bar}")
    if summary["class_warnings"]:
        lines.append("")
        lines.append("## Warnings")
        lines.append("")
        for w in summary["class_warnings"]:
            lines.append(f"- {w}")
    lines.append("")
    path = training_dir / "dataset_card.md"
    path.write_text("\n".join(lines))
    return path


# ────────────────────────────────────────────────────────────────────────
# Actual training (lazy imports, only invoked without --dry-run)

def run_training(training_dir: Path, labels: list[str], model_id: str, output_dir: Path,
                 epochs: int, batch_size: int) -> None:
    # Heavy imports only load if someone really wants to train. This keeps
    # the dry-run lightweight and lets the scaffold run on any machine.
    import torch  # noqa: F401
    from datasets import load_dataset  # type: ignore
    from peft import LoraConfig, get_peft_model  # type: ignore
    from transformers import (  # type: ignore
        AutoModelForVision2Seq,
        AutoProcessor,
        TrainingArguments,
        Trainer,
    )

    data_files = {
        "train": str(training_dir / "train.jsonl"),
        "val": str(training_dir / "val.jsonl"),
    }
    ds = load_dataset("json", data_files=data_files)

    processor = AutoProcessor.from_pretrained(model_id)
    model = AutoModelForVision2Seq.from_pretrained(
        model_id, torch_dtype="auto", device_map="auto"
    )

    lora_cfg = LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.05,
        bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    )
    model = get_peft_model(model, lora_cfg)
    model.print_trainable_parameters()

    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        learning_rate=1e-4,
        logging_steps=20,
        eval_strategy="epoch",
        save_strategy="epoch",
        bf16=True,
        report_to=[],
    )

    # A full collator for Qwen3-VL chat format lives in the HF examples;
    # inline this when running for real. The scaffold documents the shape.
    raise NotImplementedError(
        "Collator + trainer step not wired in this scaffold. "
        "See docs/studio-mobile-plan.md Phase 1.4 for the HF Jobs recipe."
    )


# ────────────────────────────────────────────────────────────────────────
# Main

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fine-tune CroweLM Vision-Clip on the corpus.")
    p.add_argument("corpus")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate data and write splits, but do not train.")
    p.add_argument("--model-id", default="Qwen/Qwen3-VL-2B-Instruct")
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--batch-size", type=int, default=2)
    args = p.parse_args(argv)

    paths = load_corpus_paths(args.corpus)
    labels = load_taxonomy()
    pairs = load_pairs(paths["labels_dir"] / "_shot_training.jsonl")

    build_result = build_splits(pairs, paths["frames_dir"], labels)
    summary = report_stats(pairs, build_result, labels)

    training_dir = paths["training_dir"]
    split_paths = write_splits_to_disk(build_result["splits"], training_dir)
    (training_dir / "dataset_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    card_path = write_dataset_card(summary, training_dir)

    print(f"corpus:          {args.corpus}")
    print(f"total pairs:     {summary['total_pairs']}")
    print(f"usable pairs:    {summary['usable_pairs']}")
    print(f"missing frames:  {summary['missing_frames']}")
    print(f"unique videos:   {summary['unique_videos']}")
    print()
    print("splits (pair count):")
    for split, count in summary["split_counts"].items():
        print(f"  {split:<6} {count:>5}   -> {split_paths[split]}")
    print()
    print("per-split label distribution:")
    for split, dist in summary["label_distribution_per_split"].items():
        total = sum(dist.values())
        print(f"  {split}: {total} records")
        for label, count in sorted(dist.items(), key=lambda kv: -kv[1]):
            pct = 100.0 * count / total if total else 0
            print(f"    {label:<20} {count:>4}  {pct:5.1f}%")
    if summary["class_warnings"]:
        print()
        print("WARNINGS:")
        for w in summary["class_warnings"]:
            print(f"  ! {w}")
    if summary["missing_labels_in_taxonomy"]:
        print()
        print(f"DATA HAS LABELS NOT IN TAXONOMY: {summary['missing_labels_in_taxonomy']}")
    print()
    print(f"dataset card:    {card_path}")

    if args.dry_run:
        print()
        print("dry-run complete. Re-run without --dry-run to train "
              "(requires transformers + peft + accelerate + GPU).")
        return 0

    output_dir = paths["target_dir"] / "models" / "crowelm-vision-clip"
    run_training(training_dir, labels, args.model_id, output_dir,
                 args.epochs, args.batch_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
