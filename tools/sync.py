# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio: proprietary, private repository.

"""
Audio waveform sync: align multi-camera clips to the primary camera.

Every camera in a shoot starts recording within a few hundred ms of
each other but never exactly simultaneously. For broadcast-quality
output we cross-correlate each camera's audio waveform against the
primary camera's and compute an offset in milliseconds. The renderer
applies these as `-itsoffset` when cutting per-camera segments.

Algorithm:
  1. Extract mono 16kHz PCM from each clip's audio track (ffmpeg).
  2. Take the first N seconds (default 15s) of each.
  3. scipy.signal.correlate against the primary track.
  4. Peak argmax → lag in samples → lag in milliseconds.
  5. Clip to a sanity range (+/- 3s) so misfires don't blow up renders.

Cameras with no audio (screen capture) get offset 0 — visual sync via
their known shoot start time is close enough for cuts.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np  # noqa: F401  (annotations only; runtime import is lazy in each function)

from tools.capture import CAPTURE_ROOT

# numpy + scipy are imported lazily inside the helpers below. They are
# only needed by Crowe Studio multi-camera sync work, which is gated
# behind sync_shoot()/get_sync_offsets(). Eager import would force every
# Foundry user (including those who never touch Studio) to install both
# heavy deps just to load tools/__init__.py — the bug that previously
# made every chat turn crash with "No module named 'numpy'". Type
# annotations referencing np.ndarray remain valid because
# `from __future__ import annotations` evaluates them lazily as strings.

SHOOTS_DIR = CAPTURE_ROOT / "shoots"
SYNC_SAMPLE_RATE = 16000
SYNC_DEFAULT_WINDOW_SECONDS = 15
SYNC_MAX_OFFSET_SECONDS = 3.0


def _ff() -> str:
    return os.environ.get("FFMPEG_BIN", "/opt/homebrew/bin/ffmpeg")


def _probe_has_audio(path: str) -> bool:
    try:
        r = subprocess.run(
            [
                "/opt/homebrew/bin/ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "default=nw=1:nk=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return "audio" in r.stdout.lower()
    except Exception:
        return False


def _extract_mono_pcm(path: str, seconds: int) -> np.ndarray | None:
    """
    Extract the first N seconds of audio from a clip as a float32 mono
    numpy array at SYNC_SAMPLE_RATE Hz. Returns None if no audio.
    """
    import numpy as np

    if not _probe_has_audio(path):
        return None
    try:
        cmd = [
            _ff(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-t",
            str(seconds),
            "-i",
            path,
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            str(SYNC_SAMPLE_RATE),
            "-",
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        if proc.returncode != 0 or not proc.stdout:
            return None
        return np.frombuffer(proc.stdout, dtype=np.float32)
    except Exception:
        return None


def _offset_ms(primary: np.ndarray, other: np.ndarray) -> tuple[float, float]:
    """
    Cross-correlate two mono waveforms. Returns (offset_ms, confidence).
    Positive ms means 'other' starts that much later than primary
    (so ffmpeg needs -itsoffset +{ms}/1000 on the other track).
    Confidence is the normalized peak value in [0, 1].
    """
    import numpy as np
    from scipy.signal import correlate

    n = min(len(primary), len(other))
    if n < SYNC_SAMPLE_RATE // 2:
        return 0.0, 0.0

    a = primary[:n].astype(np.float32)
    b = other[:n].astype(np.float32)
    # Mean-subtract + normalize so silence doesn't dominate
    a = a - a.mean()
    b = b - b.mean()
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na < 1e-6 or nb < 1e-6:
        return 0.0, 0.0
    a /= na
    b /= nb

    corr = correlate(a, b, mode="full", method="fft")
    lag_samples = int(np.argmax(corr)) - (n - 1)
    confidence = float(np.max(corr))

    offset_seconds = lag_samples / SYNC_SAMPLE_RATE
    if abs(offset_seconds) > SYNC_MAX_OFFSET_SECONDS:
        return 0.0, confidence
    return offset_seconds * 1000.0, confidence


def sync_shoot(shoot_id: str, window_seconds: int = SYNC_DEFAULT_WINDOW_SECONDS) -> str:
    """
    Compute per-camera audio offsets for a stopped shoot and write them
    into the shoot manifest. The EDL renderer reads these.

    :param shoot_id: Stopped shoot_id.
    :param window_seconds: How much audio to analyze (first N seconds).
        15 is enough to resolve sub-100ms drift; longer for complex scenes.
    :return: JSON with {shoot_id, primary_camera, offsets: [{camera,
        offset_ms, confidence, has_audio}], analysis_seconds}.
    :rtype: str
    """
    try:
        t0 = time.time()
        manifest_path = SHOOTS_DIR / f"{shoot_id}.json"
        if not manifest_path.exists():
            return json.dumps({"error": f"Unknown shoot: {shoot_id}"})
        manifest = json.loads(manifest_path.read_text())
        clips = manifest.get("clips") or [
            {
                "camera": c["camera"],
                "role": c["role"],
                "path": c["path"],
                "sync_priority": c.get("sync_priority", "secondary"),
            }
            for c in manifest.get("cameras", [])
        ]
        if not clips:
            return json.dumps({"error": "shoot has no clips"})

        primary = next((c for c in clips if c.get("sync_priority") == "primary"), None)
        if not primary:
            # Fall back to the first clip with audio as primary
            primary = next((c for c in clips if _probe_has_audio(c["path"])), clips[0])
        primary_pcm = _extract_mono_pcm(primary["path"], window_seconds)
        if primary_pcm is None:
            return json.dumps(
                {"error": f"Primary {primary['camera']} has no audio to sync against"}
            )

        offsets = []
        for c in clips:
            if c["camera"] == primary["camera"]:
                offsets.append(
                    {
                        "camera": c["camera"],
                        "offset_ms": 0.0,
                        "confidence": 1.0,
                        "has_audio": True,
                        "is_primary": True,
                    }
                )
                continue
            other_pcm = _extract_mono_pcm(c["path"], window_seconds)
            if other_pcm is None:
                offsets.append(
                    {
                        "camera": c["camera"],
                        "offset_ms": 0.0,
                        "confidence": 0.0,
                        "has_audio": False,
                        "is_primary": False,
                    }
                )
                continue
            ms, conf = _offset_ms(primary_pcm, other_pcm)
            offsets.append(
                {
                    "camera": c["camera"],
                    "offset_ms": round(ms, 2),
                    "confidence": round(conf, 4),
                    "has_audio": True,
                    "is_primary": False,
                }
            )

        manifest["sync"] = {
            "primary_camera": primary["camera"],
            "window_seconds": window_seconds,
            "offsets": offsets,
            "analyzed_at": time.time(),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2))

        return json.dumps(
            {
                "shoot_id": shoot_id,
                "primary_camera": primary["camera"],
                "offsets": offsets,
                "analysis_seconds": round(time.time() - t0, 2),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


def get_sync_offsets(shoot_id: str) -> str:
    """
    Read a shoot's computed sync offsets without recomputing.

    :param shoot_id: Shoot to query.
    :return: JSON with sync block from manifest, or error.
    :rtype: str
    """
    try:
        manifest_path = SHOOTS_DIR / f"{shoot_id}.json"
        if not manifest_path.exists():
            return json.dumps({"error": f"Unknown shoot: {shoot_id}"})
        manifest = json.loads(manifest_path.read_text())
        sync = manifest.get("sync")
        if not sync:
            return json.dumps(
                {"error": "shoot has not been synced yet — call sync_shoot first"}
            )
        return json.dumps(sync)
    except Exception as e:
        return json.dumps({"error": str(e)})
