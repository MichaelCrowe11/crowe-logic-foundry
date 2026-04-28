"""OpenAI-compatible HTTP bridge for the Crowe Logic Foundry agent.

Exposes POST /v1/chat/completions and POST /v1/models so that any
OpenAI-compatible client (Wave Terminal's AI block, LangChain, the
official OpenAI SDK, etc.) can drive the same agent loop the IDE uses.

Translation:
  OpenAI Chat Completions request  ->  cli.headless stdin JSON
  cli.headless newline-JSON events ->  OpenAI SSE chunks

Models are advertised as ``crowelm-supreme`` and ``crowelm-prime``;
both currently route through the same MODEL_CHAIN, the label is
informational so downstream UIs can surface a friendly name.

Run:  python -m cli.openai_bridge  (defaults to 127.0.0.1:8011)
Env:  CROWE_BRIDGE_HOST, CROWE_BRIDGE_PORT
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

_FOUNDRY_ROOT = Path(__file__).resolve().parent.parent
if str(_FOUNDRY_ROOT) not in sys.path:
    sys.path.insert(0, str(_FOUNDRY_ROOT))

app = FastAPI(title="Crowe Logic OpenAI Bridge", version="0.1.0")

MODELS = [
    {"id": "crowelm-auto",    "label": "CroweLM Auto"},
    {"id": "crowelm-supreme", "label": "CroweLM Supreme"},
    {"id": "crowelm-apex",    "label": "CroweLM Apex"},
    {"id": "crowelm-titan",   "label": "CroweLM Titan"},
    {"id": "crowelm-oracle",  "label": "CroweLM Oracle"},
]


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "service": "crowe-logic-bridge"}


@app.get("/v1/models")
async def list_models() -> dict:
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": m["id"], "object": "model", "created": now, "owned_by": "crowe-logic"}
            for m in MODELS
        ],
    }


def _spawn_headless(messages: list[dict], model_id: str, session: str) -> asyncio.subprocess.Process:
    """Launch cli.headless as a child process with messages on stdin.

    The headless runner already imports the same provider stack the
    IDE uses, so the bridge stays a thin protocol shim — no agent
    logic is duplicated here.
    """
    payload = json.dumps({"messages": messages, "model": model_id, "session": session})
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(_FOUNDRY_ROOT))
    return asyncio.create_subprocess_exec(
        sys.executable, "-m", "cli.headless",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(_FOUNDRY_ROOT),
        env=env,
    ), payload


def _openai_chunk(stream_id: str, model: str, *, content: str | None = None,
                  finish_reason: str | None = None) -> str:
    delta: dict = {}
    if content is not None:
        delta["content"] = content
    chunk = {
        "id": stream_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(chunk)}\n\n"


async def _stream_chat(messages: list[dict], model_id: str, session: str) -> AsyncIterator[str]:
    coro, payload = _spawn_headless(messages, model_id, session)
    proc = await coro
    stream_id = "chatcmpl-" + uuid.uuid4().hex
    assert proc.stdin and proc.stdout
    proc.stdin.write(payload.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()

    yield _openai_chunk(stream_id, model_id, content="")  # role marker

    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        try:
            event = json.loads(line.decode("utf-8").rstrip())
        except json.JSONDecodeError:
            continue
        et = event.get("type")
        if et == "token":
            yield _openai_chunk(stream_id, model_id, content=event.get("delta", ""))
        elif et == "reasoning":
            # Reasoning deltas are surfaced as italicized prefix once at
            # turn start so the UI sees the model "thinking" without
            # cluttering the final transcript.
            pass
        elif et == "tool":
            name = event.get("name", "")
            status = event.get("status", "ok")
            yield _openai_chunk(stream_id, model_id,
                                content=f"\n\n_[tool: {name} {status}]_\n\n")
        elif et == "error":
            msg = event.get("message", "unknown error")
            yield _openai_chunk(stream_id, model_id, content=f"\n\n[error: {msg}]")
            yield _openai_chunk(stream_id, model_id, finish_reason="stop")
            yield "data: [DONE]\n\n"
            await proc.wait()
            return
        elif et == "done":
            yield _openai_chunk(stream_id, model_id, finish_reason="stop")
            yield "data: [DONE]\n\n"
            await proc.wait()
            return

    await proc.wait()
    yield _openai_chunk(stream_id, model_id, finish_reason="stop")
    yield "data: [DONE]\n\n"


async def _collect_chat(messages: list[dict], model_id: str, session: str) -> dict:
    """Non-streaming path: drain headless and return one assembled response."""
    coro, payload = _spawn_headless(messages, model_id, session)
    proc = await coro
    assert proc.stdin and proc.stdout
    proc.stdin.write(payload.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()

    text_parts: list[str] = []
    error: str | None = None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        try:
            event = json.loads(line.decode("utf-8").rstrip())
        except json.JSONDecodeError:
            continue
        et = event.get("type")
        if et == "token":
            text_parts.append(event.get("delta", ""))
        elif et == "error":
            error = event.get("message", "unknown error")
            break
        elif et == "done":
            break

    await proc.wait()
    if error:
        raise HTTPException(status_code=500, detail=error)

    content = "".join(text_parts)
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": len(content.split()),
            "total_tokens": len(content.split()),
        },
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    body = await req.json()
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="'messages' must be a non-empty array")

    normalized: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if role in ("system", "user", "assistant") and isinstance(content, str) and content.strip():
            target_role = "user" if role == "system" else role
            normalized.append({"role": target_role, "content": content})

    if not normalized or normalized[-1]["role"] != "user":
        raise HTTPException(status_code=400, detail="last message must be a user turn with content")

    model_id = body.get("model") or "crowelm-supreme"
    session = body.get("user") or "crowe-bridge"
    stream = bool(body.get("stream", False))

    if stream:
        return StreamingResponse(_stream_chat(normalized, model_id, session),
                                 media_type="text/event-stream")
    return JSONResponse(await _collect_chat(normalized, model_id, session))


def main() -> int:
    import uvicorn
    host = os.environ.get("CROWE_BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("CROWE_BRIDGE_PORT", "8011"))
    uvicorn.run("cli.openai_bridge:app", host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
