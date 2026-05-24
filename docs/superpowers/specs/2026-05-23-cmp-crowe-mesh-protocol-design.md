# CMP, Crowe Mesh Protocol (crowe-stream v1), Design

Date: 2026-05-23
Status: Design (Phase B of the Super-App Runtime; Phase 2 of the Agent Mesh)
Supersedes/extends: `docs/protocols/crowe-stream-v0.md`
Related: `2026-05-23-crowe-super-app-runtime-design.md` (D1: CMP-over-WS is the sync authority), `2026-05-23-crowe-agent-mesh-design.md` (names the `@crowelogic/mesh-protocol` package).

## Summary

CMP is one typed, versioned event + attach protocol that unifies the three event-stream formats Crowe already runs, and adds the multi-surface attach layer none of them have. It is published as `@crowelogic/mesh-protocol` (TypeScript types + Python types kept in lockstep) in the `~/Projects/crowe-logic-shared/` workspace. It is the canonical successor to crowe-stream-v0.

The three formats today (verified 2026-05-23):
- **Foundry crowe-stream v0** (`cli/headless.py`, `control_plane/streaming.py`): `ready/token/reasoning/tool/spinner/segment_end/done/error`. Discriminator `type`, snake_case. `tool` is a single completion event (no started event, no `tool_call_id`).
- **Cortex CSEP** (`crowe-cortex/src/csep/events.ts`): a richer superset (session.start/end, thinking.begin/delta/end, answer.delta/boundary, tool.invoke/progress/result, telemetry.tick, variant.swap, cache.hit, memory.touch, error.surface). Discriminator `type`, snake_case, `seq` issued by the Rust adapter. Much of it is synthesized by `crowe_logic.rs` from the thinner foundry stream.
- **Crowe Terminal agent events** (`pkg/agent/events/hub.go`): a tool-call lifecycle bus only (`call_started/call_completed/command_proposed`, plus reserved `command_approved/rejected/tool_stream`). Discriminator `kind`, lowercase-no-underscore JSON, `ts` (unix ms). No text/reasoning/session concepts.

## Canonical decisions

**C1. Discriminator = `type`; casing = `snake_case`.** Matches foundry + CSEP + crowe-stream-v0 (2 of 3, and the existing authoritative doc). Crowe Terminal's Go side keeps its internal lowercase-no-underscore `Event` struct and maps to CMP at its boundary (the agenthttp adapter), so its house rule is unaffected internally.

**C2. Every event carries `seq` (monotonic per session) and `ts` (unix ms).** `seq` enables ordering, resume, and gap detection. Today only CSEP has `seq` (adapter-issued) and only Terminal has `ts`; CMP requires both on every event, issued by the runtime (not a client adapter).

**C3. Tool lifecycle is explicit: `tool.started`, optional `tool.progress`, `tool.result`, all sharing one end-to-end `tool_call_id`.** This is the biggest emitter change: foundry currently emits only a completion `tool` event. CMP requires foundry to emit `tool.started {tool_call_id, name, args}` BEFORE invoking the tool (it already has name+args from the model's function call, and the OpenAI `tool_calls[].id` is the natural `tool_call_id`), then `tool.result {tool_call_id, status, result, duration_ms}` after. This removes the synthesis hack Cortex's adapter does today and gives every surface a correlatable lifecycle.

**C4. `args` is a parsed object, `result` is a string-or-object.** Foundry currently ships `args` as a raw JSON string; CMP parses it (the model's arguments are JSON). `result` stays flexible (string or object), truncation policy documented per event.

**C5. Canonical field names:** text delta = `delta` (not CSEP's `text_chunk`); reasoning delta = `delta` on `reasoning.delta`. Error = `{code, message, recoverable}` (merge foundry's `kind`->`code` and CSEP's `veil_message`->`message`; `message` is display-safe by contract). These collapse the divergences found in the mapping.

## Canonical event taxonomy

In-turn stream events (the agent producing a turn):

| `type` | fields (beyond `seq`, `ts`, `session_id`, `surface_id?`) | replaces |
|---|---|---|
| `ready` | `session_id`, `model_tier` | foundry `ready` |
| `token` | `delta` | foundry `token`, CSEP `answer.delta` |
| `reasoning.delta` | `delta`, `reasoning_id` | foundry `reasoning`, CSEP `thinking.delta` |
| `reasoning.begin` / `reasoning.end` | `reasoning_id` | CSEP `thinking.begin/end` (now first-class, not synthesized) |
| `segment_end` | `reason: "segment" \| "round"` | foundry `segment_end`, CSEP `answer.boundary` |
| `status` | `label: string \| null` | foundry `spinner`, CSEP `agent.status` |
| `tool.started` | `tool_call_id`, `name`, `args: object` | (new in foundry) Terminal `call_started`, CSEP `tool.invoke` |
| `tool.progress` | `tool_call_id`, `message`, `fraction: number \| null` | CSEP `tool.progress`, Terminal reserved `tool_stream` |
| `tool.result` | `tool_call_id`, `status: "ok" \| "fail"`, `result`, `duration_ms` | foundry `tool`, Terminal `call_completed`, CSEP `tool.result` |
| `command.proposed` | `tool_call_id`, `block_id`, `command` | Terminal `command_proposed` |
| `command.approved` / `command.rejected` | `tool_call_id`, `block_id` | Terminal reserved kinds (now wired) |
| `error` | `code`, `message`, `recoverable: bool` | foundry `error`, CSEP `error.surface` |
| `done` | `tokens`, `reasoning_tokens`, `elapsed_ms`, `ttft_ms` | foundry `done` |

Out-of-band/telemetry events (optional, surface may ignore): `telemetry.tick`, `variant.swap`, `cache.hit`, `memory.touch` (carried verbatim from CSEP, snake_case).

## Multi-surface attach layer (the part nobody has)

Session/control frames, distinct from in-turn events:

| frame | direction | fields | purpose |
|---|---|---|---|
| `attach` | surface -> runtime | `session_id?`, `surface_type`, `resume_after_seq?`, auth | join a session (or create one); optional resume cursor |
| `attach_ack` | runtime -> surface | `session_id`, `surface_id`, `attached_at`, `last_seq` | confirm + tell the surface where the stream is |
| `surface_joined` / `surface_left` | runtime -> all | `session_id`, `surface_id`, `surface_type` | presence broadcast |
| `resume` | implicit via `attach.resume_after_seq` | replay buffered events with `seq > after_seq` | reconnect without loss |
| `ping` / `pong` | both | `ts` | heartbeat / keep-alive |
| `events_dropped` | runtime -> surface | `count`, `since_seq` | backpressure honesty (Terminal's Hub drops on slow consumers today, silently) |

Semantics:
- **Session identity:** the runtime (foundry `control_plane`) issues a globally unique `session_id`, namespaced per workspace/user. Surfaces never mint it (CSEP hardcodes `"cortex"` today; that goes away).
- **Durable replay:** the runtime keeps a per-session ring buffer keyed by `seq`; `attach.resume_after_seq` replays the gap. Buffer size + overflow policy documented; on overflow the surface gets `events_dropped` and should refetch state.
- **Multiplexing:** every in-turn event may carry `surface_id`/`lane` so a multi-pane host routes deltas to the right pane without guessing (the crowe-stream-v0 "Known gaps" dual-pane item).

## Transport

WebSocket to the per-user runtime, hosted by foundry `control_plane` (per super-app open-decision #4: the runtime is the authority). Frame = a single CMP JSON object per WS message. The existing SSE `/v1/chat/completions` stream stays for plain OpenAI-compat clients; CMP-over-WS is the rich, multi-surface, resumable channel. A thin shim can project CMP down to the v0 SSE shape for legacy consumers during migration.

## Package shape

`@crowelogic/mesh-protocol` in `~/Projects/crowe-logic-shared/` (npm workspace):
- `src/events.ts` (TS discriminated union, the canonical types) + `src/frames.ts` (attach/control).
- Python mirror `crowe_mesh_protocol/` (dataclasses/TypedDicts) generated from or checked against the TS source via a parity test (round-trip fixtures encode/decode identically in both languages).
- Cortex imports it, retiring the hand-duplicated `src/csep/events.ts`. Foundry imports the Python side. Crowe Terminal's Go adapter maps its `Event` struct to CMP JSON at the agenthttp boundary.

## Phasing (Phase B sub-steps, each gets its own implementation plan)

- **B1:** the `@crowelogic/mesh-protocol` package: canonical event + frame types (TS + Python), parity fixtures. No behavior change yet.
- **B2:** foundry emits CMP from the runtime: extend the crowe-stream renderer to the canonical taxonomy, including the new `tool.started` (C3) with end-to-end `tool_call_id`, and runtime-issued `seq`.
- **B3:** the attach server on `control_plane`: WS endpoint, session issuance, ring buffer + resume, presence, heartbeat, backpressure.
- **B4:** Cortex migrates: import the package, drop `csep/events.ts`, attach over CMP-WS, render presence/resume.
- **B5:** Crowe Terminal Go bridge: map the agent event Hub onto CMP at the agenthttp boundary; wire the reserved `command.approved/rejected`.

## Testing strategy

- Parity (B1): the same fixture encodes/decodes identically in TS and Python; a CMP event validated by both type sets.
- Emitter (B2): a foundry turn emits a well-formed CMP sequence; `tool.started` precedes `tool.result` with a matching `tool_call_id`; `seq` is monotonic.
- Attach (B3): two surfaces attach to one session and converge on identical state after a turn; a surface that disconnects and re-attaches with `resume_after_seq` receives exactly the missed events; overflow yields `events_dropped`.
- Migration (B4/B5): Cortex on CMP renders a turn identically to its pre-migration CSEP rendering; the Terminal bridge maps a `call_started`/`call_completed` pair to `tool.started`/`tool.result`.

## Decisions for review

1. C1 casing: confirm snake_case canonical (Terminal Go maps at its boundary) vs forcing Terminal's lowercase-no-underscore everywhere.
2. C3 tool lifecycle: confirm foundry will emit a `tool.started` before invocation (a real emitter change) vs keeping completion-only and having surfaces synthesize the start.
3. Buffer/overflow policy for resume (B3): ring-buffer size and whether overflow forces a full state refetch.
4. Package location confirmed as `~/Projects/crowe-logic-shared/` per the mesh spec.
