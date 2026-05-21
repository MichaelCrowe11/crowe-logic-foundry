# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio | proprietary, private repository.

"""
Frame extraction | sample frames from every ingested video at a configured
rate, write them to disk, and emit a per-video manifest so downstream
labeling stages can iterate deterministically.

On-disk layout (continuing from ingest.py):

    <corpus>/frames/<video_id>/frame_00000.jpg
                                 frame_00001.jpg
                                 ...
    <corpus>/frames/<video_id>/manifest.json

Idempotent: if a video already has a manifest, it's skipped. Delete the
per-video manifest.json to force re-extraction.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .ingest import CorpusSpec, load_corpus


@dataclass
class ExtractResult:
    video_id: str
    frames_written: int
    duration_seconds: float
    elapsed_seconds: float
    skipped: bool = False
    error: str | None = None


# ────────────────────────────────────────────────────────────────────────

def _ffprobe_duration(path: Path) -> float:
    """Return duration in seconds; 0.0 if ffprobe fails."""
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True, text=True, check=False,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def extract_frames_for_video(
    video_path: Path,
    out_dir: Path,
    *,
    fps: float = 1.0,
    jpeg_quality: int = 3,
) -> ExtractResult:
    """Sample <fps> frames per second from a single video. jpeg_quality is
    ffmpeg -q:v (2 = visually lossless, 31 = worst). 3 is a good balance
    for label training: small on disk, no artifacts at a label's scale."""
    video_id = video_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"

    if manifest_path.exists():
        prior = json.loads(manifest_path.read_text())
        return ExtractResult(
            video_id=video_id,
            frames_written=prior.get("frames_written", 0),
            duration_seconds=prior.get("duration_seconds", 0.0),
            elapsed_seconds=0.0,
            skipped=True,
        )

    duration = _ffprobe_duration(video_path)
    started = time.time()

    args = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-y",
        "-i", str(video_path),
        "-vf", f"fps={fps}",
        "-q:v", str(jpeg_quality),
        str(out_dir / "frame_%05d.jpg"),
    ]
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    elapsed = time.time() - started

    if proc.returncode != 0:
        return ExtractResult(
            video_id=video_id,
            frames_written=0,
            duration_seconds=duration,
            elapsed_seconds=elapsed,
            error=proc.stderr.strip()[-400:],
        )

    frames = sorted(out_dir.glob("frame_*.jpg"))
    manifest = {
        "video_id": video_id,
        "video_path": str(video_path),
        "duration_seconds": duration,
        "fps": fps,
        "jpeg_quality": jpeg_quality,
        "frames_written": len(frames),
        "first_frame": frames[0].name if frames else None,
        "last_frame": frames[-1].name if frames else None,
        "extracted_at": time.time(),
        "elapsed_seconds": round(elapsed, 2),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return ExtractResult(
        video_id=video_id,
        frames_written=len(frames),
        duration_seconds=duration,
        elapsed_seconds=elapsed,
    )


def extract_corpus(
    corpus: CorpusSpec,
    *,
    fps: float = 1.0,
    limit: int | None = None,
    verbose: bool = True,
) -> list[ExtractResult]:
    frames_root = corpus.target_dir / "frames"
    frames_root.mkdir(parents=True, exist_ok=True)

    videos = sorted(corpus.videos_dir.glob("*.mp4"))
    if limit:
        videos = videos[:limit]

    results: list[ExtractResult] = []
    for i, video in enumerate(videos, 1):
        out = frames_root / video.stem
        if verbose:
            print(f"[{i}/{len(videos)}] {video.name}", flush=True)
        result = extract_frames_for_video(video, out, fps=fps)
        if verbose:
            if result.skipped:
                print(f"  skipped (manifest exists, {result.frames_written} frames)")
            elif result.error:
                print(f"  error: {result.error[:200]}")
            else:
                print(f"  wrote {result.frames_written} frames in {result.elapsed_seconds:.1f}s")
        results.append(result)
    return results


# ────────────────────────────────────────────────────────────────────────
# CLI

def _cli() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Extract frames from every video in a corpus.")
    p.add_argument("corpus")
    p.add_argument("--fps", type=float, default=1.0)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--jpeg-quality", type=int, default=3)
    args = p.parse_args()

    corpus = load_corpus(args.corpus)
    results = extract_corpus(corpus, fps=args.fps, limit=args.limit)

    total_frames = sum(r.frames_written for r in results)
    errored = [r for r in results if r.error]
    skipped = [r for r in results if r.skipped]

    print()
    print(f"corpus       : {corpus.name}")
    print(f"videos       : {len(results)}")
    print(f"skipped      : {len(skipped)}")
    print(f"errored      : {len(errored)}")
    print(f"frames total : {total_frames}")
    return 0 if not errored else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
