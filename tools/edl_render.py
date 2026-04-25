# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio — proprietary, private repository.

"""
EDL renderer — turns an Edit Decision List into a final multi-angle cut.

Strategy (keeps it deterministic and ffmpeg-only):

  1. For each EDL section, extract the right time range from the right
     camera, apply zoom preset if requested, re-encode to a common
     profile (1920x1080 yuv420p h264 30fps).
  2. Use the master audio clip (the primary camera's audio) as the
     single continuous audio track, trimmed to total_duration.
  3. Concat the re-encoded video segments with the concat demuxer.
  4. Mux the continuous audio track over the concatenated video.

This keeps lip-sync stable — audio stays continuous from the primary
camera; video switches between angles. The alternative (switching audio
per section) introduces clicks and level jumps.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from tools.capture import CAPTURE_ROOT
from tools.presentation import _zoom_filter as zoom_filter_for

EDL_RENDERS = CAPTURE_ROOT / "renders"
TARGET_W = 1920
TARGET_H = 1080
TARGET_FPS = 30


def _ff() -> str:
    return os.environ.get("FFMPEG_BIN", "/opt/homebrew/bin/ffmpeg")


def _ensure_renders() -> None:
    EDL_RENDERS.mkdir(parents=True, exist_ok=True)


def _render_segment(
    src: str, start: float, end: float, zoom: str, out: str,
    itsoffset_ms: float = 0.0,
) -> tuple[bool, str]:
    duration = max(0.01, end - start)
    vf_zoom = zoom_filter_for(zoom, duration, fps=TARGET_FPS) if zoom and zoom != "none" else None

    # Chain: trim → scale to target → optional zoom filter.
    scale_clause = f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    if vf_zoom:
        filter_graph = f"[0:v]{scale_clause},fps={TARGET_FPS}[pre];[pre]{vf_zoom}[v]"
    else:
        filter_graph = f"[0:v]{scale_clause},fps={TARGET_FPS}[v]"

    # Apply per-camera audio-derived offset. Positive ms means this
    # camera starts LATER than primary, so we shift the seek point
    # FORWARD by that much to re-align.
    adjusted_start = max(0.0, start + (itsoffset_ms / 1000.0))
    adjusted_end = max(adjusted_start + 0.01, end + (itsoffset_ms / 1000.0))

    cmd = [
        _ff(), "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(adjusted_start), "-to", str(adjusted_end),
        "-i", src,
        "-filter_complex", filter_graph,
        "-map", "[v]",
        "-an",
        "-c:v", "h264_videotoolbox", "-b:v", "10M",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        out,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return (proc.returncode == 0 and os.path.exists(out)), proc.stderr.strip()[-400:]


def render_edl(edl_path: str, output_path: str = "") -> str:
    """
    Render a final multi-angle cut from an EDL JSON.

    :param edl_path: Path to an EDL JSON produced by shot_selector.build_edl.
    :param output_path: Final mp4 path. Auto-generated if empty.
    :return: JSON with {output, sections_rendered, total_duration, bytes,
        render_seconds}.
    :rtype: str
    """
    try:
        _ensure_renders()
        t0 = time.time()
        edl_path = str(Path(edl_path).resolve())
        edl = json.loads(Path(edl_path).read_text())
        sections = edl.get("sections", [])
        if not sections:
            return json.dumps({"error": "EDL has no sections"})

        if not output_path:
            output_path = str(EDL_RENDERS / f"{edl['edl_id']}.mp4")
        else:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        work = Path(tempfile.mkdtemp(prefix="edl-", dir=str(EDL_RENDERS)))
        segment_paths: list[str] = []
        failures: list[dict] = []

        # Load shoot sync offsets if present so per-camera lag is corrected
        # during segment cutting.
        offsets_by_camera: dict[str, float] = {}
        shoot_id = edl.get("shoot_id")
        if shoot_id:
            from tools.capture import CAPTURE_ROOT as _CR
            shoot_manifest = _CR / "shoots" / f"{shoot_id}.json"
            if shoot_manifest.exists():
                sm = json.loads(shoot_manifest.read_text())
                for o in (sm.get("sync") or {}).get("offsets", []):
                    offsets_by_camera[o["camera"]] = float(o.get("offset_ms", 0))

        for i, sec in enumerate(sections):
            seg_out = work / f"seg-{i:03d}.mp4"
            offset_ms = offsets_by_camera.get(sec.get("source_camera"), 0.0)
            ok, err = _render_segment(
                src=sec["source_path"],
                start=sec["source_start"],
                end=sec["source_end"],
                zoom=sec.get("zoom", "none"),
                out=str(seg_out),
                itsoffset_ms=offset_ms,
            )
            if ok:
                segment_paths.append(str(seg_out))
            else:
                failures.append({"section": sec.get("title", f"#{i}"), "err": err})

        if not segment_paths:
            return json.dumps({"error": "no segments rendered", "failures": failures})

        # Concat demuxer requires a file list.
        concat_list = work / "segments.txt"
        concat_list.write_text(
            "\n".join(f"file {shlex.quote(p)}" for p in segment_paths) + "\n"
        )

        video_concat = work / "video-concat.mp4"
        proc = subprocess.run([
            _ff(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c", "copy", str(video_concat),
        ], capture_output=True, text=True, timeout=300)
        if proc.returncode != 0 or not video_concat.exists():
            return json.dumps({"error": "video concat failed", "stderr": proc.stderr.strip()[-400:]})

        # Mux master audio at its full length then trim to video duration.
        master_audio = edl["master_audio_path"]
        total = float(edl.get("total_duration", 0)) or sum(s["duration"] for s in sections)

        proc = subprocess.run([
            _ff(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video_concat),
            "-i", master_audio,
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(total),
            "-movflags", "+faststart",
            output_path,
        ], capture_output=True, text=True, timeout=300)
        if proc.returncode != 0 or not os.path.exists(output_path):
            return json.dumps({"error": "final mux failed", "stderr": proc.stderr.strip()[-400:]})

        size = os.path.getsize(output_path)
        render_secs = time.time() - t0
        return json.dumps({
            "output": output_path,
            "edl_id": edl.get("edl_id"),
            "sections_rendered": len(segment_paths),
            "total_duration": total,
            "bytes": size,
            "render_seconds": round(render_secs, 2),
            "failures": failures,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})
