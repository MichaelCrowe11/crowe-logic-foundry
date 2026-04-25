# crowe-stream v0

Status: draft, observed-from-code (`cli/headless.py`)
Owner: Crowe Logic, Inc.
Last updated: 2026-04-24

## Purpose

A single wire format that lets non-terminal hosts (the VS Code chat
participant, the `control_plane` HTTP gateway, the `crowe-logic-ai`
Next.js app, test runners) drive the same agent loop the CLI runs,
without parsing Rich or ANSI output.

The format is the contract `cli/headless.py` already emits. This
document captures it so additional surfaces can implement against a
stable target. Anything not described here is implementation detail of
the current renderer and is allowed to change without notice.

## Framing

### Stdio transport (current)

Newline-delimited JSON. Each event is a single JSON object on one line,
flushed immediately after writing. The host reads until it sees `done`
or `error`. Reference: `cli.headless.emit` (line 67).

### HTTP transport (planned, `control_plane/gateway.py`)

Server-Sent Events. Each event is a `data: <json>\n\n` frame carrying
the same JSON payload. The SSE `event:` field MUST equal the `type`
field of the payload, so SSE-aware clients can route on it natively.

The two transports carry the same event vocabulary; only the framing
differs. A v0-compliant host MUST handle both forms identically once
the JSON object is in hand.

## Input

A single JSON object. Currently consumed via stdin or `--input <file>`;
under HTTP it is the request body.

```json
{
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "model": "auto",
  "session": "vscode-abc123"
}
```

| Field    | Required | Notes |
|----------|----------|-------|
| messages | yes      | Non-empty array. Last entry MUST have role `user`. Tool messages on input are ignored; the provider recreates them as the agent runs tools. |
| model    | no       | `"auto"` (default) selects `MODEL_CHAIN[0]` from `config/agent_config.py`. Otherwise any `name` field from a `MODEL_CHAIN` entry. |
| session  | no       | Opaque tag used for telemetry and transcript persistence. Hosts SHOULD pass a stable id per chat thread so that `update_session_runtime` can persist the answer text against the right session row. |

## Events

All events are JSON objects with a required `type` field. Unknown
fields MUST be ignored by hosts. Unknown event types MUST be skipped
without aborting the stream.

### `ready`

Emitted exactly once before any token. Signals the provider has
connected and the host can begin rendering.

```json
{"type":"ready"}
```

### `token`

A streamed user-visible content delta. Concatenating every `token.delta`
in order yields the assistant's final answer for that turn (excluding
reasoning).

```json
{"type":"token","delta":"Hel"}
{"type":"token","delta":"lo."}
```

### `reasoning`

A streamed reasoning delta from a thinking-capable model. Hosts MAY
display these in a collapsed panel and MUST NOT conflate them with
`token`. Reasoning text MUST NOT be persisted as part of assistant
content for replay.

```json
{"type":"reasoning","delta":"considering the request..."}
```

### `tool`

Emitted once per completed tool call. The `args` field is the raw JSON
arguments string the model produced; `result` is truncated to 5000
characters in the current implementation (see
`cli.headless.json_render_tool_card`, line 181).

```json
{
  "type": "tool",
  "name": "read_file",
  "args": "{\"path\":\"README.md\"}",
  "status": "ok",
  "duration_ms": 42,
  "result": "..."
}
```

| Field       | Type    | Notes |
|-------------|---------|-------|
| name        | string  | Tool function name. |
| args        | string  | Raw JSON the model emitted. May be invalid JSON; hosts MUST NOT assume parseability. |
| status      | enum    | `ok` or `fail`. |
| duration_ms | integer | Wall time in milliseconds. |
| result      | string  | Stringified result, truncated at 5000 characters in v0. |

### `spinner`

Transient state hint. Hosts MAY render as a status line. A null label
clears the spinner.

```json
{"type":"spinner","label":"reading file..."}
{"type":"spinner","label":null}
```

### `segment_end`

Boundary between the assistant's distinct utterances inside a single
turn (typically: assistant text, tool call, assistant text again).
Hosts MAY use this to render visual separators or to commit a
transcript chunk to durable storage.

```json
{"type":"segment_end"}
```

### `done`

Terminates a successful turn. After `done`, the host MUST stop reading.

```json
{
  "type": "done",
  "tokens": 127,
  "reasoning_tokens": 340,
  "elapsed_ms": 4820,
  "ttft_ms": 612
}
```

### `error`

Terminates a failed turn. After `error`, the host MUST stop reading.

```json
{"type":"error","message":"...","kind":"runtime"}
```

`kind` values currently emitted:

| Kind       | Meaning |
|------------|---------|
| `input`    | The request payload was malformed (missing messages, wrong shape). |
| `config`   | The requested model is unreachable (missing credentials, unknown provider). |
| `provider` | The model call failed at runtime. |
| `cancelled`| The host or user interrupted the turn. |
| `runtime`  | Fallback for unclassified failures. |

## Ordering guarantees

The renderer at `cli/headless.py:JsonStreamRenderer` (line 86) emits
events in a sequence hosts MAY rely on:

1. Exactly one `ready`.
2. Zero or more rounds, each composed of:
   - Optional `spinner` (label set, then null).
   - Zero or more `reasoning` deltas.
   - Zero or more `token` deltas.
   - Zero or one `tool` per tool call.
   - One `segment_end` at the end of the round.
3. Exactly one terminator: either `done` or `error`.

A `tool` event implies the segment that produced its arguments has
ended. In practice tool calls are followed by another assistant
segment, so hosts will see additional `token` and `segment_end` events
after each `tool` until the model decides it is done.

## Known gaps (deferred to v1)

v0 is sufficient for single-pane chat surfaces but under-specifies four
use cases the foundry already supports or will soon support. Calling
them out so v0 implementations leave room:

1. **Dual-pane streams.** `cli/dual_mode.py` runs two models in
   parallel and synthesizes a merged answer. v1 will add a `pane_id`
   field to all in-turn events plus `pane_open` / `pane_close` /
   `synthesis_start` events so hosts can allocate UI per pane.
2. **Agent delegation.** `crowe_synapse_engine.Orchestrator.prepare`
   can route to a sub-agent (`mode: "delegated"`). v1 will add a
   `delegate` event carrying the agent name and rationale so hosts can
   render "handing off to the cultivation agent" affordances.
3. **Cost and usage detail.** v0 reports a single `tokens` counter on
   `done`. v1 will return `prompt_tokens`, `completion_tokens`,
   `cached_tokens`, and the resolved provider plus model id, so hosts
   can render per-turn cost and the `control_plane` gateway can record
   richer usage events.
4. **Cancellation control channel.** v0 only handles host-initiated
   cancel via signal (stdio) or HTTP disconnect. v1 should accept a
   `cancel` event from the host on a back-channel so the agent can
   abort cleanly mid-tool-call.

None of these gaps block the first three planned integrations
(`control_plane` SSE endpoint, Next.js consumer, VS Code chat
participant). v0 is what we build against now; v1 lands when the first
of these four gaps becomes load-bearing.

## Reference implementation

- Producer: `cli/headless.py` (stdio).
- Renderer interface: `cli.headless.JsonStreamRenderer` (line 86).
  Conforms to the same shape `BaseOpenAIProvider.stream_response`
  expects from any `StreamRenderer`, so swapping renderers is the only
  thing the protocol changes.
- Tool card emitter: `cli.headless.json_render_tool_card` (line 181),
  drop-in for `cli.branding.render_tool_card`.
- CLI entry: `crowe-logic headless` (`cli/crowe_logic.py:2556`).

## Compatibility

Before v1 ships, additions to v0 are allowed if they are forward-only:
a new event type, or a new optional field on an existing event. Hosts
MUST ignore unknown event types and unknown fields. Removals or
renames are breaking and bump the major version.

Producers MAY emit a `protocol` event as the very first frame in a
future minor revision to advertise capabilities; until then, hosts
SHOULD assume v0.
