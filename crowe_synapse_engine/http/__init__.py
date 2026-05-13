"""HTTP surface for the synapse agent runtime.

Public exports: ``app`` (the FastAPI application), ``RunRequest``,
``AgentSummary``, ``ChunkPayload``. The runtime is invoked via
``crowe_synapse_engine.runtime.select_runtime`` exactly as the CLI does;
this module adds nothing to the runtime contract.
"""

from crowe_synapse_engine.http.models import (
    AgentSummary,
    ChunkPayload,
    RunRequest,
)
from crowe_synapse_engine.http.server import app

__all__ = [
    "AgentSummary",
    "ChunkPayload",
    "RunRequest",
    "app",
]
