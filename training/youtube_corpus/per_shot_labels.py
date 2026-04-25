# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio | proprietary, private repository.

"""
Per-shot label assignment | Phase 1.3b of the training pipeline.

Takes the video-level weak labels produced by label_shot_type.py and the
shot boundaries produced by shots.py, and emits a per-shot training JSONL
ready for Phase 1.4 fine-tuning.

Strategy: naive inheritance. Each shot inherits its parent video's
dominant label, with confidence discounted for "shot-level uncertainty"
(we know the video as a whole is labeled X, but we do not yet know if
this specific shot shows X). Filters out flash cuts below a configurable
duration floor so the model does not train on 1-frame inserts.

Output layout:

    <corpus>/labels/_shot_training.jsonl       one record per shot
    <corpus>/labels/_shot_training_summary.json

Each JSONL record:
    {
      "video_id":       str,
      "shot_index":     int,
      "start_seconds":  float,
      "end_seconds":    float,
      "duration":       float,
      "start_frame":    int,
      "end_frame":      int,
      "label":          str,
      "confidence":     float,
      "camera_role":    str,
      "source":         "video_rules_inherited",
      "video_confidence": float
    }

Later phases can refine by:
  - upgrading selected UNLABELED videos via vision
  - overriding shot labels using per-shot vision inference
  - using shoot-history EDL overrides to re-weight
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

from .ingest import DEFAULT_CONFIG_PATH, load_corpus


# Shot-level confidence discount vs video-level (model has less certainty
# that a specific moment shows the dominant class). Tunable.
SHOT_CONFIDENCE_FACTOR = 0.8

# Shots shorter than this are treated as flash cuts and excluded from
# training output, not deleted from the shots/ manifests.
MIN_SHOT_DURATION_SECONDS = 2.0


@dataclass
class ShotPair:
    video_id: str
    shot_index: int
    start_seconds: float
    end_seconds: float
    duration: float
    start_frame: int
    end_frame: int
    label: str
    confidence: float
    camera_role: str
    video_confidence: float


def load_camera_role_map(config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, str]:
    """label -> camera_role_default, from taxonomies.shot_type."""
    cfg = yaml.safe_load(config_path.read_text())
    mapping: dict[str, str] = {}
    for entry in cfg.get("taxonomies", {}).get("shot_type", []):
        mapping[entry["label"]] = entry.get("camera_role_default", "close")
    return mapping


def build_pairs(corpus, *, min_shot_seconds: float = MIN_SHOT_DURATION_SECONDS) -> tuple[list[ShotPair], dict]:
    labels_dir = corpus.target_dir / "labels"
    shots_dir = corpus.target_dir / "shots"

    camera_roles = load_camera_role_map()
    pairs: list[ShotPair] = []

    videos_seen = 0
    videos_with_shots = 0
    videos_with_label = 0
    videos_usable = 0
    shots_total = 0
    shots_kept = 0
    shots_filtered_flash = 0

    for label_path in sorted(labels_dir.glob("*.json")):
        if label_path.name.startswith("_"):
            continue
        videos_seen += 1
        label_record = json.loads(label_path.read_text())
        video_id = label_record["video_id"]
        dominant = label_record.get("dominant_label")
        if not dominant:
            continue
        videos_with_label += 1

        shots_path = shots_dir / f"{video_id}.json"
        if not shots_path.exists():
            continue
        videos_with_shots += 1

        shots_record = json.loads(shots_path.read_text())
        shots = shots_record.get("shots", [])
        shots_total += len(shots)
        if not shots:
            continue

        video_conf = label_record["candidates"][0]["confidence"]
        shot_conf = round(video_conf * SHOT_CONFIDENCE_FACTOR, 2)
        camera_role = camera_roles.get(dominant, "close")

        kept_for_video = 0
        for shot in shots:
            if shot["duration"] < min_shot_seconds:
                shots_filtered_flash += 1
                continue
            pairs.append(ShotPair(
                video_id=video_id,
                shot_index=shot["index"],
                start_seconds=shot["start_seconds"],
                end_seconds=shot["end_seconds"],
                duration=shot["duration"],
                start_frame=shot["start_frame"],
                end_frame=shot["end_frame"],
                label=dominant,
                confidence=shot_conf,
                camera_role=camera_role,
                video_confidence=video_conf,
            ))
            kept_for_video += 1
        if kept_for_video:
            videos_usable += 1
        shots_kept += kept_for_video

    dist = Counter(p.label for p in pairs)
    summary = {
        "built_at": time.time(),
        "min_shot_duration_seconds": min_shot_seconds,
        "shot_confidence_factor": SHOT_CONFIDENCE_FACTOR,
        "videos_seen": videos_seen,
        "videos_with_label": videos_with_label,
        "videos_with_shots": videos_with_shots,
        "videos_usable": videos_usable,
        "shots_total": shots_total,
        "shots_kept": shots_kept,
        "shots_filtered_as_flash": shots_filtered_flash,
        "distribution": dict(dist.most_common()),
    }
    return pairs, summary


def write_pairs(corpus, pairs: list[ShotPair], summary: dict) -> tuple[Path, Path]:
    labels_dir = corpus.target_dir / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = labels_dir / "_shot_training.jsonl"
    summary_path = labels_dir / "_shot_training_summary.json"

    with jsonl_path.open("w") as fh:
        for p in pairs:
            fh.write(json.dumps({
                "video_id": p.video_id,
                "shot_index": p.shot_index,
                "start_seconds": p.start_seconds,
                "end_seconds": p.end_seconds,
                "duration": p.duration,
                "start_frame": p.start_frame,
                "end_frame": p.end_frame,
                "label": p.label,
                "confidence": p.confidence,
                "camera_role": p.camera_role,
                "source": "video_rules_inherited",
                "video_confidence": p.video_confidence,
            }) + "\n")

    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    return jsonl_path, summary_path


def _cli() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Assign per-shot labels via video-level inheritance.")
    p.add_argument("corpus")
    p.add_argument("--min-shot-seconds", type=float, default=MIN_SHOT_DURATION_SECONDS)
    args = p.parse_args()

    corpus = load_corpus(args.corpus)
    pairs, summary = build_pairs(corpus, min_shot_seconds=args.min_shot_seconds)
    jsonl_path, summary_path = write_pairs(corpus, pairs, summary)

    print(f"videos seen:            {summary['videos_seen']}")
    print(f"videos with label:      {summary['videos_with_label']}")
    print(f"videos with shots:      {summary['videos_with_shots']}")
    print(f"videos usable:          {summary['videos_usable']}")
    print(f"shots total:            {summary['shots_total']}")
    print(f"shots filtered (<{summary['min_shot_duration_seconds']}s): {summary['shots_filtered_as_flash']}")
    print(f"shots kept for training: {summary['shots_kept']}")
    print()
    print("distribution:")
    for label, count in sorted(summary["distribution"].items(), key=lambda kv: -kv[1]):
        bar = "#" * min(count, 60)
        print(f"  {label:<20} {count:>5}  {bar}")
    print()
    print(f"training pairs: {jsonl_path}")
    print(f"summary:        {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
