# Mesh B2 (native CMP emission) + B3 broadcast — Design

**Date:** 2026-05-24
**Repo:** `crowe-logic-foundry` (branch continues `feat/mesh-visibility-endpoints` → new branch `feat/mesh-cmp-stream-broadcast`)
**Status:** Approved (decisions locked below), pre-implementation
**Builds on:** PR #40 (mesh-visibility endpoints + WS attach handshake). Mesh north-star items **B2** + **B3 broadcast**.

## Problem

After PR #40 the mesh has visibility (`/mesh/tools`, `/mesh/surfaces`) and an attach handshake (`WS /mesh/attach` → `attach_ack` + presence + heartbeat). But two gaps remain:

- **B2:** Foundry's chat path emits **crowe-stream v0** (`control_plane/streaming.py`), not canonical **CMP v1**. v0 lacks `session_id` on every event, has no `tool.started`/`tool.result` split, and uses ad-hoc shapes. CMP v1 (`crowe_mesh_protocol`) is the canonical wire format the mesh is standardizing on.
- **B3 broadcast:** `/mesh/attach` only echoes the connecting client's own presence. When a turn actually runs for a session, attached clients receive nothing — there is no fan-out from the producer to the session's subscribers.

## Decisions (locked)

1. **B2 surface:** a **new `/mesh/stream` endpoint** emitting CMP-native SSE. `/chat/stream` stays frozen on v0 (backward-compatible for existing consumers: `cla run`, headless, OpenAI-compat).
2. **B3 bus:** **Redis-backed pub/sub** (multi-worker durable), not in-process. Redis is **new infrastructure** for Foundry (not currently a dependency).

## Architecture

The single event chokepoint is `stream_agent_events()` (its docstring already anticipates "in-process fan-out"). Both features attach there.

```
stream_agent_events(messages, model_id, session_id)  → v0 dicts
        │
        ▼
CmpTranslator(session_id, model_tier).translate(v0)  → [CMP event dicts]   ← B2
        │
        ├──────────────► SSE to the caller                 (GET/POST /mesh/stream)
        └──────────────► MeshBus.publish(session_id, ev)    ← B3
                                  │  (Redis PUBLISH mesh:session:<id>)
                                  ▼
        WS /mesh/attach ── MeshBus.subscribe(session_id) ──► attached cla clients
                                  (bounded consumer queue; events_dropped on overflow)
```

### Unit 1 — `control_plane/cmp_translate.py` (B2, pure, no I/O)

`CmpTranslator(session_id: str, model_tier: str = "auto")` with `translate(v0: dict) -> list[dict]`. Holds per-turn state (reasoning id, tool-call counter). Mapping from the v0 vocabulary (verified in `streaming.py`):

| v0 event | fields | → CMP event(s) |
|---|---|---|
| `ready` | — | `ready` (+ `model_tier`) |
| `token` | `delta` | `token` (`delta`) |
| `reasoning` | `delta` | `reasoning.delta` (`reasoning_id`, `delta`) |
| `spinner` | `label` | `status` (`label`) |
| `segment_end` | — | `segment_end` (`reason="segment"`) |
| `tool` | `name,args,status,result,duration_ms` | `tool.started`(`tool_call_id`,`name`) **+** `tool.result`(`tool_call_id`,`status`) |
| `done` | `tokens,reasoning_tokens,elapsed_ms,ttft_ms` | `done` (same) |
| `error` | `message,kind` | `error` (`code=kind`, `message`, `recoverable=False`) |

Every emitted event carries `session_id`. `tool_call_id` is synthesized stably as `f"{name}-{n}"` (v0 carries none). Note: v0 fires the tool card once *after* execution, so the `tool.started` here is synthesized alongside `tool.result` rather than truly pre-invoke; genuine pre-invoke timing is a deeper provider hook (future, not this cycle). Each emitted dict validates against `crowe_mesh_protocol.CMP_EVENT_TYPES`.

### Unit 2 — `control_plane/mesh_bus.py` (B3 engine, Redis)

`MeshBus` wrapping `redis.asyncio`. Channel convention `mesh:session:<session_id>`.

- `async publish(session_id, event: dict) -> None` — `PUBLISH` JSON. On `RedisError`/connection failure: log once + no-op (**graceful degradation** — a turn must never fail because the broadcast bus is down).
- `async subscribe(session_id) -> AsyncIterator[dict]` — subscribe the channel, decode JSON, yield. On connection failure: the iterator ends cleanly (degrade), logged. **Backpressure note:** this cycle relies on Redis's own connection-level buffering; an explicit bounded consumer queue that synthesizes `events_dropped` on slow-consumer overflow is deferred (the CMP `events_dropped` frame exists and `cla` already renders it, but the producer does not synthesize it yet — untested-code avoidance).
- `async ping() -> bool` — used by `/mesh/surfaces` + `cla doctor` to report bus health.
- Module-level singleton `get_bus()` reading `REDIS_URL` (default `redis://localhost:6379/0`).

### Unit 3 — endpoints (`control_plane/mesh.py`)

- **`POST /mesh/stream`** — body `{messages, model, session_id}`. Runs `stream_agent_events`, pipes each v0 event through `CmpTranslator`, and for each CMP event: (a) `yield sse_frame(ev)` to the caller, (b) `await bus.publish(session_id, ev)`. Auth: same resolver as `/chat/stream`.
- **`WS /mesh/attach`** (extend existing) — after the handshake, spawn a task iterating `bus.subscribe(session_id)` and forwarding each event to the socket, concurrently with the existing ping/pong/detach loop. Clean teardown on disconnect.

### Config + deps

- `requirements-control-plane.txt`: add `redis>=5.0`. Dev/test: `fakeredis` (already used for hermetic tests).
- `REDIS_URL` env (default `redis://localhost:6379/0`). Local: `docker run -p 6379:6379 redis` (OrbStack).
- `/mesh/surfaces` gains a `bus` pseudo-surface (or a `bus_reachable` flag) so `cla doctor` shows Redis health.

## Testing

- **`tests/test_cmp_translate.py`** — pure unit tests over every v0→CMP mapping; assert `session_id` present and types ∈ `CMP_EVENT_TYPES`; tool event yields the started+result pair with a stable `tool_call_id`.
- **`tests/test_mesh_bus.py`** — `fakeredis.aioredis` backend: publish→subscribe round-trip; session isolation (s2 not seen by s1); `publish` no-ops (no raise) when the client raises.
- **`tests/test_mesh_endpoints.py`** (extend) — `/mesh/stream` with `stream_agent_events` monkeypatched to a canned v0 sequence asserts CMP-shaped SSE frames; `/mesh/attach` with an injected fakeredis bus receives an event published mid-session.

## Risks / notes

- **Redis is new infra.** Everything degrades gracefully without it: `/mesh/stream` still streams to the caller (publish no-ops), `/mesh/attach` still does handshake/presence (subscribe ends clean), `cla doctor` reports the bus down. No hard dependency for launch-critical paths.
- **`tool.started` is synthesized post-hoc** this cycle (v0 limitation). True pre-invoke emission needs a provider-level hook — tracked as a follow-up, out of scope.
- **`cla` side:** no change required for `cla attach` to show live turns (it forwards whatever the WS sends). Optional small add: `cla run --cmp` to hit `/mesh/stream` natively — deferred unless wanted.
