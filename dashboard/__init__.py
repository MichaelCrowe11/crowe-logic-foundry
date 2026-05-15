"""
Crowe Logic Foundry — Dashboard.

Serves the web dashboard for workspace management, analytics, domain tools,
and Substrate album production.
Mounts as a FastAPI sub-app at /dashboard.
"""

import json
import os
import asyncio
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

router = APIRouter(tags=["Dashboard"])

STATIC_DIR = Path(__file__).parent / "static"


def _dashboard_config(request: Request) -> dict[str, str]:
    host = request.url.hostname or "localhost"
    scheme = request.url.scheme or "http"
    return {
        "api_base": str(request.base_url).rstrip("/"),
        "session_router_url": os.environ.get("CROWE_SESSION_ROUTER_URL", f"{scheme}://{host}:3001"),
        "ide_url": os.environ.get("CROWE_IDE_URL", f"{scheme}://{host}:10000"),
    }


@router.get("/dashboard/config")
def dashboard_config(request: Request):
    """Expose same-host dashboard configuration for local and remote deploys."""
    return _dashboard_config(request)


@router.get("/dashboard/service-health")
async def dashboard_service_health(request: Request):
    """Check IDE-adjacent service health server-side to avoid browser CORS issues."""
    cfg = _dashboard_config(request)
    checks = {"api": True, "router": False, "ide": False}
    health_urls = {
        "router": f"{cfg['session_router_url'].rstrip('/')}/health",
        "ide": f"{cfg['ide_url'].rstrip('/')}/healthz",
    }

    async with httpx.AsyncClient(timeout=2.0, follow_redirects=True) as client:
        for name, url in health_urls.items():
            try:
                response = await client.get(url)
                checks[name] = response.is_success
            except httpx.HTTPError:
                checks[name] = False

    return {**cfg, "checks": checks}


# ───────────────────────────────────────────────
# Substrate Album Studio Endpoints
# ───────────────────────────────────────────────

@router.get("/dashboard/substrate/tracks")
def substrate_tracks():
    """List all 8 Substrate tracks with builder and render status."""
    from tools.substrate import substrate_list_tracks
    return JSONResponse(content=json.loads(substrate_list_tracks()))


@router.get("/dashboard/substrate/status")
def substrate_status():
    """Check render status for all tracks (file sizes, stem counts)."""
    from tools.substrate import substrate_render_status
    return JSONResponse(content=json.loads(substrate_render_status()))


@router.get("/dashboard/substrate/vocals")
def substrate_vocals():
    """Check vocal clip inventory per track."""
    from tools.substrate import substrate_vocal_status
    return JSONResponse(content=json.loads(substrate_vocal_status()))


@router.post("/dashboard/substrate/render/{track}")
def substrate_render_track(
    track: str,
    instrumental: bool = Query(default=True, description="Skip vocal generation"),
):
    """Render a single track. Returns immediately with job metadata."""
    from tools.substrate import substrate_render_track as _render
    result = _render(track, instrumental=instrumental)
    return JSONResponse(content=json.loads(result))


@router.post("/dashboard/substrate/render-album")
def substrate_render_album(
    instrumental: bool = Query(default=True, description="Skip vocal generation"),
):
    """Render all 8 tracks sequentially. Returns immediately with job metadata."""
    from tools.substrate import substrate_render_album as _render_all
    result = _render_all(instrumental=instrumental)
    return JSONResponse(content=json.loads(result))


@router.post("/dashboard/substrate/mix/{track}")
def substrate_mix(
    track: str,
    vocal_volume_db: float = Query(default=-6.0, description="Vocal level dB"),
):
    """Mix vocal clips into an instrumental master."""
    from tools.substrate import substrate_mix_vocals as _mix
    result = _mix(track, vocal_volume_db=vocal_volume_db)
    return JSONResponse(content=json.loads(result))


@router.post("/dashboard/substrate/open/{track}")
def substrate_open(track: str):
    """Open a rendered track in the default audio player."""
    from tools.substrate import substrate_open_track as _open
    result = _open(track)
    return JSONResponse(content=json.loads(result))


@router.get("/dashboard/substrate/dna")
def substrate_dna():
    """Display the Substrate DNA creative grammar spec."""
    from tools.substrate import substrate_dna as _dna
    dna_text = _dna()
    try:
        parsed = json.loads(dna_text)
        return JSONResponse(content=parsed)
    except json.JSONDecodeError:
        return JSONResponse(content={"dna_markdown": dna_text})


@router.get("/dashboard/substrate/stream")
async def substrate_stream():
    """Server-sent events for real-time render progress."""
    async def event_generator():
        yield "data: {\"type\": \"connected\", \"message\": \"Substrate SSE stream active\"}\n\n"
        while True:
            await asyncio.sleep(5)
            yield "data: {\"type\": \"heartbeat\", \"ts\": " + str(int(asyncio.get_event_loop().time())) + "}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ───────────────────────────────────────────────
# Talon Composition Studio — Ableton-style DAW-lite
# ───────────────────────────────────────────────

@router.get("/dashboard/composer/grooves")
def composer_grooves():
    """List available groove profiles."""
    from tools.talon_music import talon_list_grooves
    return JSONResponse(content={"grooves": talon_list_grooves()})


@router.get("/dashboard/composer/emotions")
def composer_emotions():
    """List available emotion presets."""
    from tools.talon_music import talon_list_emotions
    return JSONResponse(content={"emotions": talon_list_emotions()})


@router.post("/dashboard/composer/generate/chords")
def composer_generate_chords(
    root: str = Query(default="A"),
    scale: str = Query(default="minor"),
    bars: int = Query(default=8),
    tempo: int = Query(default=85),
    groove: str = Query(default="swing"),
    style: str = Query(default=""),
):
    """Generate chord progression MIDI."""
    from tools.talon_music import talon_generate_chords
    result = talon_generate_chords(root=root, scale=scale, bars=bars, tempo=tempo, groove=groove, style=style)
    try:
        return JSONResponse(content=json.loads(result))
    except json.JSONDecodeError:
        return JSONResponse(content={"raw": result})


@router.post("/dashboard/composer/generate/drums")
def composer_generate_drums(
    genre: str = Query(default="breakbeat"),
    bars: int = Query(default=8),
    tempo: int = Query(default=85),
    groove: str = Query(default="swing"),
):
    """Generate drum pattern MIDI."""
    from tools.talon_music import talon_generate_drums
    result = talon_generate_drums(genre=genre, bars=bars, tempo=tempo, groove=groove)
    try:
        return JSONResponse(content=json.loads(result))
    except json.JSONDecodeError:
        return JSONResponse(content={"raw": result})


@router.post("/dashboard/composer/generate/melody")
def composer_generate_melody(
    root: str = Query(default="A"),
    scale: str = Query(default="minor"),
    bars: int = Query(default=8),
    tempo: int = Query(default=85),
    density: float = Query(default=0.5),
    groove: str = Query(default="floyd"),
):
    """Generate melody line MIDI."""
    from tools.talon_music import talon_generate_melody
    result = talon_generate_melody(root=root, scale=scale, bars=bars, tempo=tempo, density=density, groove=groove)
    try:
        return JSONResponse(content=json.loads(result))
    except json.JSONDecodeError:
        return JSONResponse(content={"raw": result})


@router.post("/dashboard/composer/generate/quantum-melody")
def composer_quantum_melody(
    key: str = Query(default="Am"),
    style: str = Query(default="miles"),
    notes: int = Query(default=16),
):
    """Generate quantum probability-driven melody."""
    from tools.talon_music import talon_quantum_melody
    result = talon_quantum_melody(key=key, style=style, notes=notes)
    try:
        return JSONResponse(content=json.loads(result))
    except json.JSONDecodeError:
        return JSONResponse(content={"raw": result})


@router.post("/dashboard/composer/generate/quantum-chord")
def composer_quantum_chord(
    key: str = Query(default="Am"),
    tension: float = Query(default=0.5),
):
    """Generate quantum superposition chord voicing."""
    from tools.talon_music import talon_quantum_chord
    result = talon_quantum_chord(key=key, tension=tension)
    try:
        return JSONResponse(content=json.loads(result))
    except json.JSONDecodeError:
        return JSONResponse(content={"raw": result})


@router.post("/dashboard/composer/generate/emotion")
def composer_emotion(
    emotion: str = Query(default="nostalgia"),
    key: str = Query(default="Am"),
    bars: int = Query(default=16),
    tempo: int = Query(default=0),
):
    """Compose from emotion preset."""
    from tools.talon_music import talon_compose_emotion
    result = talon_compose_emotion(emotion=emotion, key=key, bars=bars, tempo=tempo)
    try:
        return JSONResponse(content=json.loads(result))
    except json.JSONDecodeError:
        return JSONResponse(content={"raw": result})


@router.post("/dashboard/composer/generate/full")
def composer_full(
    title: str = Query(default="composition"),
    root: str = Query(default="A"),
    scale: str = Query(default="minor"),
    bars: int = Query(default=32),
    tempo: int = Query(default=85),
    sections: str = Query(default="intro,theme,solo,bridge,outro"),
    groove: str = Query(default="swing"),
    drum_genre: str = Query(default="breakbeat"),
    melody_density: float = Query(default=0.5),
):
    """Generate full multi-section composition with stems."""
    from tools.talon_music import talon_full_composition
    result = talon_full_composition(
        title=title, root=root, scale=scale, bars=bars, tempo=tempo,
        sections=sections, groove=groove, drum_genre=drum_genre,
        melody_density=melody_density,
    )
    try:
        return JSONResponse(content=json.loads(result))
    except json.JSONDecodeError:
        return JSONResponse(content={"raw": result})


@router.post("/dashboard/composer/analyze")
def composer_analyze(path: str = Query(..., description="Path to MIDI or audio file")):
    """Analyze a MIDI or audio file for musical properties."""
    from tools.talon_music import talon_analyze
    result = talon_analyze(path)
    try:
        return JSONResponse(content=json.loads(result))
    except json.JSONDecodeError:
        return JSONResponse(content={"raw": result})


@router.get("/dashboard/composer", response_class=HTMLResponse)
def serve_composer():
    """Serve the Talon Composition Studio (Ableton-style DAW-lite)."""
    html_path = STATIC_DIR / "composer.html"
    return HTMLResponse(html_path.read_text())


@router.get("/dashboard/substrate", response_class=HTMLResponse)
def serve_substrate_studio():
    """Serve the Talon Studio production dashboard."""
    html_path = STATIC_DIR / "substrate.html"
    return HTMLResponse(html_path.read_text())


# ───────────────────────────────────────────────
# Catch-all: must be last so specific routes win
# ───────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
def serve_dashboard_root():
    """Serve the single-page dashboard application."""
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text())
