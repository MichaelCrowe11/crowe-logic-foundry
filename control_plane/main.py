"""
Crowe Logic Foundry. Control Plane entrypoint.

    uvicorn control_plane.main:app --host 0.0.0.0 --port 8001

Domain / knowledge / dashboard routers are loaded defensively: if any one
of them fails to import (missing optional dependency, misconfigured
service, etc.) we skip it and log the reason so the control plane can
still serve /health, auth, billing, and the public pricing endpoint.
Launch-critical endpoints live in control_plane/__init__.py and must not
depend on these optional routers.
"""

from __future__ import annotations

import logging
import sys

from control_plane import app  # noqa: F401  (re-export the FastAPI app)
from control_plane.gateway import router as gateway_router, openai_router
from control_plane.billing import router as billing_router
from control_plane.web import router as web_router
from control_plane.chat_history import router as chat_history_router
from control_plane.kb_search import router as kb_router
from control_plane.db import lifespan

logger = logging.getLogger("control_plane.main")
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# Core routers (launch-critical).
app.include_router(gateway_router)
app.include_router(openai_router)
app.include_router(billing_router)
app.include_router(web_router)
app.include_router(chat_history_router)
app.include_router(kb_router)


# Optional routers: if any one of these blows up at import-time we still
# want the core control plane to boot so customers can sign up and pay.
def _try_include(label: str, loader):
    try:
        router = loader()
        app.include_router(router)
        logger.info("router loaded: %s", label)
    except Exception as exc:  # noqa: BLE001 - intentional: surface + continue
        logger.warning("skipping router %s: %s", label, exc)


_try_include(
    "domain.mycology", lambda: __import__("domain.mycology", fromlist=["router"]).router
)
_try_include(
    "domain.vision", lambda: __import__("domain.vision", fromlist=["router"]).router
)
_try_include(
    "domain.research", lambda: __import__("domain.research", fromlist=["router"]).router
)
_try_include(
    "domain.compound", lambda: __import__("domain.compound", fromlist=["router"]).router
)
_try_include(
    "knowledge.search",
    lambda: __import__("knowledge.search", fromlist=["router"]).router,
)
_try_include("dashboard", lambda: __import__("dashboard", fromlist=["router"]).router)

# Wire up DB pool lifecycle. If the DB is unreachable at boot, the pool
# init will fail and the app will exit. that is desirable, because the
# core auth + billing endpoints cannot function without it.
app.router.lifespan_context = lifespan
