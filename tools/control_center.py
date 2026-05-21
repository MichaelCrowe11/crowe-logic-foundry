# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio — proprietary, private repository.

"""
Studio Control Center — local FastAPI dashboard for the studio agent stack.

One browser window, live status, one-click everything. Wraps the
existing capture / studio_route / presentation tools as HTTP endpoints.

Usage from Python:
    from tools.control_center import start_control_center
    start_control_center(port=7777, open_browser=True)

Or from CLI:
    .venv/bin/python -m tools.control_center
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from tools.capture import (
    capture_still, find_iphone_device, list_capture_devices,
    list_live_captures, preview_device, start_live_capture,
    stop_live_capture, stop_preview,
)
from tools.presentation import (
    apply_zoom_effect, launch_teleprompter, list_zoom_effects, load_script,
)
from tools.studio_route import (
    list_tenants, route_clip_to_tenant, tenant_inbox_peek,
)
from tools.shoot import (
    list_cameras, list_shoots, register_cloud_camera,
    start_shoot, stop_shoot,
)
from tools.shot_selector import build_edl, list_edls, load_edl
from tools.edl_render import render_edl
from tools.sync import sync_shoot

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "dashboard" / "static"
CAPTURE_OUT = Path(os.environ.get("CAPTURE_ROOT", "/tmp/crowe-capture")) / "out"


def _parse(raw: str) -> dict | list:
    try:
        return json.loads(raw)
    except Exception:
        return {"error": "parse failed", "raw": raw}


# ─── pydantic models for request bodies ────────────────────────────────

class StartRecordingReq(BaseModel):
    session_id: Optional[str] = None
    device: str = "iphone"
    width: int = 1920
    height: int = 1080
    framerate: int = 30
    video_bitrate: str = "10M"


class StopRecordingReq(BaseModel):
    session_id: str


class StartPreviewReq(BaseModel):
    device: str = "iphone"
    width: int = 1280
    height: int = 720
    framerate: int = 30
    window_title: str = "Studio Self-view"


class StopPreviewReq(BaseModel):
    session_id: str


class TeleprompterReq(BaseModel):
    script_path: str
    wpm: int = 150
    mirror: bool = False


class RouteReq(BaseModel):
    clip_path: str
    tenant: str
    session_id: Optional[str] = None
    move: bool = False


class ZoomReq(BaseModel):
    clip_path: str
    effect: str = "punch_in"
    output_path: Optional[str] = None


class StillReq(BaseModel):
    device: str = "iphone"


class LoadScriptReq(BaseModel):
    script_path: str


class StartShootReq(BaseModel):
    shoot_id: Optional[str] = None
    cameras: Optional[str] = None
    framerate_override: int = 0


class StopShootReq(BaseModel):
    shoot_id: str


class CloudCameraReq(BaseModel):
    shoot_id: str
    camera_name: str
    role: str = "broll"
    uplink_url: str


class AutoEditReq(BaseModel):
    shoot_id: str
    script_path: str
    strategy: str = "rule_based"
    route_to_tenant: Optional[str] = None
    sync_first: bool = True


class SyncReq(BaseModel):
    shoot_id: str
    window_seconds: int = 15


class PreviewEDLReq(BaseModel):
    shoot_id: str
    script_path: str
    strategy: str = "crowelm"


class RenderOverrideReq(BaseModel):
    edl_path: str
    overrides: dict[int, str] = {}  # section_index -> camera name
    route_to_tenant: Optional[str] = None


class BatchRouteReq(BaseModel):
    items: list[dict]  # [{path, tenant, session_id?, move?}]


# ─── app ───────────────────────────────────────────────────────────────

app = FastAPI(title="Studio Control Center")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html_path = DASHBOARD_DIR / "studio.html"
    if not html_path.exists():
        return HTMLResponse(f"<pre>Dashboard file missing: {html_path}</pre>", status_code=500)
    return HTMLResponse(html_path.read_text())


@app.get("/api/status")
def api_status():
    devices = _parse(list_capture_devices())
    iphone = _parse(find_iphone_device())
    sessions = _parse(list_live_captures())
    tenants = _parse(list_tenants())
    presets = _parse(list_zoom_effects())
    recent = _recent_outputs()
    return {
        "devices": devices,
        "iphone": iphone,
        "sessions": sessions if isinstance(sessions, list) else [],
        "tenants": tenants if isinstance(tenants, list) else [],
        "zoom_presets": presets,
        "recent_outputs": recent,
        "ts": time.time(),
    }


def _recent_outputs(limit: int = 20) -> list[dict]:
    if not CAPTURE_OUT.exists():
        return []
    entries = []
    for p in sorted(CAPTURE_OUT.rglob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]:
        entries.append({
            "path": str(p),
            "name": p.name,
            "bytes": p.stat().st_size,
            "mtime": p.stat().st_mtime,
            "session_dir": p.parent.name if p.parent != CAPTURE_OUT else None,
        })
    for p in sorted(CAPTURE_OUT.rglob("*.jpg"), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]:
        entries.append({
            "path": str(p),
            "name": p.name,
            "bytes": p.stat().st_size,
            "mtime": p.stat().st_mtime,
            "kind": "still",
        })
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries[:limit]


@app.post("/api/recording/start")
def api_recording_start(req: StartRecordingReq):
    return _parse(start_live_capture(
        session_id=req.session_id or "",
        device=req.device,
        width=req.width, height=req.height,
        framerate=req.framerate, video_bitrate=req.video_bitrate,
    ))


@app.post("/api/recording/stop")
def api_recording_stop(req: StopRecordingReq):
    return _parse(stop_live_capture(req.session_id))


@app.post("/api/preview/start")
def api_preview_start(req: StartPreviewReq):
    return _parse(preview_device(
        device=req.device,
        width=req.width, height=req.height,
        framerate=req.framerate,
        window_title=req.window_title,
    ))


@app.post("/api/preview/stop")
def api_preview_stop(req: StopPreviewReq):
    return _parse(stop_preview(req.session_id))


@app.post("/api/still")
def api_still(req: StillReq):
    return _parse(capture_still(device=req.device))


@app.post("/api/teleprompter/launch")
def api_teleprompter(req: TeleprompterReq):
    return _parse(launch_teleprompter(
        script_path=req.script_path, wpm=req.wpm, mirror=req.mirror,
    ))


@app.post("/api/teleprompter/load")
def api_load_script(req: LoadScriptReq):
    return _parse(load_script(req.script_path))


@app.post("/api/route")
def api_route(req: RouteReq):
    return _parse(route_clip_to_tenant(
        clip_path=req.clip_path, tenant=req.tenant,
        session_id=req.session_id or "", move=req.move,
    ))


@app.post("/api/zoom")
def api_zoom(req: ZoomReq):
    return _parse(apply_zoom_effect(
        clip_path=req.clip_path, effect=req.effect,
        output_path=req.output_path or "",
    ))


@app.get("/api/tenant/{name}/inbox")
def api_tenant_inbox(name: str, limit: int = 5):
    return _parse(tenant_inbox_peek(name, limit=limit))


# ─── Multi-camera shoots ───────────────────────────────────────────────

@app.get("/api/cameras")
def api_cameras():
    return _parse(list_cameras())


@app.get("/api/shoots")
def api_shoots(limit: int = 20):
    return _parse(list_shoots(limit=limit))


@app.post("/api/shoot/start")
def api_shoot_start(req: StartShootReq):
    return _parse(start_shoot(
        shoot_id=req.shoot_id or "",
        cameras=req.cameras or "",
        framerate_override=req.framerate_override,
    ))


@app.post("/api/shoot/stop")
def api_shoot_stop(req: StopShootReq):
    return _parse(stop_shoot(req.shoot_id))


@app.post("/api/shoot/register-cloud")
def api_register_cloud(req: CloudCameraReq):
    return _parse(register_cloud_camera(
        shoot_id=req.shoot_id, camera_name=req.camera_name,
        role=req.role, uplink_url=req.uplink_url,
    ))


@app.post("/api/shoot/auto-edit")
def api_auto_edit(req: AutoEditReq):
    """
    One-call shoot-to-final pipeline:
      1. Optionally sync (cross-correlate audio) so per-camera lag is fixed.
      2. Build EDL from script + shoot manifest using chosen strategy.
      3. Render the EDL to a final mp4.
      4. Optionally route to a tenant pipeline.
    """
    sync_info = None
    if req.sync_first:
        sync_info = _parse(sync_shoot(shoot_id=req.shoot_id))

    edl_summary = _parse(build_edl(
        script_path=req.script_path,
        shoot_id=req.shoot_id,
        strategy=req.strategy,
    ))
    if isinstance(edl_summary, dict) and "error" in edl_summary:
        return {"stage": "edl", "error": edl_summary["error"], "sync": sync_info}

    render = _parse(render_edl(edl_path=edl_summary["path"]))
    if isinstance(render, dict) and "error" in render:
        return {"stage": "render", "edl": edl_summary, "sync": sync_info, "error": render["error"]}

    routed = None
    if req.route_to_tenant:
        routed = _parse(route_clip_to_tenant(
            clip_path=render["output"], tenant=req.route_to_tenant,
        ))

    return {
        "stage": "complete",
        "sync": sync_info,
        "edl": edl_summary,
        "render": render,
        "routed": routed,
    }


@app.post("/api/shoot/sync")
def api_shoot_sync(req: SyncReq):
    return _parse(sync_shoot(shoot_id=req.shoot_id, window_seconds=req.window_seconds))


@app.post("/api/shoot/preview-edl")
def api_preview_edl(req: PreviewEDLReq):
    """Build the EDL without rendering so the dashboard can show the
    shot plan and let the user override picks before committing."""
    edl_summary = _parse(build_edl(
        script_path=req.script_path,
        shoot_id=req.shoot_id,
        strategy=req.strategy,
    ))
    if isinstance(edl_summary, dict) and "error" in edl_summary:
        return edl_summary
    # Inline the full EDL so the dashboard has section detail
    full_edl = _parse(load_edl(edl_path=edl_summary["path"]))
    edl_summary["edl"] = full_edl
    return edl_summary


@app.post("/api/render-with-overrides")
def api_render_with_overrides(req: RenderOverrideReq):
    """Apply user overrides to an existing EDL, save it, render it,
    optionally route, and save a training tuple."""
    from tools.training_store import record_shot_selection
    try:
        edl_path = req.edl_path
        edl = json.loads(Path(edl_path).read_text())
    except Exception as e:
        return {"error": f"cannot load edl: {e}"}

    overrides_applied = []
    cams_by_name = {s["source_camera"]: s for s in edl["sections"]}
    cameras_lookup = {s["source_camera"]: s["source_path"] for s in edl["sections"]}
    # To override to a new camera not currently in the EDL, we need its path.
    # Get from shoot manifest.
    shoot_id = edl.get("shoot_id")
    shoot_path_map: dict[str, str] = {}
    if shoot_id:
        from tools.shoot import SHOOTS_DIR
        mp = SHOOTS_DIR / f"{shoot_id}.json"
        if mp.exists():
            sm = json.loads(mp.read_text())
            clips = sm.get("clips") or sm.get("cameras", [])
            for c in clips:
                shoot_path_map[c["camera"]] = c["path"]

    original_picks = {s["index"]: s["source_camera"] for s in edl["sections"]}

    for idx_str, cam in (req.overrides or {}).items():
        idx = int(idx_str)
        if idx >= len(edl["sections"]):
            continue
        new_path = shoot_path_map.get(cam) or cameras_lookup.get(cam)
        if not new_path:
            continue
        if edl["sections"][idx]["source_camera"] != cam:
            overrides_applied.append({
                "section_index": idx,
                "from": edl["sections"][idx]["source_camera"],
                "to": cam,
            })
        edl["sections"][idx]["source_camera"] = cam
        edl["sections"][idx]["source_path"] = new_path
        edl["sections"][idx]["overridden"] = True

    edl["overrides_applied"] = overrides_applied
    Path(edl_path).write_text(json.dumps(edl, indent=2))

    render = _parse(render_edl(edl_path=edl_path))
    if isinstance(render, dict) and "error" in render:
        return {"stage": "render", "error": render["error"]}

    routed = None
    if req.route_to_tenant:
        routed = _parse(route_clip_to_tenant(
            clip_path=render["output"], tenant=req.route_to_tenant,
        ))

    # Record training tuple (for future fine-tune).
    final_picks = {s["index"]: s["source_camera"] for s in edl["sections"]}
    try:
        record_shot_selection(
            shoot_id=edl.get("shoot_id", ""),
            edl_id=edl.get("edl_id", ""),
            strategy=edl.get("strategy"),
            effective_strategy=edl.get("effective_strategy"),
            script_path=edl.get("script_path", ""),
            original_picks=original_picks,
            final_picks=final_picks,
            overrides=overrides_applied,
            sections=edl.get("sections", []),
            cameras_used=edl.get("cameras_used", []),
            render_output=render.get("output"),
        )
    except Exception:
        pass

    return {
        "stage": "complete",
        "edl": {"path": edl_path, "overrides_applied": len(overrides_applied)},
        "render": render,
        "routed": routed,
    }


@app.post("/api/route/batch")
def api_route_batch(req: BatchRouteReq):
    results = []
    for item in req.items:
        res = _parse(route_clip_to_tenant(
            clip_path=item.get("path", ""),
            tenant=item.get("tenant", "scratch"),
            session_id=item.get("session_id", "") or "",
            move=bool(item.get("move", False)),
        ))
        results.append({"path": item.get("path"), "tenant": item.get("tenant"), "result": res})
    return {"count": len(results), "results": results}


@app.get("/api/training/stats")
def api_training_stats():
    from tools.training_store import training_stats
    return _parse(training_stats())


@app.get("/api/edls")
def api_edls(limit: int = 20):
    return _parse(list_edls(limit=limit))


@app.get("/api/edl/load")
def api_edl_load(path: str):
    return _parse(load_edl(edl_path=path))


@app.post("/api/shoot/{shoot_id}/upload/{camera_name}")
async def api_cloud_upload(shoot_id: str, camera_name: str, request):
    """
    Endpoint for remote (cloud container, second iPhone) cameras to push
    their captured clips into an active shoot. Writes to the shoot's
    <shoot_dir>/cloud__<camera>.mp4. Intentionally minimal; scale via
    streaming adapters (WebRTC/NDI) in a future release.
    """
    from tools.shoot import SHOOTS_DIR
    manifest_path = SHOOTS_DIR / f"{shoot_id}.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="shoot not found")
    manifest = json.loads(manifest_path.read_text())
    shoot_dir = Path(manifest["shoot_dir"])
    dest = shoot_dir / f"cloud__{camera_name}.mp4"
    with dest.open("wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
    return {"shoot_id": shoot_id, "camera": camera_name,
            "path": str(dest), "bytes": dest.stat().st_size}


@app.get("/files/{path:path}")
def serve_file(path: str):
    # Minimal local file server so the dashboard can embed mp4/jpg previews.
    # Restricted to paths under CAPTURE_OUT for safety.
    full = Path("/") / path
    try:
        full.resolve().relative_to(CAPTURE_OUT.resolve())
    except Exception:
        raise HTTPException(status_code=403, detail="path outside capture root")
    if not full.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(full))


@app.post("/api/open")
def api_open(path: str):
    """Open a file in the default macOS viewer."""
    try:
        subprocess.Popen(["open", path])
        return {"opened": path}
    except Exception as e:
        return {"error": str(e)}


# ─── launcher ──────────────────────────────────────────────────────────

_server_thread: Optional[threading.Thread] = None


def start_control_center(
    port: int = 7777,
    host: str = "127.0.0.1",
    open_browser: bool = True,
) -> str:
    """
    Start the Studio Control Center. Runs a local FastAPI server on the
    given port and opens a Safari window on the main display.

    :param port: TCP port (default 7777).
    :param host: Bind host (default 127.0.0.1, local only).
    :param open_browser: If true, opens Safari to the dashboard URL.
    :return: JSON with {url, port, pid_of_thread, opened}.
    :rtype: str
    """
    import uvicorn

    url = f"http://{host}:{port}/"

    def _run():
        uvicorn.run(app, host=host, port=port, log_level="warning")

    global _server_thread
    if _server_thread is None or not _server_thread.is_alive():
        _server_thread = threading.Thread(target=_run, daemon=True)
        _server_thread.start()
        time.sleep(0.7)

    if open_browser:
        applescript = f'''
        tell application "Finder"
            set b to bounds of window of desktop
        end tell
        set scrW to item 3 of b
        set scrH to item 4 of b
        tell application "Safari"
            activate
            make new document with properties {{URL:"{url}"}}
            delay 0.3
            try
                set bounds of front window to {{0, 25, scrW, scrH}}
                set index of front window to 1
            end try
        end tell
        '''
        try:
            subprocess.run(["osascript", "-e", applescript],
                           capture_output=True, text=True, timeout=10)
        except Exception:
            subprocess.Popen(["open", url])

    return json.dumps({
        "url": url,
        "port": port,
        "host": host,
        "opened": open_browser,
        "status": "serving",
    })


if __name__ == "__main__":
    print(start_control_center(port=7777, open_browser=True))
    # Keep the main thread alive so the daemon uvicorn thread keeps serving
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass
