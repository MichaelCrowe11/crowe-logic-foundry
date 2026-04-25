# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio — proprietary, private repository.

"""
Shot selector — reads a presentation script + a shoot manifest and
produces an Edit Decision List (EDL): which camera feeds which second
of the final cut, with which zoom effect.

Two selection strategies:

  rule_based  — reads script directives per section ([angle: close],
                [zoom: punch_in]) and falls back to the primary camera
                when ambiguous. Deterministic. Fast. No model calls.

  crowelm     — placeholder that routes to the CroweLM model for
                natural shot-selection reasoning. Not wired yet; will
                become the moat in v0.7.

The EDL format is intentionally simple JSON so the renderer, the
dashboard, and eventually the cloud container can all consume it.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Optional

import httpx

from tools.presentation import load_script
from tools.capture import CAPTURE_ROOT

SHOOTS_DIR = CAPTURE_ROOT / "shoots"
EDLS_DIR = CAPTURE_ROOT / "edls"

CROWELM_BASE_URL = os.environ.get("CROWELM_BASE_URL", "http://localhost:11434/v1")
CROWELM_MODEL = os.environ.get("CROWELM_MODEL", "Mcrowe1210/DeepParallel:v2.2")
CROWELM_TIMEOUT_SECONDS = int(os.environ.get("CROWELM_TIMEOUT_SECONDS", "45"))


def _ensure_edls() -> None:
    EDLS_DIR.mkdir(parents=True, exist_ok=True)


def _probe_duration(path: str) -> float:
    try:
        r = subprocess.run(
            ["/opt/homebrew/bin/ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=15,
        )
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0.0


def _load_shoot(shoot_id: str) -> dict | None:
    p = SHOOTS_DIR / f"{shoot_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def _pick_camera_for_section(section: dict, cameras: list[dict], primary: dict | None) -> dict:
    """
    Rule-based camera pick.

    Priority:
      1. Script [angle: <name>] directive matches a camera name or role.
      2. Script [camera: <name>] directive matches a camera name.
      3. Fall back to primary sync camera.
      4. Fall back to first available camera.
    """
    angle = (section.get("angle") or "").strip().lower()
    cam_dir = (section.get("camera") or "").strip().lower()

    if cam_dir:
        for c in cameras:
            if c["camera"].lower() == cam_dir:
                return c
    if angle:
        for c in cameras:
            if c.get("role", "").lower() == angle or c["camera"].lower() == angle:
                return c

    if primary:
        return primary
    return cameras[0] if cameras else None


def _crowelm_pick_shots(
    sections: list[dict],
    clips: list[dict],
    primary_camera: str,
) -> Optional[list[dict]]:
    """
    Ask the local CroweLM model (DeepParallel via Ollama) to pick a
    camera for each section. Returns a list of {section_index,
    camera, reason} dicts or None on any failure.

    The model is instructed to return strict JSON. We validate every
    camera name exists before trusting the pick.
    """
    camera_summaries = []
    for c in clips:
        camera_summaries.append({
            "camera": c["camera"],
            "role": c.get("role", "unknown"),
            "is_primary_audio": c["camera"] == primary_camera,
        })

    section_summaries = []
    for s in sections:
        section_summaries.append({
            "index": s["index"],
            "title": s["title"],
            "body_preview": (s.get("body", "") or "")[:280],
            "word_count": s["word_count"],
            "duration_seconds": s.get("duration"),
            "script_zoom_hint": s.get("zoom"),
        })

    system = (
        "You are CroweLM, the shot-selection brain of Crowe Studio. "
        "For each script section, pick the single best camera from the "
        "available list. Optimize for engagement: hooks get close-ups "
        "or product angles; explanations stay on the primary wide shot; "
        "calls-to-action return to wide. Output STRICT JSON only: a "
        "list of objects with keys section_index (int), camera (string "
        "matching exactly one available camera name), reason (short "
        "string). No prose, no markdown, no code fences."
    )
    user = json.dumps({
        "available_cameras": camera_summaries,
        "script_sections": section_summaries,
    })

    try:
        with httpx.Client(timeout=CROWELM_TIMEOUT_SECONDS) as client:
            r = client.post(
                f"{CROWELM_BASE_URL}/chat/completions",
                json={
                    "model": CROWELM_MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                    "stream": False,
                },
            )
        if r.status_code >= 400:
            return None
        data = r.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        if not content:
            return None

        # Model might wrap in {"picks": [...]} or return a raw array.
        parsed = json.loads(content)
        picks = parsed.get("picks") if isinstance(parsed, dict) else parsed
        if isinstance(parsed, dict) and not picks:
            # try any top-level array-valued key
            for v in parsed.values():
                if isinstance(v, list):
                    picks = v
                    break
        if not isinstance(picks, list):
            return None

        valid_cameras = {c["camera"] for c in clips}
        cleaned = []
        for p in picks:
            if not isinstance(p, dict):
                continue
            idx = p.get("section_index")
            cam = p.get("camera")
            if cam not in valid_cameras:
                continue
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                continue
            cleaned.append({
                "section_index": idx,
                "camera": cam,
                "reason": (p.get("reason") or "")[:200],
            })
        if not cleaned:
            return None
        return cleaned
    except Exception:
        return None


def build_edl(
    script_path: str,
    shoot_id: str,
    strategy: str = "rule_based",
    output_path: str = "",
) -> str:
    """
    Construct an Edit Decision List for a shoot given a presentation
    script. Section durations come from the script's [duration: N]
    directives; if a section has no duration, we distribute the
    remaining shoot length proportionally to word count.

    :param script_path: Path to the markdown script.
    :param shoot_id: A stopped shoot with recorded clips.
    :param strategy: "rule_based" or "crowelm" (crowelm not wired yet).
    :param output_path: Where to save the EDL JSON. Auto-generated if empty.
    :return: JSON with the EDL summary: {path, sections, total_duration,
        cameras_used}.
    :rtype: str
    """
    try:
        _ensure_edls()
        parsed = json.loads(load_script(script_path))
        if "error" in parsed:
            return json.dumps({"error": f"script parse failed: {parsed['error']}"})

        shoot = _load_shoot(shoot_id)
        if not shoot:
            return json.dumps({"error": f"Unknown shoot: {shoot_id}"})

        clips = shoot.get("clips") or [
            {"camera": c["camera"], "role": c["role"], "path": c["path"],
             "sync_priority": c.get("sync_priority", "secondary")}
            for c in shoot.get("cameras", [])
        ]
        if not clips:
            return json.dumps({"error": "shoot has no recorded clips"})

        primary = next((c for c in clips if c.get("sync_priority") == "primary"), clips[0])
        master_path = primary["path"]
        master_duration = _probe_duration(master_path)
        if master_duration <= 0:
            return json.dumps({"error": "cannot probe master clip duration"})

        sections = parsed.get("sections", [])
        if not sections:
            return json.dumps({"error": "script has no sections"})

        # Decide duration per section. Honor explicit [duration: N]
        # directives; fill the remainder pro-rata by word count.
        explicit = sum(s["duration"] for s in sections if s.get("duration"))
        unbounded = [s for s in sections if not s.get("duration")]
        remaining = max(0.0, master_duration - explicit)
        total_unbounded_words = sum(s["word_count"] for s in unbounded) or 1
        for s in unbounded:
            s["duration"] = max(1.0, remaining * (s["word_count"] / total_unbounded_words))

        # If requested, ask CroweLM for its shot picks; fall back to rules
        # if the model is unreachable or returns invalid JSON.
        crowelm_picks: dict[int, dict] = {}
        effective_strategy = strategy
        if strategy == "crowelm":
            primary_name = primary.get("camera") if primary else (clips[0]["camera"] if clips else None)
            picks = _crowelm_pick_shots(sections, clips, primary_name)
            if picks:
                for p in picks:
                    crowelm_picks[p["section_index"]] = p
            else:
                effective_strategy = "rule_based_fallback"

        edl_sections = []
        cursor = 0.0
        cameras_used = set()
        for i, sec in enumerate(sections):
            dur = float(sec["duration"])
            end = min(master_duration, cursor + dur)

            pick_reason = None
            if i in crowelm_picks:
                pick_name = crowelm_picks[i]["camera"]
                cam = next((c for c in clips if c["camera"] == pick_name), None)
                pick_reason = crowelm_picks[i].get("reason")
            else:
                cam = None

            if not cam:
                cam = _pick_camera_for_section(sec, clips, primary)
            if not cam:
                return json.dumps({"error": "no camera available for any section"})

            entry = {
                "index": i,
                "title": sec["title"],
                "source_camera": cam["camera"],
                "source_path": cam["path"],
                "source_start": round(cursor, 3),
                "source_end": round(end, 3),
                "duration": round(end - cursor, 3),
                "zoom": sec.get("zoom") or "none",
            }
            if pick_reason:
                entry["crowelm_reason"] = pick_reason
            edl_sections.append(entry)
            cameras_used.add(cam["camera"])
            cursor = end
            if cursor >= master_duration:
                break

        edl = {
            "edl_id": f"edl-{shoot_id}",
            "shoot_id": shoot_id,
            "script_path": str(Path(script_path).resolve()),
            "strategy": strategy,
            "effective_strategy": effective_strategy,
            "crowelm_model": CROWELM_MODEL if strategy == "crowelm" else None,
            "master_audio_path": master_path,
            "total_duration": round(cursor, 3),
            "sections": edl_sections,
            "cameras_used": sorted(cameras_used),
        }

        if not output_path:
            output_path = str(EDLS_DIR / f"{edl['edl_id']}.json")
        Path(output_path).write_text(json.dumps(edl, indent=2))

        return json.dumps({
            "path": output_path,
            "edl_id": edl["edl_id"],
            "shoot_id": shoot_id,
            "sections": len(edl_sections),
            "total_duration": edl["total_duration"],
            "cameras_used": edl["cameras_used"],
            "strategy": strategy,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def load_edl(edl_path: str) -> str:
    """
    Load and return a saved EDL. Convenience for agent/dashboard reads.

    :param edl_path: Absolute path to the EDL JSON.
    :return: JSON of the EDL or error.
    :rtype: str
    """
    try:
        p = Path(edl_path)
        if not p.exists():
            return json.dumps({"error": f"EDL not found: {edl_path}"})
        return p.read_text()
    except Exception as e:
        return json.dumps({"error": str(e)})


def list_edls(limit: int = 20) -> str:
    """
    List recent EDLs with their shoot_id, section count, and duration.

    :param limit: Max EDLs to return.
    :return: JSON array of EDL summaries.
    :rtype: str
    """
    try:
        _ensure_edls()
        out = []
        for p in sorted(EDLS_DIR.glob("*.json"),
                         key=lambda f: f.stat().st_mtime, reverse=True)[:limit]:
            try:
                e = json.loads(p.read_text())
            except Exception:
                continue
            out.append({
                "path": str(p),
                "edl_id": e.get("edl_id"),
                "shoot_id": e.get("shoot_id"),
                "sections": len(e.get("sections", [])),
                "total_duration": e.get("total_duration"),
                "cameras_used": e.get("cameras_used", []),
            })
        return json.dumps(out)
    except Exception as e:
        return json.dumps({"error": str(e)})
