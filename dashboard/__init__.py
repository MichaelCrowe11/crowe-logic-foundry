"""
Crowe Logic Foundry — Dashboard.

Serves the web dashboard for workspace management, analytics, and domain tools.
Mounts as a FastAPI sub-app at /dashboard.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["Dashboard"])

STATIC_DIR = Path(__file__).parent / "static"


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
@router.get("/dashboard/{path:path}", response_class=HTMLResponse)
def serve_dashboard(path: str = ""):
    """Serve the single-page dashboard application."""
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text())
