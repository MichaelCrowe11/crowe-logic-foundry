#!/usr/bin/env python3
# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
End-to-end Crowe Studio demo.

Runs the complete loop:
    start_shoot → wait → stop_shoot → build_edl → render_edl → route → open

Non-destructive: routes the final render to the "scratch" tenant.

Usage:
    .venv/bin/python scripts/e2e.py
    .venv/bin/python scripts/e2e.py --duration 20 --script path/to/script.md --tenant toxicteetv
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.edl_render import render_edl
from tools.shoot import list_cameras, start_shoot, stop_shoot
from tools.shot_selector import build_edl
from tools.studio_route import route_clip_to_tenant
from tools.sync import sync_shoot


def step(n: str, msg: str) -> None:
    print(f"\n[{n}] {msg}")


def _json(raw: str, label: str) -> dict:
    try:
        parsed = json.loads(raw)
    except Exception:
        print(f"  !! {label} returned non-JSON: {raw[:300]}")
        sys.exit(1)
    if isinstance(parsed, dict) and "error" in parsed:
        print(f"  !! {label} ERROR: {parsed['error']}")
        sys.exit(1)
    return parsed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=15)
    ap.add_argument("--script", default=str(Path("/tmp/crowe-capture/presentations/sample_script.md")))
    ap.add_argument("--tenant", default="scratch")
    ap.add_argument("--cameras", default="")
    ap.add_argument("--strategy", default="rule_based", choices=["rule_based", "crowelm"])
    ap.add_argument("--sync", action="store_true", default=False)
    ap.add_argument("--open-result", action="store_true", default=True)
    args = ap.parse_args()

    print("=" * 64)
    print("CROWE STUDIO — END-TO-END DEMO")
    print("=" * 64)

    step("1/6", "Camera registry")
    cams = _json(list_cameras(), "list_cameras")
    for c in cams:
        mark = "OK" if c["available"] else "--"
        print(f"    [{mark}]  {c['name']:<18} role={c['role']:<10} {c.get('resolved','')}")

    shoot_id = f"e2e-{time.strftime('%H%M%S')}"
    step("2/6", f"Starting shoot '{shoot_id}' for {args.duration}s")
    s = _json(start_shoot(shoot_id=shoot_id, cameras=args.cameras), "start_shoot")
    print(f"    cameras running: {s['cameras']}  failed: {s['failed']}")

    print(f"    recording {args.duration} seconds ...")
    time.sleep(args.duration)

    step("3/6", "Stopping shoot")
    stopped = _json(stop_shoot(shoot_id), "stop_shoot")
    for c in stopped["clips"]:
        mb = c["bytes"] / 1024 / 1024
        print(f"    {c['camera']:<18} {mb:>6.1f} MB  {c['path']}")

    if args.sync:
        step("3.5/6", "Cross-correlating audio to compute per-camera offsets")
        sync = _json(sync_shoot(shoot_id=shoot_id), "sync_shoot")
        for o in sync.get("offsets", []):
            mark = "primary" if o.get("is_primary") else ("silent" if not o.get("has_audio") else f"{o['offset_ms']:+.1f}ms")
            conf = f"conf {o.get('confidence', 0)*100:.0f}%" if o.get("has_audio") and not o.get("is_primary") else ""
            print(f"    {o['camera']:<18}  {mark:<14}  {conf}")

    step("4/6", f"Building EDL from {Path(args.script).name}  (strategy={args.strategy})")
    edl = _json(build_edl(script_path=args.script, shoot_id=shoot_id, strategy=args.strategy), "build_edl")
    print(f"    edl_id={edl['edl_id']}  sections={edl['sections']}  duration={edl['total_duration']}s")
    print(f"    cameras_used={edl['cameras_used']}  strategy={edl.get('strategy')}")

    step("5/6", "Rendering final multi-angle cut")
    render = _json(render_edl(edl_path=edl["path"]), "render_edl")
    mb = render["bytes"] / 1024 / 1024
    print(f"    output: {render['output']}")
    print(f"    sections_rendered={render['sections_rendered']}  {mb:.1f} MB  {render['render_seconds']}s to render")

    step("6/6", f"Routing final cut to tenant '{args.tenant}'")
    routed = _json(route_clip_to_tenant(
        clip_path=render["output"], tenant=args.tenant,
    ), "route_clip_to_tenant")
    print(f"    session_id: {routed['session_id']}")
    print(f"    manifest_id: {routed.get('manifest_id')}")
    print(f"    dest: {routed['dest_path']}")

    print("\n" + "=" * 64)
    print("E2E COMPLETE")
    print("=" * 64)
    print(f"final cut: {render['output']}")
    if args.open_result:
        subprocess.Popen(["open", render["output"]])
        print("(opened in default video viewer)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
