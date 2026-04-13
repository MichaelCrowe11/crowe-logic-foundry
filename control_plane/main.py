"""
Crowe Logic Foundry — Control Plane entrypoint.

    uvicorn control_plane.main:app --host 0.0.0.0 --port 8001
"""

from control_plane import app  # noqa: re-export the FastAPI app
from control_plane.gateway import router as gateway_router
from control_plane.db import lifespan

# Attach the model gateway routes
app.include_router(gateway_router)

# Wire up DB pool lifecycle
app.router.lifespan_context = lifespan
