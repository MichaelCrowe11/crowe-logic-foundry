"""Mesh-visibility endpoints. Expose the in-process tool/surface registry that
`crowe-logic tools` reads, so external consumers (the cla mesh console) can see
the mesh over HTTP instead of only in-process."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(prefix="/mesh", tags=["mesh"])

CMP_VERSION = "crowe-stream-v1"


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
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "ping":
                await ws.send_json({"type": "pong", "ts": msg.get("ts", 0)})
            elif msg.get("type") == "detach":
                await ws.close()
                return
    except WebSocketDisconnect:
        return
