"""
Crowe Logic Foundry — Control Plane entrypoint.

    uvicorn control_plane.main:app --host 0.0.0.0 --port 8001
"""

from control_plane import app  # noqa: re-export the FastAPI app
from control_plane.gateway import router as gateway_router
from control_plane.billing import router as billing_router
from control_plane.db import lifespan

# Domain modules
from domain.mycology import router as mycology_router
from domain.vision import router as vision_router
from domain.research import router as research_router
from domain.compound import router as compound_router

# Knowledge plane
from knowledge.search import router as knowledge_router

# Attach routers
app.include_router(gateway_router)
app.include_router(billing_router)
app.include_router(mycology_router)
app.include_router(vision_router)
app.include_router(research_router)
app.include_router(compound_router)
app.include_router(knowledge_router)

# Wire up DB pool lifecycle
app.router.lifespan_context = lifespan
