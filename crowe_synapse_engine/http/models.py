"""Pydantic request/response models for the Crowe-Synapse HTTP service.

These models live alongside ``server.py`` so the FastAPI app stays a thin
HTTP wrapper over the runtime layer. The schemas mirror the dataclasses in
``agent_registry`` and ``runtime.base`` rather than re-deriving them, so a
client only needs to know one shape per resource.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentSummary(BaseModel):
    """One row of ``GET /agents``. Mirrors the CLI ``synapse list`` output."""

    name: str
    description: str = ""
    model: str
    cluster: str | None = None
    tools: list[str] = Field(default_factory=list)
    runtime: str | None = None
    alias_of: str | None = None


class RunRequest(BaseModel):
    """Body of ``POST /run``.

    Either ``agent_name`` (resolved against the ``agents/`` directory) or
    ``agent_path`` (a ``.synapse-agent`` / ``.yaml`` file path) is required.
    ``runtime_hint`` mirrors the CLI flag and forces a specific runtime.
    """

    agent_name: str | None = None
    agent_path: str | None = None
    prompt: str
    max_turns: int = 20
    runtime_hint: str | None = None
    thread_id: str = "http"


class ChunkPayload(BaseModel):
    """JSON shape of a single SSE event from ``POST /run``.

    Matches ``RuntimeChunk`` field-for-field so a TypeScript client can
    discriminate on ``kind`` and pull the relevant fields without guessing.
    """

    kind: str
    text: str = ""
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: str | None = None
    reason: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
