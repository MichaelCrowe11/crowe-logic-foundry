#!/usr/bin/env python3
# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
Crowe Studio smoke test — runs the full capture/route/zoom chain in
under 30 seconds. Non-destructive: writes to the "scratch" tenant.

Run from repo root:
    .venv/bin/python scripts/studio-smoke-test.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.capture import capture_clip, capture_still, find_iphone_device, list_capture_devices
from tools.presentation import apply_zoom_effect, list_zoom_effects
from tools.studio_route import list_tenants, route_clip_to_tenant


CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    icon = "PASS" if ok else "FAIL"
    print(f"  [{icon}]  {name}" + (f"  — {detail}" if detail else ""))


def main() -> int:
    t0 = time.time()
    print("=" * 60)
    print("CROWE STUDIO SMOKE TEST")
    print("=" * 60)

    # 1. device enumeration
    print("\n[1] device enumeration")
    devs = json.loads(list_capture_devices())
    has_video = bool(devs.get("video"))
    has_iphone = any(d.get("is_iphone") for d in devs.get("video", []))
    check("list_capture_devices returns video list", has_video, f"{len(devs.get('video', []))} devices")
    check("at least one iPhone visible", has_iphone)

    # 2. iphone resolve
    print("\n[2] iPhone resolution")
    ip = json.loads(find_iphone_device())
    check("find_iphone_device succeeds", "error" not in ip,
          ip.get("device_string", ip.get("error", "")))

    # 3. tenant registry
    print("\n[3] tenant registry")
    tenants = json.loads(list_tenants())
    has_scratch = any(t.get("name") == "scratch" for t in tenants)
    check("tenant registry loads", isinstance(tenants, list), f"{len(tenants)} tenants")
    check("scratch tenant exists", has_scratch)

    # 4. zoom presets
    print("\n[4] zoom presets")
    presets = json.loads(list_zoom_effects())
    has_punch = "punch_in" in presets
    check("zoom presets registered", isinstance(presets, dict), f"{len(presets)} presets")
    check("punch_in preset exists", has_punch)

    if not has_iphone or "error" in ip:
        print("\nCANNOT proceed with capture — no iPhone. Is Continuity Camera on?")
        return _finish()

    # 5. still capture
    print("\n[5] still capture")
    still = json.loads(capture_still())
    check("still captured", "error" not in still and still.get("bytes", 0) > 0,
          f"{still.get('bytes', 0)} bytes")

    # 6. 2-second clip
    print("\n[6] 2-second clip capture")
    clip = json.loads(capture_clip(duration_seconds=2))
    check("clip captured", "error" not in clip and clip.get("bytes", 0) > 0,
          f"{clip.get('bytes', 0)} bytes")

    if "error" in clip:
        return _finish()

    # 7. route to scratch
    print("\n[7] route to scratch tenant")
    route = json.loads(route_clip_to_tenant(clip_path=clip["path"], tenant="scratch"))
    check("routed to scratch", "error" not in route,
          f"session={route.get('session_id')}")

    # 8. zoom
    print("\n[8] apply punch_in zoom")
    zoom = json.loads(apply_zoom_effect(clip_path=clip["path"], effect="punch_in"))
    check("zoom rendered", "error" not in zoom and zoom.get("bytes", 0) > 0,
          f"{zoom.get('bytes', 0)} bytes")

    return _finish()


def _finish() -> int:
    dt = time.time() - 0  # recompute below
    passes = sum(1 for _, ok, _ in CHECKS if ok)
    fails = sum(1 for _, ok, _ in CHECKS if not ok)
    print()
    print("=" * 60)
    print(f"RESULT  {passes} PASS  {fails} FAIL  ({len(CHECKS)} total)")
    print("=" * 60)
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
