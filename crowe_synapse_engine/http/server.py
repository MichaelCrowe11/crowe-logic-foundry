"""FastAPI service wrapping the synapse agent runtime.

Three endpoints:

* ``GET  /agents`` returns the agents discovered under ``agents/``.
* ``POST /run`` streams a run as Server-Sent Events. Each ``data:`` frame
  is one JSON-serialized ``RuntimeChunk``; AICL chunks are also persisted
  to the MemoryStore as they arrive. The terminal frame is ``data: [DONE]``.
* ``GET  /sessions/{session_id}/aicl`` returns the persisted AICL
  conversation as JSONL (one message per line).

The HTTP layer is a thin shell over the existing runtime. Agent loading
mirrors ``cli/synapse_cli.py`` so the two surfaces stay equivalent.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from crowe_synapse_engine.agent_registry import AgentConfig, AgentRegistry
from crowe_synapse_engine.aicl import AICLMessage
from crowe_synapse_engine.memory import MemoryStore
from crowe_synapse_engine.runtime import select_runtime
from crowe_synapse_engine.runtime.base import ChunkKind, RuntimeChunk

from crowe_synapse_engine.http.models import (
    AgentSummary,
    ChunkPayload,
    RunRequest,
)


# ── helpers ──────────────────────────────────────────────────────────────


def _agents_dir() -> str:
    """Locate the ``agents/`` directory next to the package root.

    Mirrors ``cli.synapse_cli._agents_dir`` so the HTTP and CLI surfaces
    agree on agent discovery. Honors ``CROWE_FOUNDRY_AGENTS_DIR`` for tests
    and deployments that ship a non-standard layout.
    """
    override = os.environ.get("CROWE_FOUNDRY_AGENTS_DIR")
    if override:
        return override
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor / "agents"
        if candidate.is_dir() and (ancestor / "crowe_synapse_engine").is_dir():
            return str(candidate)
    return str(Path.cwd() / "agents")


def _make_memory_store() -> MemoryStore:
    """Construct a MemoryStore using the env-configured DB path if present.

    Indirection point for tests: monkeypatch ``CROWE_SYNAPSE_MEMORY_DB`` to
    drop persistence into a tmp path without touching the user's home DB.
    """
    db_path = os.environ.get("CROWE_SYNAPSE_MEMORY_DB", "~/.crowe-logic/memory.db")
    return MemoryStore(db_path=db_path)


def _load_agent(*, agent_name: str | None, agent_path: str | None) -> AgentConfig:
    """Load an AgentConfig from a registry name or an explicit file path.

    Same resolution logic as ``cli.synapse_cli._load_agent``. ``.synapse-agent``
    source files are compiled through the DSL; ``.yaml`` files are parsed
    directly; bare names are looked up in the registry. One blocker becomes a
    400 here so the client gets a clear error.
    """
    if not agent_name and not agent_path:
        raise HTTPException(
            status_code=400,
            detail="Either 'agent_name' or 'agent_path' must be provided.",
        )

    if agent_path:
        path = Path(agent_path)
        if not path.is_file():
            raise HTTPException(
                status_code=404, detail=f"Agent file not found: {agent_path}"
            )
        if path.suffix == ".synapse-agent":
            from crowe_synapse_engine.synapse_dsl import compile_source

            compiled = compile_source(path.read_text(encoding="utf-8"))
            if not compiled:
                raise HTTPException(
                    status_code=422,
                    detail=f"No agent blocks found in {agent_path}",
                )
            return AgentConfig(**compiled[0])
        if path.suffix in (".yaml", ".yml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return AgentConfig(
                **{
                    k: v
                    for k, v in data.items()
                    if k in AgentConfig.__dataclass_fields__
                }
            )
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported agent file type: {path.suffix}",
        )

    registry = AgentRegistry(agents_dir=_agents_dir())
    agent = registry.get_agent(agent_name or "")
    if agent is None:
        known = ", ".join(a.name for a in registry.list_agents()) or "(none)"
        raise HTTPException(
            status_code=404,
            detail=f"Agent {agent_name!r} not found. Known: {known}",
        )
    return agent


def _chunk_to_payload(chunk: RuntimeChunk) -> dict[str, Any]:
    """Serialize a RuntimeChunk to a JSON-safe dict for SSE transport."""
    data = asdict(chunk)
    # ChunkKind is a str-enum; asdict yields the enum, not the string value.
    data["kind"] = chunk.kind.value
    return data


# ── app + endpoints ──────────────────────────────────────────────────────


app = FastAPI(
    title="Crowe-Synapse HTTP",
    description="HTTP wrapper around the Crowe-Synapse agent runtime.",
    version="0.1.0",
)


@app.get("/agents", response_model=list[AgentSummary])
def list_agents() -> list[AgentSummary]:
    """Return every agent the registry can discover under ``agents/``."""
    registry = AgentRegistry(agents_dir=_agents_dir())
    return [
        AgentSummary(
            name=agent.name,
            description=agent.description,
            model=agent.model,
            cluster=agent.cluster,
            tools=list(agent.tools),
            runtime=agent.runtime,
            alias_of=agent.alias_of,
        )
        for agent in sorted(registry.list_agents(), key=lambda a: a.name)
    ]


@app.post("/run")
async def run_agent(request: Request, body: RunRequest) -> StreamingResponse:
    """Run an agent and stream RuntimeChunks as Server-Sent Events.

    Each SSE frame is ``data: <json>\\n\\n`` where ``<json>`` is one
    ``ChunkPayload``. AICL chunks are persisted to MemoryStore as they
    arrive. The session id is returned in the ``X-Session-Id`` response
    header so a client can later fetch the AICL transcript.
    """
    agent = _load_agent(agent_name=body.agent_name, agent_path=body.agent_path)
    if not body.prompt.strip():
        raise HTTPException(status_code=400, detail="Empty prompt.")

    store = _make_memory_store()
    session_id = store.start_session(
        thread_id=body.thread_id, project_context=f"agent={agent.name}"
    )
    runtime = select_runtime(agent, runtime_hint=body.runtime_hint or agent.runtime)

    async def event_stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in runtime.run(
                agent_name=agent.name,
                user_prompt=body.prompt,
                system_prompt=agent.prompt_override,
                model=agent.model,
                tools=agent.tools,
                max_turns=body.max_turns,
            ):
                if await request.is_disconnected():
                    break
                if chunk.kind == ChunkKind.AICL and "aicl" in chunk.meta:
                    try:
                        msg = AICLMessage.from_dict(chunk.meta["aicl"])
                        store.record_aicl_message(session_id, msg)
                    except Exception:
                        # Persistence failure must not break the stream.
                        pass
                payload = json.dumps(_chunk_to_payload(chunk), default=str)
                yield f"data: {payload}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"
        finally:
            try:
                store.end_session(session_id, summary=f"agent={agent.name}")
            finally:
                store.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "X-Session-Id": session_id,
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/sessions/{session_id}/aicl")
def get_session_aicl(session_id: str) -> Response:
    """Return the AICL transcript for ``session_id`` as JSONL."""
    store = _make_memory_store()
    try:
        if store.get_session(session_id) is None:
            raise HTTPException(
                status_code=404, detail=f"Session {session_id!r} not found."
            )
        rows = store.get_aicl_messages(session_id)
    finally:
        store.close()

    lines: list[str] = []
    for row in rows:
        data = dict(row)
        for jcol in ("evidence", "constraints", "payload"):
            raw = data.get(jcol)
            if isinstance(raw, str):
                try:
                    data[jcol] = (
                        json.loads(raw) if raw else ({} if jcol == "payload" else [])
                    )
                except json.JSONDecodeError:
                    data[jcol] = {} if jcol == "payload" else []
        data["requires_human"] = bool(data.get("requires_human", 0))
        lines.append(json.dumps(data, default=str))
    body = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    return Response(content=body, media_type="application/x-ndjson")


__all__ = ["ChunkPayload", "app"]
