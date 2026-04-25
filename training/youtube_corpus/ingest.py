# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio | proprietary, private repository.

"""
YouTube corpus ingest | pulls a channel's videos into the training corpus
directory using yt-dlp. Designed around the CroweLM Vision-Clip fine-tune
so downstream stages (frames, shots, labels) can assume a stable on-disk
layout:

    <TRAINING_CORPUS_DIR>/<corpus_name>/
        videos/<video_id>.<ext>
        videos/<video_id>.info.json
        manifests/<run_id>.json
        archive.txt                       # yt-dlp download archive

Idempotent by design. Re-running is cheap: yt-dlp's archive file tracks
every already-downloaded id, so only new uploads are fetched. A run
manifest is written each invocation so later training stages can pin
themselves to a specific corpus slice.

Shells out to yt-dlp rather than importing it to keep the dependency
surface small and to match the subprocess-based style of tools/capture.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# ────────────────────────────────────────────────────────────────────────
# Config

DEFAULT_CONFIG_PATH = Path(
    os.environ.get(
        "STUDIO_TRAINING_CONFIG",
        "/Users/crowelogic/Projects/crowe-logic-foundry/config/studio_training.yaml",
    )
)

# Resolution presets keyed by config value. 1080p is the project default:
# strong enough for Vision-Clip labels, ~43 GB for the 237-video SW
# Mushrooms channel, fits the Mac's internal disk without external storage.
FORMAT_PRESETS = {
    "720p": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]",
    "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
    "best": "bestvideo+bestaudio/best",
}


@dataclass
class CorpusSpec:
    name: str
    source_type: str
    source: str
    target_dir: Path
    max_videos: int | None = None
    resolution: str = "1080p"
    notes: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def videos_dir(self) -> Path:
        return self.target_dir / "videos"

    @property
    def manifests_dir(self) -> Path:
        return self.target_dir / "manifests"

    @property
    def archive_file(self) -> Path:
        return self.target_dir / "archive.txt"


def _expand_env(value: str) -> str:
    """Substitute ${VAR} references using current environment. Leaves
    unresolved variables as-is so config errors surface loudly."""
    return os.path.expandvars(value) if isinstance(value, str) else value


def load_corpus(name: str, config_path: Path = DEFAULT_CONFIG_PATH) -> CorpusSpec:
    cfg = yaml.safe_load(config_path.read_text())
    for entry in cfg.get("corpora", []):
        if entry.get("name") != name:
            continue
        target_dir = Path(_expand_env(entry["target_dir"])).expanduser()
        return CorpusSpec(
            name=entry["name"],
            source_type=entry["source_type"],
            source=_expand_env(entry["source"]),
            target_dir=target_dir,
            max_videos=entry.get("max_videos"),
            resolution=entry.get("resolution", "1080p"),
            notes=entry.get("notes", "").strip(),
            extra=entry,
        )
    raise KeyError(f"corpus {name!r} not found in {config_path}")


# ────────────────────────────────────────────────────────────────────────
# yt-dlp operations

def _ytdlp() -> list[str]:
    """Invoke yt-dlp via the venv's python so we always match the version
    installed for this project, not whatever the system PATH resolves."""
    return [sys.executable, "-m", "yt_dlp"]


def list_channel(source: str, limit: int | None = None) -> list[dict]:
    """Return a flat list of {id, title, duration, upload_date} dicts for
    every video on the channel. No download. Uses --flat-playlist so the
    listing is cheap (metadata only)."""
    args = _ytdlp() + [
        "--flat-playlist",
        "--no-warnings",
        "--skip-download",
        "--dump-json",
        source,
    ]
    if limit:
        args += ["--playlist-end", str(limit)]
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp list failed: {proc.stderr.strip()}")
    videos = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        videos.append({
            "id": d.get("id"),
            "title": d.get("title"),
            "duration": d.get("duration"),
            "url": d.get("url") or d.get("webpage_url"),
        })
    return videos


def download_corpus(
    corpus: CorpusSpec,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    verbose: bool = True,
) -> dict:
    """Download every not-yet-archived video from the corpus source into
    the corpus's videos/ directory. Writes a run manifest and returns it.
    """
    corpus.videos_dir.mkdir(parents=True, exist_ok=True)
    corpus.manifests_dir.mkdir(parents=True, exist_ok=True)

    fmt = FORMAT_PRESETS.get(corpus.resolution)
    if fmt is None:
        raise ValueError(
            f"unknown resolution {corpus.resolution!r}; "
            f"choose one of {list(FORMAT_PRESETS)}"
        )

    output_template = str(corpus.videos_dir / "%(id)s.%(ext)s")
    archive = str(corpus.archive_file)

    args = _ytdlp() + [
        "--download-archive", archive,
        "--format", fmt,
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--no-write-playlist-metafiles",
        "--output", output_template,
        "--no-warnings",
        "--ignore-errors",
        "--concurrent-fragments", "4",
        "--retries", "5",
        "--fragment-retries", "5",
    ]
    if limit:
        args += ["--playlist-end", str(limit)]
    if dry_run:
        args += ["--skip-download"]
    args += [corpus.source]

    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    started = time.time()

    if verbose:
        print(f"[{run_id}] starting ingest: {corpus.name}")
        print(f"[{run_id}] source:     {corpus.source}")
        print(f"[{run_id}] target:     {corpus.videos_dir}")
        print(f"[{run_id}] resolution: {corpus.resolution}")
        print(f"[{run_id}] dry_run:    {dry_run}")

    proc = subprocess.run(args, check=False)

    elapsed = time.time() - started
    videos_present = sorted(p.name for p in corpus.videos_dir.glob("*.mp4"))
    info_files = sorted(p.name for p in corpus.videos_dir.glob("*.info.json"))
    total_bytes = sum(p.stat().st_size for p in corpus.videos_dir.glob("*.mp4"))

    manifest = {
        "run_id": run_id,
        "corpus": corpus.name,
        "source": corpus.source,
        "source_type": corpus.source_type,
        "resolution": corpus.resolution,
        "dry_run": dry_run,
        "limit": limit,
        "ytdlp_exit_code": proc.returncode,
        "started_at": started,
        "ended_at": time.time(),
        "elapsed_seconds": round(elapsed, 2),
        "videos_on_disk": len(videos_present),
        "info_files": len(info_files),
        "total_bytes": total_bytes,
        "total_gb": round(total_bytes / (1024 ** 3), 2),
    }

    manifest_path = corpus.manifests_dir / f"{run_id}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    if verbose:
        print(f"[{run_id}] wrote manifest: {manifest_path}")
        print(f"[{run_id}] videos on disk: {manifest['videos_on_disk']} "
              f"({manifest['total_gb']} GB)")
    return manifest


# ────────────────────────────────────────────────────────────────────────
# CLI

def _cli() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Ingest a YouTube corpus for Studio training.")
    p.add_argument("corpus", help="Corpus name defined in config/studio_training.yaml")
    p.add_argument("--list", action="store_true",
                   help="List channel videos only, do not download anything.")
    p.add_argument("--dry-run", action="store_true",
                   help="Run yt-dlp with --skip-download to verify format selection.")
    p.add_argument("--limit", type=int, default=None,
                   help="Cap number of videos (useful for smoke tests).")
    p.add_argument("--resolution", choices=list(FORMAT_PRESETS.keys()),
                   default=None, help="Override corpus resolution preset.")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = p.parse_args()

    corpus = load_corpus(args.corpus, args.config)
    if args.resolution:
        corpus.resolution = args.resolution

    if args.list:
        videos = list_channel(corpus.source, limit=args.limit)
        print(f"{len(videos)} videos on {corpus.source}")
        for v in videos[:20]:
            dur = f"{v['duration']}s" if v['duration'] else "?"
            print(f"  {v['id']} | {dur:>6} | {(v['title'] or '')[:80]}")
        if len(videos) > 20:
            print(f"  ... +{len(videos) - 20} more")
        return 0

    manifest = download_corpus(
        corpus, dry_run=args.dry_run, limit=args.limit
    )
    return 0 if manifest["ytdlp_exit_code"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
