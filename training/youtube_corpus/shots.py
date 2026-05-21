# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio | proprietary, private repository.

"""
Shot detection | run PySceneDetect against every ingested video and
write a per-video shot list. Downstream labeling stages operate on shots
(coherent stretches of footage) rather than individual frames, because
the shot_type taxonomy is a per-shot property, not a per-frame one.

On-disk layout (continuing from ingest.py and frames.py):

    <corpus>/shots/<video_id>.json         # list of shot boundaries

Each shot record is:

    {
      "index":         int,
      "start_frame":   int,
      "end_frame":     int,
      "start_seconds": float,
      "end_seconds":   float,
      "duration":      float
    }

Idempotent: if shots/<video_id>.json exists, the video is skipped.
Remove the file to force re-detection.

Uses ContentDetector by default (perceptual color change). For mycology
cultivation footage this is usually the right detector; AdaptiveDetector
may over-split slow-pan facility tours and under-split static talking
heads with subtle background motion. Override with --detector if needed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from scenedetect import (
    AdaptiveDetector,
    ContentDetector,
    SceneManager,
    ThresholdDetector,
    open_video,
)

from .ingest import CorpusSpec, load_corpus


DETECTORS = {
    "content": ContentDetector,
    "adaptive": AdaptiveDetector,
    "threshold": ThresholdDetector,
}


@dataclass
class DetectResult:
    video_id: str
    shots: int
    duration_seconds: float
    elapsed_seconds: float
    skipped: bool = False
    error: str | None = None


def detect_shots_for_video(
    video_path: Path,
    out_path: Path,
    *,
    detector: str = "content",
    threshold: float = 27.0,
    min_scene_len: int = 15,
) -> DetectResult:
    """Run PySceneDetect against a single video.

    threshold:     ContentDetector sensitivity (lower = more splits).
                   27 is the library default; 22 for talking-head,
                   35 for fast-cut creator footage.
    min_scene_len: frames; prevents flash cuts from registering.
    """
    video_id = video_path.stem

    if out_path.exists():
        prior = json.loads(out_path.read_text())
        return DetectResult(
            video_id=video_id,
            shots=len(prior.get("shots", [])),
            duration_seconds=prior.get("duration_seconds", 0.0),
            elapsed_seconds=0.0,
            skipped=True,
        )

    detector_cls = DETECTORS.get(detector)
    if detector_cls is None:
        return DetectResult(
            video_id=video_id,
            shots=0,
            duration_seconds=0.0,
            elapsed_seconds=0.0,
            error=f"unknown detector {detector!r}; choose {list(DETECTORS)}",
        )

    started = time.time()
    try:
        video = open_video(str(video_path))
        mgr = SceneManager()
        if detector == "threshold":
            mgr.add_detector(detector_cls(threshold=threshold))
        else:
            mgr.add_detector(
                detector_cls(threshold=threshold, min_scene_len=min_scene_len)
            )
        mgr.detect_scenes(video=video, show_progress=False)
        scenes = mgr.get_scene_list()
    except Exception as exc:
        return DetectResult(
            video_id=video_id,
            shots=0,
            duration_seconds=0.0,
            elapsed_seconds=time.time() - started,
            error=f"{type(exc).__name__}: {exc}",
        )

    shots = []
    for i, (start, end) in enumerate(scenes):
        shots.append({
            "index": i,
            "start_frame": start.get_frames(),
            "end_frame": end.get_frames(),
            "start_seconds": round(start.get_seconds(), 3),
            "end_seconds": round(end.get_seconds(), 3),
            "duration": round(end.get_seconds() - start.get_seconds(), 3),
        })

    duration = shots[-1]["end_seconds"] if shots else 0.0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "video_id": video_id,
        "video_path": str(video_path),
        "detector": detector,
        "threshold": threshold,
        "min_scene_len": min_scene_len,
        "duration_seconds": duration,
        "shot_count": len(shots),
        "detected_at": time.time(),
        "elapsed_seconds": round(time.time() - started, 2),
        "shots": shots,
    }, indent=2) + "\n")

    return DetectResult(
        video_id=video_id,
        shots=len(shots),
        duration_seconds=duration,
        elapsed_seconds=time.time() - started,
    )


def detect_corpus(
    corpus: CorpusSpec,
    *,
    detector: str = "content",
    threshold: float = 27.0,
    min_scene_len: int = 15,
    limit: int | None = None,
    verbose: bool = True,
) -> list[DetectResult]:
    shots_root = corpus.target_dir / "shots"
    shots_root.mkdir(parents=True, exist_ok=True)

    videos = sorted(corpus.videos_dir.glob("*.mp4"))
    if limit:
        videos = videos[:limit]

    results: list[DetectResult] = []
    for i, video in enumerate(videos, 1):
        out = shots_root / f"{video.stem}.json"
        if verbose:
            print(f"[{i}/{len(videos)}] {video.name}", flush=True)
        result = detect_shots_for_video(
            video, out,
            detector=detector,
            threshold=threshold,
            min_scene_len=min_scene_len,
        )
        if verbose:
            if result.skipped:
                print(f"  skipped ({result.shots} shots on disk)")
            elif result.error:
                print(f"  error: {result.error[:200]}")
            else:
                print(
                    f"  {result.shots} shots  "
                    f"({result.duration_seconds:.0f}s video, "
                    f"{result.elapsed_seconds:.1f}s detect)"
                )
        results.append(result)
    return results


def _cli() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Detect shot boundaries across a corpus.")
    p.add_argument("corpus")
    p.add_argument("--detector", choices=list(DETECTORS.keys()), default="content")
    p.add_argument("--threshold", type=float, default=27.0)
    p.add_argument("--min-scene-len", type=int, default=15)
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()

    corpus = load_corpus(args.corpus)
    results = detect_corpus(
        corpus,
        detector=args.detector,
        threshold=args.threshold,
        min_scene_len=args.min_scene_len,
        limit=args.limit,
    )

    total_shots = sum(r.shots for r in results)
    errored = [r for r in results if r.error]
    skipped = [r for r in results if r.skipped]

    print()
    print(f"corpus       : {corpus.name}")
    print(f"videos       : {len(results)}")
    print(f"skipped      : {len(skipped)}")
    print(f"errored      : {len(errored)}")
    print(f"shots total  : {total_shots}")
    return 0 if not errored else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
