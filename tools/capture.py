# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio — proprietary, private repository.

"""
Capture tool — live AVFoundation recording for iPhone / webcam / screen.

Wraps ffmpeg to enumerate devices, record bounded clips, start/stop long
sessions, and snap stills. Designed around Continuity Camera so an iPhone
plugged into the Mac is treated as a first-class capture source.

Recording profile defaults match the toxicteetv ingest stage expectations
(1080p30, h264_videotoolbox, AAC 192k) so captures drop straight into
`<project>/automation/pipeline/01_ingest.js` without re-encode.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

CAPTURE_ROOT = Path(os.environ.get("CAPTURE_ROOT", "/tmp/crowe-capture"))
SESSIONS_DIR = CAPTURE_ROOT / "sessions"
OUTPUT_DIR = CAPTURE_ROOT / "out"

# iPhone Continuity Camera supports only exact integer framerates (30 / 60),
# not the 29.97 default ffmpeg picks. See: AVFoundation frame-rate error
# "Selected framerate (29.970030) is not supported".
IPHONE_NATIVE_RATES = {30, 60}

_VIDEO_LINE = re.compile(r"\[(\d+)\]\s+(.*?)\s*$")
_AUDIO_BANNER = re.compile(r"AVFoundation audio devices:")
_VIDEO_BANNER = re.compile(r"AVFoundation video devices:")


def _ensure_dirs() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _ffmpeg_path() -> str:
    return os.environ.get("FFMPEG_BIN", "/opt/homebrew/bin/ffmpeg")


def list_capture_devices() -> str:
    """
    Enumerate AVFoundation video and audio capture devices visible to the Mac.

    Shows iPhone Continuity Camera, MacBook camera, Desk View, screen capture,
    JBL headphones, etc. Each device has an index used by other capture tools.

    :return: JSON with {"video": [{index, name, is_iphone}], "audio": [...]}.
    :rtype: str
    """
    try:
        proc = subprocess.run(
            [_ffmpeg_path(), "-hide_banner", "-f", "avfoundation",
             "-list_devices", "true", "-i", ""],
            capture_output=True, text=True, timeout=10,
        )
        stderr = proc.stderr
        video, audio, section = [], [], None
        for line in stderr.splitlines():
            if _VIDEO_BANNER.search(line):
                section = "video"
                continue
            if _AUDIO_BANNER.search(line):
                section = "audio"
                continue
            m = _VIDEO_LINE.search(line)
            if not m or section is None:
                continue
            idx = int(m.group(1))
            name = m.group(2).strip()
            entry = {"index": idx, "name": name}
            if section == "video":
                entry["is_iphone"] = "iphone" in name.lower()
                video.append(entry)
            else:
                entry["is_iphone"] = "iphone" in name.lower()
                audio.append(entry)
        return json.dumps({"video": video, "audio": audio})
    except Exception as e:
        return json.dumps({"error": str(e)})


def find_iphone_device() -> str:
    """
    Resolve the iPhone video + audio indices in one call. Convenience wrapper
    around list_capture_devices that returns just what start_capture needs.

    :return: JSON with {"video_index", "audio_index", "device_string"} or error.
    :rtype: str
    """
    raw = list_capture_devices()
    data = json.loads(raw)
    if "error" in data:
        return raw
    v = next((d for d in data["video"] if d.get("is_iphone") and "desk view" not in d["name"].lower()), None)
    a = next((d for d in data["audio"] if d.get("is_iphone")), None)
    if not v:
        return json.dumps({"error": "No iPhone video device found. Is Continuity Camera enabled and iPhone unlocked?"})
    return json.dumps({
        "video_index": v["index"],
        "audio_index": a["index"] if a else None,
        "device_string": f"{v['index']}:{a['index']}" if a else f"{v['index']}:",
        "video_name": v["name"],
        "audio_name": a["name"] if a else None,
    })


def capture_clip(
    duration_seconds: int = 10,
    device: str = "iphone",
    output_path: str = "",
    width: int = 1920,
    height: int = 1080,
    framerate: int = 30,
    video_bitrate: str = "8M",
    audio_bitrate: str = "192k",
) -> str:
    """
    Record a bounded capture clip to disk. Blocks until the clip finishes.

    Use for deterministic recordings (B-roll, platform-sized clips, test
    captures). For live/monitored recording use start_live_capture.

    :param duration_seconds: How long to record, in seconds.
    :param device: "iphone" to auto-detect, or "video_idx:audio_idx" string
        like "3:0", or just "3:" for video-only.
    :param output_path: Destination mp4. If empty, auto-generates under
        $CAPTURE_ROOT/out/<timestamp>.mp4.
    :param width: Video width in pixels. Must match a device-supported mode.
    :param height: Video height in pixels. Must match a device-supported mode.
    :param framerate: Frames per second. iPhone supports 30 or 60 only.
    :param video_bitrate: h264 bitrate string (e.g. "8M", "12M").
    :param audio_bitrate: AAC bitrate string (e.g. "192k").
    :return: JSON with {path, duration, bytes, codec, width, height} or error.
    :rtype: str
    """
    _ensure_dirs()
    try:
        if device == "iphone":
            dev_raw = find_iphone_device()
            dev = json.loads(dev_raw)
            if "error" in dev:
                return dev_raw
            device_string = dev["device_string"]
        else:
            device_string = device

        if not output_path:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            output_path = str(OUTPUT_DIR / f"capture-{stamp}.mp4")
        else:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            _ffmpeg_path(), "-hide_banner", "-loglevel", "error",
            "-f", "avfoundation",
            "-framerate", str(framerate),
            "-video_size", f"{width}x{height}",
            "-i", device_string,
            "-t", str(duration_seconds),
            "-c:v", "h264_videotoolbox", "-b:v", video_bitrate,
            "-c:a", "aac", "-b:a", audio_bitrate,
            "-movflags", "+faststart",
            "-y", output_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=duration_seconds + 30)
        if proc.returncode != 0 or not os.path.exists(output_path):
            return json.dumps({
                "error": "ffmpeg failed",
                "stderr": proc.stderr.strip()[-800:],
                "cmd": " ".join(cmd),
            })
        size = os.path.getsize(output_path)
        return json.dumps({
            "path": output_path,
            "bytes": size,
            "width": width,
            "height": height,
            "framerate": framerate,
            "duration_target": duration_seconds,
            "device_string": device_string,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def start_live_capture(
    session_id: str = "",
    device: str = "iphone",
    output_path: str = "",
    width: int = 1920,
    height: int = 1080,
    framerate: int = 30,
    video_bitrate: str = "8M",
    chunk_seconds: int = 0,
) -> str:
    """
    Start a long-running capture session that runs in the background.

    Writes a session file at $CAPTURE_ROOT/sessions/<id>.json with pid + path.
    Stop with stop_live_capture(session_id). The session keeps recording
    even if this tool call returns.

    When chunk_seconds > 0, ffmpeg's segment muxer splits the recording
    into a numbered sequence (output_path is interpreted as a directory
    root; files land as chunk-%04d.mp4 inside it). Each chunk is an
    independently playable mp4, so a crash at any point preserves all
    previous chunks intact.

    :param session_id: Unique session name. Auto-generated from timestamp if empty.
    :param device: "iphone" or "video_idx:audio_idx" string.
    :param output_path: Destination mp4 (single file) or directory (chunked).
        Auto-generated if empty.
    :param width: Video width.
    :param height: Video height.
    :param framerate: FPS. iPhone supports 30 or 60.
    :param video_bitrate: h264 bitrate string.
    :param chunk_seconds: If > 0, split into N-second chunks. 0 = single file.
    :return: JSON with {session_id, pid, path, chunked} or error.
    :rtype: str
    """
    _ensure_dirs()
    try:
        if not session_id:
            session_id = f"live-{time.strftime('%Y%m%d-%H%M%S')}"

        session_file = SESSIONS_DIR / f"{session_id}.json"
        if session_file.exists():
            return json.dumps({"error": f"Session already exists: {session_id}"})

        if device == "iphone":
            dev = json.loads(find_iphone_device())
            if "error" in dev:
                return json.dumps({"error": dev["error"]})
            device_string = dev["device_string"]
        else:
            device_string = device

        chunked = chunk_seconds > 0
        if chunked:
            # Chunked mode: output_path is treated as a directory.
            out_dir = Path(output_path) if output_path else OUTPUT_DIR / f"{session_id}"
            out_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(out_dir / "chunk-%04d.mp4")
        elif not output_path:
            output_path = str(OUTPUT_DIR / f"{session_id}.mp4")
        else:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        log_path = SESSIONS_DIR / f"{session_id}.log"
        cmd = [
            _ffmpeg_path(), "-hide_banner", "-loglevel", "warning",
            "-f", "avfoundation",
            "-framerate", str(framerate),
            "-video_size", f"{width}x{height}",
            "-i", device_string,
            "-c:v", "h264_videotoolbox", "-b:v", video_bitrate,
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
        ]
        if chunked:
            cmd += [
                "-f", "segment",
                "-segment_time", str(chunk_seconds),
                "-segment_format", "mp4",
                "-reset_timestamps", "1",
                "-y", output_path,
            ]
        else:
            cmd += ["-y", output_path]
        log_fh = open(log_path, "wb")
        proc = subprocess.Popen(
            cmd, stdout=log_fh, stderr=log_fh, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        session = {
            "session_id": session_id,
            "pid": proc.pid,
            "path": output_path,
            "log": str(log_path),
            "device_string": device_string,
            "started_at": time.time(),
            "cmd": cmd,
            "chunked": chunked,
            "chunk_seconds": chunk_seconds if chunked else 0,
        }
        session_file.write_text(json.dumps(session, indent=2))
        time.sleep(0.4)
        if proc.poll() is not None:
            tail = log_path.read_text()[-600:] if log_path.exists() else ""
            return json.dumps({"error": "ffmpeg exited immediately", "log": tail})
        return json.dumps({
            "session_id": session_id,
            "pid": proc.pid,
            "path": output_path,
            "status": "recording",
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def get_session_chunks(session_id: str) -> str:
    """
    For a chunked live capture, return the list of produced chunk files
    (sorted by index). Works whether the session is still active or has
    been stopped.

    :param session_id: session_id used when starting the capture.
    :return: JSON with {session_id, chunked, chunks: [{index, path, bytes}]}.
    :rtype: str
    """
    try:
        session_file = SESSIONS_DIR / f"{session_id}.json"
        if not session_file.exists():
            return json.dumps({"error": f"No such session: {session_id}"})
        session = json.loads(session_file.read_text())
        if not session.get("chunked"):
            return json.dumps({"session_id": session_id, "chunked": False, "chunks": []})
        chunk_dir = Path(session["path"]).parent
        chunks = []
        for i, f in enumerate(sorted(chunk_dir.glob("chunk-*.mp4"))):
            chunks.append({
                "index": i,
                "path": str(f),
                "bytes": f.stat().st_size,
            })
        return json.dumps({
            "session_id": session_id,
            "chunked": True,
            "chunk_dir": str(chunk_dir),
            "chunks": chunks,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def stop_live_capture(session_id: str) -> str:
    """
    Gracefully stop a live capture session. Sends SIGINT so ffmpeg writes
    the mp4 moov atom and leaves a playable file.

    :param session_id: The session_id returned by start_live_capture.
    :return: JSON with {path, bytes, duration_observed} or error.
    :rtype: str
    """
    try:
        session_file = SESSIONS_DIR / f"{session_id}.json"
        if not session_file.exists():
            return json.dumps({"error": f"No such session: {session_id}"})
        session = json.loads(session_file.read_text())
        pid = session["pid"]
        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            pass
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

        path = session["path"]
        bytes_ = os.path.getsize(path) if os.path.exists(path) else 0
        duration_observed = time.time() - session["started_at"]
        session_file.unlink(missing_ok=True)
        return json.dumps({
            "session_id": session_id,
            "path": path,
            "bytes": bytes_,
            "duration_observed": round(duration_observed, 2),
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def list_live_captures() -> str:
    """
    List every active background capture session and current file size.

    :return: JSON array with session_id, pid, path, bytes, alive.
    :rtype: str
    """
    try:
        _ensure_dirs()
        out = []
        for sf in SESSIONS_DIR.glob("*.json"):
            try:
                s = json.loads(sf.read_text())
            except Exception:
                continue
            alive = True
            try:
                os.kill(s["pid"], 0)
            except ProcessLookupError:
                alive = False
            out.append({
                "session_id": s["session_id"],
                "pid": s["pid"],
                "path": s["path"],
                "alive": alive,
                "bytes": os.path.getsize(s["path"]) if os.path.exists(s["path"]) else 0,
                "elapsed": round(time.time() - s["started_at"], 1),
            })
        return json.dumps(out)
    except Exception as e:
        return json.dumps({"error": str(e)})


def preview_device(
    device: str = "iphone",
    width: int = 1280,
    height: int = 720,
    framerate: int = 30,
    window_title: str = "Studio Preview",
) -> str:
    """
    Open a live preview window for a capture device using ffplay.

    No recording. Zero-latency display, backgrounded process. Close the
    window or call stop_preview(session_id) to dismiss. Useful for
    framing, subject tracking (Center Stage), and confirming lighting
    before a real record.

    :param device: "iphone" or "video_idx:" or "video_idx:audio_idx".
    :param width: Preview width. 1280x720 is the default to keep CPU light.
    :param height: Preview height.
    :param framerate: Preview framerate (30 or 60 for iPhone).
    :param window_title: Title bar label of the preview window.
    :return: JSON with {session_id, pid, device_string}.
    :rtype: str
    """
    _ensure_dirs()
    try:
        if device == "iphone":
            dev = json.loads(find_iphone_device())
            if "error" in dev:
                return json.dumps({"error": dev["error"]})
            device_string = f"{dev['video_index']}:"
        else:
            device_string = device if ":" in device else f"{device}:"

        session_id = f"preview-{time.strftime('%Y%m%d-%H%M%S')}"
        session_file = SESSIONS_DIR / f"{session_id}.json"
        log_path = SESSIONS_DIR / f"{session_id}.log"

        ffplay = os.environ.get("FFPLAY_BIN", "/opt/homebrew/bin/ffplay")
        # iPhone Continuity Camera delivers uyvy422/yuyv422/nv12 natively.
        # Letting ffplay default to yuv420p triggers "Selected pixel format
        # is not supported" and hangs on frame-rate estimation. Force
        # uyvy422 + a generous probesize.
        cmd = [
            ffplay, "-hide_banner", "-loglevel", "warning",
            "-window_title", window_title,
            "-probesize", "10M", "-analyzeduration", "2M",
            "-f", "avfoundation",
            "-pixel_format", "uyvy422",
            "-framerate", str(framerate),
            "-video_size", f"{width}x{height}",
            "-i", device_string,
            "-fflags", "nobuffer", "-flags", "low_delay",
            "-an",
        ]
        log_fh = open(log_path, "wb")
        proc = subprocess.Popen(
            cmd, stdout=log_fh, stderr=log_fh, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        session = {
            "session_id": session_id,
            "pid": proc.pid,
            "kind": "preview",
            "device_string": device_string,
            "started_at": time.time(),
            "log": str(log_path),
        }
        session_file.write_text(json.dumps(session, indent=2))
        time.sleep(0.6)
        if proc.poll() is not None:
            tail = log_path.read_text()[-600:] if log_path.exists() else ""
            return json.dumps({"error": "ffplay exited immediately", "log": tail})
        return json.dumps({
            "session_id": session_id,
            "pid": proc.pid,
            "device_string": device_string,
            "status": "previewing",
            "window_title": window_title,
            "hint": "Call stop_preview(session_id) or close the window to dismiss.",
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def stop_preview(session_id: str) -> str:
    """
    Close a live preview window.

    :param session_id: Preview session_id returned by preview_device.
    :return: JSON with {stopped, session_id}.
    :rtype: str
    """
    try:
        session_file = SESSIONS_DIR / f"{session_id}.json"
        if not session_file.exists():
            return json.dumps({"error": f"No such preview session: {session_id}"})
        session = json.loads(session_file.read_text())
        try:
            os.kill(session["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
        session_file.unlink(missing_ok=True)
        return json.dumps({"stopped": True, "session_id": session_id})
    except Exception as e:
        return json.dumps({"error": str(e)})


def enable_center_stage() -> str:
    """
    Toggle iPhone Continuity Camera's Center Stage feature via the macOS
    Control Center Video Effects menu. Center Stage uses on-device AI
    to auto-frame the user — follows you around, zooms to keep you in
    frame. Requires macOS Ventura+ and iPhone 11+.

    Note: macOS does not expose a CLI flag for this. The tool drives
    the menu bar Control Center via AppleScript. If the Video Effects
    menu is not visible, the iPhone must be actively in use by a
    capture session (preview or recording) first.

    :return: JSON with {attempted, note}.
    :rtype: str
    """
    script = '''
    tell application "System Events"
        tell process "ControlCenter"
            try
                click menu bar item "Control Center" of menu bar 1
                delay 0.4
                click button "Video Effects" of window 1
                delay 0.4
                click button "Center Stage" of window 1
                delay 0.3
                key code 53
                return "toggled"
            on error errMsg
                try
                    key code 53
                end try
                return "error: " & errMsg
            end try
        end tell
    end tell
    '''
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=8,
        )
        return json.dumps({
            "attempted": True,
            "output": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "note": (
                "If 'error: ...menu...does not exist', iPhone is not yet "
                "in use by a capture process. Start preview_device first, "
                "then call this again."
            ),
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def capture_still(device: str = "iphone", output_path: str = "") -> str:
    """
    Grab a single still frame from a capture device.

    Useful for thumbnail generation, quick "is the iPhone framed right?"
    checks before a long record, or feeding analyze_image for scene triage.

    :param device: "iphone" or "video_idx:audio_idx" or "video_idx:".
    :param output_path: Destination .jpg. Auto-generated if empty.
    :return: JSON with path + bytes.
    :rtype: str
    """
    _ensure_dirs()
    try:
        if device == "iphone":
            dev = json.loads(find_iphone_device())
            if "error" in dev:
                return json.dumps({"error": dev["error"]})
            video_string = f"{dev['video_index']}:"
        else:
            video_string = device if ":" in device else f"{device}:"

        if not output_path:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            output_path = str(OUTPUT_DIR / f"still-{stamp}.jpg")
        else:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            _ffmpeg_path(), "-hide_banner", "-loglevel", "error",
            "-f", "avfoundation",
            "-framerate", "30",
            "-video_size", "1920x1080",
            "-i", video_string,
            "-frames:v", "1",
            "-q:v", "2",
            "-y", output_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if proc.returncode != 0 or not os.path.exists(output_path):
            return json.dumps({"error": "ffmpeg failed", "stderr": proc.stderr.strip()[-400:]})
        return json.dumps({"path": output_path, "bytes": os.path.getsize(output_path)})
    except Exception as e:
        return json.dumps({"error": str(e)})
