# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio | proprietary, private repository.

"""
Mobile signaling server | Phase 2 of the Studio mobile plan.

Accepts WebRTC peer connections from any source that presents a valid
bearer token. Each peer becomes a new Studio camera feed, recorded to
disk via aiortc's MediaRecorder so the existing render + route pipeline
keeps operating unchanged.

Endpoints (all require Authorization: Bearer <STUDIO_SIGNALING_TOKEN>
except /healthz):

    GET    /healthz                    liveness, no auth
    POST   /session                    create a recording session
    POST   /session/{sid}/offer        SDP offer -> SDP answer
    POST   /session/{sid}/ice          trickle ICE candidate
    DELETE /session/{sid}              stop recording, close PC
    GET    /session/{sid}              status, tracks, output path
    GET    /sessions                   list active sessions

Output layout:

    $CAPTURE_ROOT/sessions/<session_id>/
        camera-<name>.mp4              one recording per peer
        manifest.json                  session metadata

Run:

    .venv/bin/python -m tools.mobile_signaling

Env:

    STUDIO_SIGNALING_TOKEN    required bearer token (from ~/.env.secrets)
    STUDIO_SIGNALING_HOST     default 0.0.0.0
    STUDIO_SIGNALING_PORT     default 8787
    CAPTURE_ROOT              default /tmp/crowe-capture
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from aiortc import RTCIceCandidate, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRecorder
from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


# ────────────────────────────────────────────────────────────────────────
# Config

TOKEN = os.environ.get("STUDIO_SIGNALING_TOKEN")
HOST = os.environ.get("STUDIO_SIGNALING_HOST", "0.0.0.0")
PORT = int(os.environ.get("STUDIO_SIGNALING_PORT", "8787"))
CAPTURE_ROOT = Path(os.environ.get("CAPTURE_ROOT", "/tmp/crowe-capture"))
SESSIONS_DIR = CAPTURE_ROOT / "sessions"
STATIC_DIR = Path(__file__).parent.parent / "dashboard" / "static"


# ────────────────────────────────────────────────────────────────────────
# Session state

@dataclass
class Session:
    id: str
    pc: RTCPeerConnection
    recorder: MediaRecorder | None
    created_at: float
    out_dir: Path
    camera_name: str = "default"
    tracks: list[str] = field(default_factory=list)

    def manifest(self) -> dict:
        return {
            "session_id": self.id,
            "camera_name": self.camera_name,
            "created_at": self.created_at,
            "out_dir": str(self.out_dir),
            "tracks": self.tracks,
            "pc_connection_state": self.pc.connectionState if self.pc else None,
        }


SESSIONS: dict[str, Session] = {}


# ────────────────────────────────────────────────────────────────────────
# Auth

security = HTTPBearer(auto_error=False)


def require_token(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> None:
    if not TOKEN:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="STUDIO_SIGNALING_TOKEN not configured on the server",
        )
    if credentials is None or credentials.credentials != TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
        )


# ────────────────────────────────────────────────────────────────────────
# App lifecycle

@asynccontextmanager
async def lifespan(app: FastAPI):
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    yield
    # Best-effort cleanup on shutdown.
    for sid, session in list(SESSIONS.items()):
        try:
            await session.pc.close()
            if session.recorder:
                await session.recorder.stop()
        except Exception:
            pass
        SESSIONS.pop(sid, None)


app = FastAPI(title="Crowe Studio Mobile Signaling", lifespan=lifespan)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ────────────────────────────────────────────────────────────────────────
# Schemas

class CreateSessionRequest(BaseModel):
    camera_name: str = Field(default="default", pattern=r"^[A-Za-z0-9_-]{1,40}$")


class OfferRequest(BaseModel):
    sdp: str
    type: str = Field(pattern=r"^(offer|answer)$")


class IceRequest(BaseModel):
    candidate: str
    sdpMid: str | None = None
    sdpMLineIndex: int | None = None


# ────────────────────────────────────────────────────────────────────────
# Routes

@app.get("/healthz")
async def healthz():
    return {
        "ok": True,
        "active_sessions": len(SESSIONS),
        "token_configured": bool(TOKEN),
        "capture_root": str(CAPTURE_ROOT),
    }


@app.get("/", response_class=FileResponse)
async def index():
    """Serve the browser test page without auth. The token lives in the
    URL query string (?t=) so the client JS can read it and attach a
    Bearer header to the actual API calls. The HTML itself is static
    and carries no secrets, so guarding it would only break browser
    access (browsers cannot set Authorization headers from the URL bar).
    """
    test_page = STATIC_DIR / "mobile_signaling_test.html"
    if not test_page.exists():
        raise HTTPException(404, "test page not installed")
    return FileResponse(test_page)


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


@app.post("/session", dependencies=[Depends(require_token)])
async def create_session(req: CreateSessionRequest):
    sid = uuid.uuid4().hex[:12]
    out_dir = SESSIONS_DIR / sid
    out_dir.mkdir(parents=True, exist_ok=True)

    pc = RTCPeerConnection()
    out_file = str(out_dir / f"camera-{req.camera_name}.mp4")
    recorder = MediaRecorder(out_file)

    session = Session(
        id=sid,
        pc=pc,
        recorder=recorder,
        created_at=time.time(),
        out_dir=out_dir,
        camera_name=req.camera_name,
    )

    @pc.on("track")
    def on_track(track):
        session.tracks.append(track.kind)
        recorder.addTrack(track)

        @track.on("ended")
        async def on_ended():
            await recorder.stop()

    @pc.on("connectionstatechange")
    async def on_state():
        if pc.connectionState in ("failed", "closed"):
            try:
                await recorder.stop()
            except Exception:
                pass

    SESSIONS[sid] = session

    # Persist manifest early so operators can see sessions as they start.
    (out_dir / "manifest.json").write_text(json.dumps(session.manifest(), indent=2) + "\n")
    return {"session_id": sid, "out_dir": str(out_dir)}


@app.post("/session/{sid}/offer", dependencies=[Depends(require_token)])
async def session_offer(sid: str, req: OfferRequest):
    session = SESSIONS.get(sid)
    if not session:
        raise HTTPException(404, "session not found")

    offer = RTCSessionDescription(sdp=req.sdp, type=req.type)
    await session.pc.setRemoteDescription(offer)

    # Start recorder before generating answer so tracks are ready to write.
    if session.recorder:
        await session.recorder.start()

    answer = await session.pc.createAnswer()
    await session.pc.setLocalDescription(answer)

    return {
        "sdp": session.pc.localDescription.sdp,
        "type": session.pc.localDescription.type,
    }


def _parse_ice(candidate_line: str) -> dict:
    """Parse a 'candidate:...' line into the kwargs aiortc needs."""
    parts = candidate_line.replace("candidate:", "").split()
    if len(parts) < 8:
        raise ValueError(f"malformed candidate: {candidate_line!r}")
    return {
        "foundation": parts[0],
        "component": int(parts[1]),
        "protocol": parts[2].lower(),
        "priority": int(parts[3]),
        "ip": parts[4],
        "port": int(parts[5]),
        "type": parts[7],
    }


@app.post("/session/{sid}/ice", dependencies=[Depends(require_token)])
async def session_ice(sid: str, req: IceRequest):
    session = SESSIONS.get(sid)
    if not session:
        raise HTTPException(404, "session not found")

    if not req.candidate:
        return {"ok": True, "note": "end-of-candidates"}

    kwargs = _parse_ice(req.candidate)
    ice = RTCIceCandidate(
        sdpMid=req.sdpMid,
        sdpMLineIndex=req.sdpMLineIndex,
        **kwargs,
    )
    await session.pc.addIceCandidate(ice)
    return {"ok": True}


@app.get("/session/{sid}", dependencies=[Depends(require_token)])
async def session_status(sid: str):
    session = SESSIONS.get(sid)
    if not session:
        raise HTTPException(404, "session not found")
    return session.manifest()


@app.get("/sessions", dependencies=[Depends(require_token)])
async def list_sessions():
    return {"sessions": [s.manifest() for s in SESSIONS.values()]}


@app.delete("/session/{sid}", dependencies=[Depends(require_token)])
async def end_session(sid: str):
    session = SESSIONS.pop(sid, None)
    if not session:
        raise HTTPException(404, "session not found")
    try:
        if session.recorder:
            await session.recorder.stop()
        await session.pc.close()
    except Exception as exc:
        raise HTTPException(500, f"cleanup failed: {exc}")
    (session.out_dir / "manifest.json").write_text(
        json.dumps({**session.manifest(), "ended_at": time.time()}, indent=2) + "\n"
    )
    return {"ok": True, "session_id": sid}


# ────────────────────────────────────────────────────────────────────────

def main():
    import uvicorn
    uvicorn.run(
        "tools.mobile_signaling:app",
        host=HOST,
        port=PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
