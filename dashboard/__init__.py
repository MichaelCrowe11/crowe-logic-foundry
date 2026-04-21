"""
Crowe Logic Foundry — Dashboard.

Serves the web dashboard for workspace management, analytics, and domain tools.
Mounts as a FastAPI sub-app at /dashboard.
"""

import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

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


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
@router.get("/dashboard/{path:path}", response_class=HTMLResponse)
def serve_dashboard(path: str = ""):
    """Serve the single-page dashboard application."""
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text())
