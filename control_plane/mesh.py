"""Mesh-visibility endpoints. Expose the in-process tool/surface registry that
`crowe-logic tools` reads, so external consumers (the cla mesh console) can see
the mesh over HTTP instead of only in-process."""

from __future__ import annotations

from fastapi import APIRouter

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
