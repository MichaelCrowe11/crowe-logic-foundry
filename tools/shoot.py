# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio — proprietary, private repository.

"""
Shoot orchestration — multi-camera simultaneous capture.

A "shoot" is a coordinated capture across N cameras declared in
config/studio_cameras.yaml. Each camera runs its own ffmpeg process.
A shoot manifest JSON at $CAPTURE_ROOT/shoots/<shoot_id>.json records
every per-camera session so the whole set can be stopped, routed, and
post-processed atomically.

Also exposes cloud_ingest_url and cloud_dispatch stubs so remote
cameras (second/third iPhones, Fly.io/Railway compute containers) can
declare themselves as shoot participants over HTTP.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

import yaml

from tools.capture import (
    _ensure_dirs, _ffmpeg_path, CAPTURE_ROOT, OUTPUT_DIR, SESSIONS_DIR,
    list_capture_devices,
)

SHOOTS_DIR = CAPTURE_ROOT / "shoots"
CAMERAS_PATH = Path(os.environ.get(
    "STUDIO_CAMERAS_PATH",
    str(Path(__file__).resolve().parent.parent / "config" / "studio_cameras.yaml"),
))


def _ensure_shoots() -> None:
    SHOOTS_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_dirs()


def _load_cameras() -> list[dict]:
    if not CAMERAS_PATH.exists():
        return []
    with CAMERAS_PATH.open() as f:
        return (yaml.safe_load(f) or {}).get("cameras", [])


def _resolve_avfoundation_device(source: str) -> Optional[str]:
    """
    Resolve a camera registry 'source' string to an AVFoundation device
    string like '1:0'. Accepts:
      - "iphone" → first non-Desk-View iPhone camera + iPhone mic
      - any substring of a device name ("MacBook Air Camera", "Capture screen")

    Pairs video with its matching audio when the source brand matches
    both (e.g. "MacBook Air Camera" -> "MacBook Air Microphone"). This
    is required for waveform sync, which needs audio on every camera.
    Screen capture stays video-only.
    """
    raw = json.loads(list_capture_devices())
    if "error" in raw:
        return None
    video = raw.get("video", [])
    audio = raw.get("audio", [])

    if source.lower() == "iphone":
        v = next((d for d in video if d.get("is_iphone") and "desk view" not in d["name"].lower()), None)
        a = next((d for d in audio if d.get("is_iphone")), None)
        if not v:
            return None
        return f"{v['index']}:{a['index']}" if a else f"{v['index']}:"

    vmatch = next((d for d in video if source.lower() in d["name"].lower()), None)
    if not vmatch:
        return None

    # Screen capture and similar have no audio counterpart; keep video-only.
    if "screen" in vmatch["name"].lower() or "desk view" in vmatch["name"].lower():
        return f"{vmatch['index']}:"

    # Heuristic: strip "Camera"/"Microphone"/"Mic" suffix and match the
    # remaining brand prefix. "MacBook Air Camera" -> prefix "MacBook Air"
    # -> matches "MacBook Air Microphone".
    import re as _re
    brand = _re.sub(r"\s*(camera|webcam|cam|microphone|mic)\s*$", "", vmatch["name"], flags=_re.I).strip()
    amatch = None
    if brand:
        amatch = next((d for d in audio if brand.lower() in d["name"].lower()), None)

    return f"{vmatch['index']}:{amatch['index']}" if amatch else f"{vmatch['index']}:"


def list_cameras() -> str:
    """
    Enumerate cameras declared in config/studio_cameras.yaml. For each,
    resolve its current AVFoundation device string so the agent knows
    which are actually plugged in right now.

    :return: JSON array of {name, role, source_type, resolved, default_specs,
        sync_priority, notes, available}.
    :rtype: str
    """
    try:
        cams = _load_cameras()
        out = []
        for c in cams:
            entry = {
                "name": c.get("name"),
                "role": c.get("role"),
                "source_type": c.get("source_type"),
                "source": c.get("source"),
                "default_specs": c.get("default_specs", {}),
                "sync_priority": c.get("sync_priority", "secondary"),
                "notes": (c.get("notes") or "").strip(),
                "resolved": None,
                "available": False,
            }
            if c.get("source_type") == "avfoundation":
                resolved = _resolve_avfoundation_device(c.get("source", ""))
                entry["resolved"] = resolved
                entry["available"] = resolved is not None
            elif c.get("source_type") == "file_drop":
                src = c.get("source", "")
                entry["available"] = Path(src).exists() if src else False
            else:
                # ndi/rtsp/cloud: we don't probe them here; the agent assumes
                # the operator knows whether the stream is up. A connectivity
                # probe is a future enhancement.
                entry["available"] = True
            out.append(entry)
        return json.dumps(out)
    except Exception as e:
        return json.dumps({"error": str(e)})


def get_camera(name: str) -> str:
    """
    Fetch a single camera's full config + resolved device string.

    :param name: Camera name from the registry.
    :return: JSON with the camera config or error.
    :rtype: str
    """
    try:
        cams = _load_cameras()
        c = next((x for x in cams if x.get("name") == name), None)
        if not c:
            return json.dumps({"error": f"Unknown camera: {name}"})
        result = dict(c)
        if c.get("source_type") == "avfoundation":
            result["resolved"] = _resolve_avfoundation_device(c.get("source", ""))
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _spawn_avfoundation_recorder(
    device_string: str,
    output_path: str,
    width: int, height: int, framerate: int,
    video_bitrate: str,
    log_path: str,
) -> subprocess.Popen:
    # Pin uyvy422 + generous probesize so AVFoundation's frame-rate
    # negotiation always settles. Without this, ffmpeg occasionally
    # fails with "not enough frames to estimate rate" and produces
    # a zero-byte output.
    has_audio = ":" in device_string and not device_string.endswith(":")
    cmd = [
        _ffmpeg_path(), "-hide_banner", "-loglevel", "warning",
        "-probesize", "10M", "-analyzeduration", "2M",
        "-f", "avfoundation",
        "-pixel_format", "uyvy422",
        "-framerate", str(framerate),
        "-video_size", f"{width}x{height}",
        "-i", device_string,
        "-c:v", "h264_videotoolbox", "-b:v", video_bitrate,
    ]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += [
        "-movflags", "+faststart",
        "-y", output_path,
    ]
    log_fh = open(log_path, "wb")
    return subprocess.Popen(
        cmd, stdout=log_fh, stderr=log_fh,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )


def start_shoot(
    shoot_id: str = "",
    cameras: str = "",
    framerate_override: int = 0,
) -> str:
    """
    Start a multi-camera shoot. Every camera runs its own ffmpeg process
    in parallel; the shoot manifest tracks them as one logical unit.

    :param shoot_id: Name for the shoot. Auto-generated if empty.
    :param cameras: Comma-separated list of camera names from the registry.
        If empty, uses every available avfoundation camera.
    :param framerate_override: If non-zero, overrides all cameras'
        default framerate (useful for forcing 60fps rig).
    :return: JSON with {shoot_id, shoot_dir, manifest_path, cameras: [...],
        started: N, failed: N}.
    :rtype: str
    """
    try:
        _ensure_shoots()
        sid = shoot_id or time.strftime("shoot-%Y%m%d-%H%M%S")
        shoot_dir = OUTPUT_DIR / sid
        shoot_dir.mkdir(parents=True, exist_ok=True)

        all_cams = _load_cameras()
        if cameras:
            wanted = {c.strip() for c in cameras.split(",") if c.strip()}
            cam_list = [c for c in all_cams if c.get("name") in wanted]
        else:
            cam_list = [c for c in all_cams if c.get("source_type") == "avfoundation"]

        started: list[dict] = []
        failed: list[dict] = []

        for c in cam_list:
            name = c["name"]
            specs = dict(c.get("default_specs", {}))
            w = int(specs.get("width", 1920))
            h = int(specs.get("height", 1080))
            fr = int(framerate_override or specs.get("framerate", 30))
            br = specs.get("video_bitrate", "8M")

            out_path = str(shoot_dir / f"{name}.mp4")
            log_path = str(SESSIONS_DIR / f"{sid}__{name}.log")

            if c.get("source_type") != "avfoundation":
                failed.append({
                    "camera": name,
                    "reason": f"source_type {c.get('source_type')} not yet supported in start_shoot (use its own ingest path)",
                })
                continue

            dev = _resolve_avfoundation_device(c.get("source", ""))
            if not dev:
                failed.append({"camera": name, "reason": "device not available"})
                continue

            try:
                proc = _spawn_avfoundation_recorder(
                    device_string=dev, output_path=out_path,
                    width=w, height=h, framerate=fr, video_bitrate=br,
                    log_path=log_path,
                )
                time.sleep(0.3)
                if proc.poll() is not None:
                    failed.append({
                        "camera": name, "reason": "ffmpeg exited immediately",
                        "log": Path(log_path).read_text()[-400:] if Path(log_path).exists() else "",
                    })
                    continue
                started.append({
                    "camera": name,
                    "role": c.get("role"),
                    "device": dev,
                    "pid": proc.pid,
                    "path": out_path,
                    "log": log_path,
                    "specs": {"width": w, "height": h, "framerate": fr, "video_bitrate": br},
                    "sync_priority": c.get("sync_priority", "secondary"),
                })
            except Exception as e:
                failed.append({"camera": name, "reason": str(e)})

        manifest = {
            "shoot_id": sid,
            "shoot_dir": str(shoot_dir),
            "started_at": time.time(),
            "cameras": started,
            "failed": failed,
            "status": "recording" if started else "failed",
            "cloud_ingest_url": f"http://127.0.0.1:7777/api/shoot/{sid}/cloud-join",
        }
        manifest_path = SHOOTS_DIR / f"{sid}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))

        return json.dumps({
            "shoot_id": sid,
            "shoot_dir": str(shoot_dir),
            "manifest_path": str(manifest_path),
            "started": len(started),
            "failed": len(failed),
            "cameras": [s["camera"] for s in started],
            "failures": failed,
            "status": manifest["status"],
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def stop_shoot(shoot_id: str) -> str:
    """
    Stop every per-camera recording in a shoot. SIGINT to each ffmpeg so
    moov atoms finalize cleanly. Updates the manifest with final byte
    counts and durations.

    :param shoot_id: The shoot_id returned by start_shoot.
    :return: JSON with {shoot_id, clips: [{camera, path, bytes, duration}]}.
    :rtype: str
    """
    try:
        manifest_path = SHOOTS_DIR / f"{shoot_id}.json"
        if not manifest_path.exists():
            return json.dumps({"error": f"No such shoot: {shoot_id}"})
        manifest = json.loads(manifest_path.read_text())

        results = []
        for cam in manifest.get("cameras", []):
            pid = cam.get("pid")
            try:
                os.kill(pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            # wait up to 5s for graceful exit
            for _ in range(50):
                time.sleep(0.1)
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
            else:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

            path = cam["path"]
            size = os.path.getsize(path) if os.path.exists(path) else 0
            results.append({
                "camera": cam["camera"],
                "role": cam["role"],
                "path": path,
                "bytes": size,
                "sync_priority": cam.get("sync_priority", "secondary"),
            })

        manifest["stopped_at"] = time.time()
        manifest["duration_observed"] = round(
            manifest["stopped_at"] - manifest.get("started_at", manifest["stopped_at"]), 2,
        )
        manifest["clips"] = results
        manifest["status"] = "stopped"
        manifest_path.write_text(json.dumps(manifest, indent=2))

        return json.dumps({
            "shoot_id": shoot_id,
            "duration_observed": manifest["duration_observed"],
            "clips": results,
            "manifest_path": str(manifest_path),
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def list_shoots(limit: int = 20) -> str:
    """
    List recent shoots (most recent first), with active status.

    :param limit: Max shoots to return.
    :return: JSON array of shoot summaries.
    :rtype: str
    """
    try:
        _ensure_shoots()
        manifests = sorted(
            SHOOTS_DIR.glob("*.json"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )[:limit]
        out = []
        for m in manifests:
            try:
                data = json.loads(m.read_text())
            except Exception:
                continue
            alive = 0
            for cam in data.get("cameras", []):
                try:
                    os.kill(cam.get("pid", -1), 0)
                    alive += 1
                except ProcessLookupError:
                    pass
            out.append({
                "shoot_id": data.get("shoot_id"),
                "status": data.get("status"),
                "started_at": data.get("started_at"),
                "duration_observed": data.get("duration_observed"),
                "cameras": [c.get("camera") for c in data.get("cameras", [])],
                "alive_cameras": alive,
                "failed": len(data.get("failed", [])),
                "manifest_path": str(m),
                "shoot_dir": data.get("shoot_dir"),
            })
        return json.dumps(out)
    except Exception as e:
        return json.dumps({"error": str(e)})


def register_cloud_camera(
    shoot_id: str,
    camera_name: str,
    role: str,
    uplink_url: str,
) -> str:
    """
    Register a remote (cloud or network) camera as a participant in an
    active shoot. Stub for the eventual multi-phone / Fly.io container
    workflow: the cloud camera POSTs its clips to the returned URL and
    they attach to the shoot manifest.

    :param shoot_id: Existing shoot_id.
    :param camera_name: Identifier for the remote camera.
    :param role: Role tag ("wide", "close", etc).
    :param uplink_url: Where the remote sends its footage.
    :return: JSON ack with {shoot_id, camera, receiver_path}.
    :rtype: str
    """
    try:
        manifest_path = SHOOTS_DIR / f"{shoot_id}.json"
        if not manifest_path.exists():
            return json.dumps({"error": f"No such shoot: {shoot_id}"})
        manifest = json.loads(manifest_path.read_text())
        shoot_dir = Path(manifest["shoot_dir"])
        receiver = shoot_dir / f"cloud__{camera_name}.mp4"

        cloud = {
            "camera": camera_name,
            "role": role,
            "source_type": "cloud",
            "uplink_url": uplink_url,
            "receiver_path": str(receiver),
            "registered_at": time.time(),
        }
        manifest.setdefault("cloud_cameras", []).append(cloud)
        manifest_path.write_text(json.dumps(manifest, indent=2))
        return json.dumps({
            "shoot_id": shoot_id, "camera": camera_name,
            "receiver_path": str(receiver), "status": "registered",
        })
    except Exception as e:
        return json.dumps({"error": str(e)})
