"""Mesh-visibility endpoints. Expose the in-process tool/surface registry that
`crowe-logic tools` reads, so external consumers (the cla mesh console) can see
the mesh over HTTP instead of only in-process."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .gateway import CROWE_STREAM_ENABLED, _resolve_api_key
from .mesh_bus import get_bus

router = APIRouter(prefix="/mesh", tags=["mesh"])

CMP_VERSION = "crowe-stream-v1"


class MeshStreamRequest(BaseModel):
    messages: list[dict]
    model: str = "auto"
    session_id: str | None = None


def _surface_for(name: str) -> str:
    # ct_* tools are injected from the :8012 terminal bridge (tools/crowe_terminal.py).
    return "terminal" if name.startswith("ct_") else "foundry-runtime"


@router.get("/tools")
def list_tools() -> list[dict]:
    from tools import user_functions

    out: list[dict] = []
    for func in sorted(user_functions, key=lambda f: f.__name__):
        out.append(
            {
                "name": func.__name__,
                "description": (func.__doc__ or "").strip().split("\n")[0],
                "surface": _surface_for(func.__name__),
            }
        )
    return out


def _terminal_surface() -> dict:
    """Probe the :8012 terminal bridge without raising if it is down."""
    reachable = False
    tool_count = 0
    try:
        from tools import user_functions

        tool_count = sum(1 for f in user_functions if f.__name__.startswith("ct_"))
        reachable = tool_count > 0
    except Exception:
        pass
    return {
        "id": "terminal",
        "kind": "editor",
        "reachable": reachable,
        "tool_count": tool_count,
        "cmp_version": CMP_VERSION,
    }


@router.get("/surfaces")
def list_surfaces() -> list[dict]:
    from tools import user_functions

    native = sum(1 for f in user_functions if not f.__name__.startswith("ct_"))
    return [
        {
            "id": "foundry-runtime",
            "kind": "runtime",
            "reachable": True,
            "tool_count": native,
            "cmp_version": CMP_VERSION,
        },
        _terminal_surface(),
    ]


@router.websocket("/attach")
async def attach(ws: WebSocket) -> None:
    """CMP attach server (mesh B3): handshake + presence + heartbeat."""
    await ws.accept()
    try:
        frame = await ws.receive_json()
        if frame.get("type") != "attach":
            await ws.close(code=4400)
            return
        session_id = frame.get("session_id", "")
        surface_id = frame.get("surface_id", "unknown")
        await ws.send_json(
            {"type": "attach_ack", "session_id": session_id, "cmp_version": CMP_VERSION}
        )
        await ws.send_json(
            {
                "type": "surface_joined",
                "session_id": session_id,
                "surface_id": surface_id,
            }
        )

        # Forward broadcast events for this session (B3). Runs alongside the
        # control loop; cancelled on disconnect. Bus failure degrades to
        # handshake/heartbeat only.
        async def _forward() -> None:
            try:
                async for event in get_bus().subscribe(session_id):
                    await ws.send_json(event)
            except Exception:  # noqa: BLE001 — never crash the socket on bus issues
                return

        forwarder = asyncio.create_task(_forward())
        try:
            while True:
                msg = await ws.receive_json()
                if msg.get("type") == "ping":
                    await ws.send_json({"type": "pong", "ts": msg.get("ts", 0)})
                elif msg.get("type") == "detach":
                    return
        finally:
            forwarder.cancel()
    except WebSocketDisconnect:
        return


@router.post("/stream")
async def mesh_stream(
    req: MeshStreamRequest,
    key_info: dict = Depends(_resolve_api_key),
) -> StreamingResponse:
    """CMP-native streaming turn (mesh B2).

    Runs one agent turn, translates each crowe-stream v0 event into canonical
    CMP v1, frames it as SSE for the caller, AND publishes it to the session's
    broadcast bus (B3) so attached surfaces receive the same stream.

    Behind CROWE_STREAM_ENABLED. NOTE: plan-gating and usage recording present
    on /chat/stream are NOT yet mirrored here — this is a dogfood/operator path
    (cla). Gating + billing parity is a required follow-up before broad exposure.
    """
    if not CROWE_STREAM_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Streaming endpoint is disabled (set CROWE_STREAM_ENABLED=1)",
        )

    from .cmp_translate import CmpTranslator
    from .streaming import sse_frame, stream_agent_events

    messages = req.messages or []
    if not messages or messages[-1].get("role") != "user":
        raise HTTPException(
            status_code=400, detail="messages must end with a user turn"
        )

    session_id = req.session_id or f"http-{key_info['workspace_id'][:12]}"
    translator = CmpTranslator(session_id=session_id, model_tier=req.model)
    bus = get_bus()

    async def _sse() -> AsyncIterator[str]:
        async for v0 in stream_agent_events(
            messages=messages, model_id=req.model, session_id=session_id
        ):
            for ev in translator.translate(v0):
                await bus.publish(session_id, ev)
                yield sse_frame(ev)

    return StreamingResponse(
        _sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
