# Crowe Agent Mesh — Architecture Design

Date: 2026-05-23
Status: Design (approved direction; pending spec review)
Scope: Full mesh (Phases 0–6), with Phase 1 broken out for immediate implementation.

## Summary

This spec reframes "build a Crowe terminal superior to iTerm2" into the problem that
actually limits Crowe Logic today: the agent system is fractured across three surfaces
that cannot share tools or speak one protocol. The terminal emulator is already a
non-issue — every surface renders with xterm.js behind a thin, swappable boundary. The
asset nobody else has, and the thing currently broken, is a **unified agent mesh**: one
runtime (Foundry) that every surface (Terminal, Cortex, Code, CLI) plugs into as a node
that both *consumes* agent turns and *contributes* its local tools back up.

iTerm2 does not compete in this category. This is where "stay years ahead" lives.

## Background: what the codebase actually is (2026-05-23)

Findings from a deep trace of `crowe-terminal`, `crowe-logic-foundry`, and `crowe-cortex`.
These correct several stale claims in project memory.

### The terminal core is already swappable (no VT parser to build)
- `crowe-terminal` renders the terminal with `@xterm/xterm ^6.0.0`, isolated entirely in
  `frontend/app/view/term/termwrap.ts` (`TermWrap` is the only file importing `@xterm/*`).
  PTY bytes flow in via `doTerminalWrite(Uint8Array)`; input flows out via
  `RpcApi.ControllerInputCommand`.
- `crowe-cortex` renders with the same `@xterm/xterm 6.0.0` + `@xterm/addon-fit 0.11.0`
  in `src/components/TerminalTab.tsx`, with PTY over Tauri `invoke`/`listen`.
- Owning a clean-room VT parser/grid renderer buys nothing competitive and costs perpetual
  maintenance. The terminal stays a swappable dependency behind an interface.

### The agent system is fractured into three disconnected halves
1. **Crowe Terminal's own Go agent registry** — `crowe-terminal/pkg/agent/`, HTTP on
   `127.0.0.1:8012` (`pkg/agent/transport/agenthttp/server.go`). Exposes `GET /v1/tools`,
   `POST /v1/call` (X-AuthKey gated), `GET /v1/events`. Tools self-register via `init()`.
   Surface-aware tools live here: `editor.get_active_context`
   (`pkg/agent/tools/editor/editor_active_context.go` + `editorctx` package),
   `editor.read_file/write_file/apply_edit`, `terminal.exec_safe`
   (`pkg/agent/tools/terminal/exec_safe.go`), `terminal.propose_command`, `system.metrics`,
   web/applescript/MCP proxies. Per-block scope grants in `pkg/agent/scope/`.
2. **Crowe Logic Foundry's Python agent** (the brain) — the real runtime. The agent loop is
   `BaseOpenAIProvider.stream_response` in `providers/_shared.py`; the live tool catalog is
   the hand-curated `user_functions` set in `tools/__init__.py` (~112 tools). Model routing,
   virtual tiers (Auto/Supreme/Apex/Titan/Oracle), and `classify_task`/`route_for_auto` live
   in `config/agent_config.py` (the `_BASE_MODEL_CHAIN`). The control plane FastAPI app
   (`control_plane/main.py`) exposes `/v1/chat/completions` (`control_plane/gateway.py`,
   `openai_router`) and `/api/kb/*` (`control_plane/kb_search.py`).
3. **They never meet.** Crowe Terminal's AI panel POSTs to Foundry for *inference* (via a
   `waveai@crowelm-auto` provider config → `/api/post-chat-message` → `aiusechat`), but
   Foundry's Python agent loop cannot see the terminal's Go tools, and the Go registry
   cannot see Foundry's 112. Cortex bridges to Foundry by shell-spawning
   `crowe-logic headless` (`crowe-cortex/src-tauri/src/crowe_logic.rs`) and hand-duplicates
   the event types (`crowe-cortex/src/csep/events.ts` mirrors Foundry's streaming events).

### The inter-agent layer exists but is unmerged and not wired into the runtime
- **AICL is real, on an unmerged branch.** `crowe_synapse_engine/aicl/` (`acts.py`,
  `conversation.py`, `messages.py`) + `docs/AICL_SPEC.md` exist at commit `cfd0859`, currently
  living on **`origin/quality-stack`**, not on `main` and not on the working branch. It is a
  v0.1 agent-to-agent semantic protocol: immutable (`frozen=True`) messages of shape
  *act · sender · subject · evidence · confidence · parent-pointer*, append-only JSONL, FIPA
  ACL/KQML lineage. **This is agent↔agent semantics, distinct from the surface↔brain transport
  wire CMP provides — the two are complementary layers, not competitors.** (Corrected
  2026-05-23: an earlier draft wrongly called AICL absent; it is on `quality-stack`.)
- **`config/crowelm/rebrand_map.py` DOES exist** on `main` (`REBRAND_MAP`, `is_leaky_label`,
  `display_label`, `unmapped_leaky_names`, import-time `_self_check`). It is the leaky-label →
  CroweLM display map, a *different thing* from the `_BASE_MODEL_CHAIN` virtual-tier routing in
  `config/agent_config.py`. Both exist. (Corrected 2026-05-23: an earlier draft wrongly called
  this file absent.)
- `crowe_synapse_engine/orchestrator.py` does keyword-based routing only. Even with AICL's
  message types defined, there is **no live message bus, no pub/sub, no runtime inter-agent
  exchange** on `main`. `azure_agent_invoke` (`tools/azure_agent.py`) is the closest live proxy
  — unidirectional, synchronous, requires an Azure-side agent ID.
- The agent loop is **synchronous and single-threaded per turn**; tools run sequentially.

### The integration seam already exists but is unplugged
- `tools/crowe_terminal.py` in Foundry is designed to **discover and inject a surface's
  local tools into the agent at runtime**: `discover_and_register()` fetches a tool list from
  a known local endpoint, wraps each as a proxy, mutates `tools.user_functions` in place, and
  busts the `_TOOL_CACHE` keyed by `id(user_functions)`. Its `system_prompt()` addendum is
  pulled into `cli/session_runtime.py:build_runtime_system_instructions`. **The server side
  the proxies call was never implemented.** The wire is drawn; nothing is plugged in.

### Substrate divergence is real (deliberately deferred to Phase 6)
- Cortex = Tauri 2 / Rust (`portable-pty`, `rusqlite`, React 19 + Zustand + Vite 7).
- Terminal = Electron 41 / Go (`creack/pty`, `wstore` SQLite, React 19 + Jotai + Tailwind v4).
- Full convergence onto one runtime is a migration, not a refactor. A shared
  `@crowelogic/terminal-pane` is ~1 day; convergence is a later, data-informed decision.

## Goals

- One typed protocol every surface and Foundry speak (retire hand-duplicated event types).
- Any surface's local tools (editor context, terminal exec, filesystem, problems) callable
  by Foundry's agent loop — the "unfracturing."
- Shared terminal-pane component across Electron and Tauri behind a `PtyBridge` interface.
- Foundry's agent loop able to execute tools in parallel and route turns to multiple surfaces.
- A clear, data-informed substrate decision at the end, not as a speculative up-front bet.

## Non-goals

- Building a clean-room VT parser / terminal emulator. (xterm.js stays.)
- Provable clean-room re-derivation of the Wave fork. Apache-2.0 permits commercial closed
  shipping with attribution; ownership comes from strangler-fig surgery (Phase 4), and the
  defensible original IP is the mesh/agent layer, built fresh here.
- Reinventing an inter-agent semantic language. CMP is the minimal transport wire (turns +
  tool calls + presence, surface↔brain). The FIPA-ACL/KQML-lineage agent↔agent semantics
  already exist as **AICL** on `origin/quality-stack`; Phase 5 reconciles and layers AICL over
  CMP rather than building a parallel scheme.
- Resurrecting the dead `tools/registry.py` `@tool` decorator path. The live catalog is the
  `user_functions` set in `tools/__init__.py`; new tools register there.

## Architecture

```
                    ┌─────────────────────────────────────┐
                    │   Crowe Logic Foundry (the brain)    │
                    │   agent loop · 112 tools · model      │
                    │   chain · virtual tiers · KB/RAG      │
                    │   control_plane /v1 · headless        │
                    └──────────────┬──────────────────────┘
                                   │  @crowelogic/agent-protocol
                                   │  (turns ↓ · tool-calls ↓ · results ↑ · presence)
              ┌────────────────────┼────────────────────┐
              │                    │                     │
        ┌─────┴─────┐        ┌─────┴─────┐         ┌─────┴─────┐
        │  Terminal │        │  Cortex   │         │   Code    │
        │ (Electron)│        │  (Tauri)  │         │ (= Term)  │
        │  Go :8012 │        │  Rust     │         │           │
        └─────┬─────┘        └─────┬─────┘         └─────┬─────┘
              └────────── local tools registered UP ─────┘
        editor.get_active_context · terminal.exec_safe · fs · problems
```

Three load-bearing components.

### Component 1 — Crowe Mesh Protocol (CMP), `@crowelogic/mesh-protocol` (the wire)
**Crowe Mesh Protocol (CMP)** is a new neutral name — deliberately broader than Cortex's
CSEP, and not owned by any one surface. One typed, versioned package defining: event stream
types (`ready`, `token`, `reasoning`, `tool`, `segment_end`, `done`, `error`), tool descriptor
schema, tool-call request/result frames, and a presence/registration handshake. TS surfaces
(Cortex, Terminal, Code) import it; Foundry has the canonical Python definitions generated from
/ kept in lockstep with it. CSEP becomes a legacy alias that maps onto CMP during migration.
Replaces: Cortex's `src/csep/events.ts` hand-mirror, Terminal's bespoke `/v1/events` shapes,
and the non-standard `crowe_tool_result` sidecar in `control_plane/streaming.py`.

### Component 2 — `@crowelogic/terminal-pane` (the shared surface)
Extract the xterm wrapper once. Parameterize behind a `PtyBridge` interface
`{ spawn, write, resize, kill, onData, onExit, onEof }` and an `onSnip?(lines)` callback.
Two bridge impls: Tauri (`invoke`/`listen`) and Electron/Go (`wshrpc`). Shared theme tokens
move into the package (today Cortex copies Terminal's palette in a comment).

### Component 3 — the tool-contribution server (the unfracturing)
Implement the local HTTP contract that Foundry's `tools/crowe_terminal.py` already discovers.
Crowe Terminal already runs the right server on `:8012` with `editor.get_active_context`,
`terminal.exec_safe`, etc. — Phase 1 makes Foundry's agent discover and call it, so the agent
in any surface can see and act on *that surface's* live editor and terminal.

## Phases

| Phase | Name | Ships | Rationale |
|---|---|---|---|
| 0 | Truth-up | Correct the spec's verified errors; decide the AICL↔CMP layering and whether to merge `origin/quality-stack`'s AICL to `main`; name the protocol (done: CMP). | Downstream assumes an accurate map. ~½ day. |
| 1 | Tool-contribution wire | Foundry agent discovers + calls Crowe Terminal's `:8012` tools (`editor.get_active_context`, `terminal.exec_safe`). End-to-end on one surface. | The unfracturing. Smallest change, biggest capability jump. **Broken out below.** |
| 2 | `@crowelogic/mesh-protocol` (CMP) | One typed protocol package; Cortex + Terminal import it; retire duplicated CSEP types and the `crowe_tool_result` sidecar. | Stops divergence before it worsens. |
| 3 | `@crowelogic/terminal-pane` | Shared xterm component + `PtyBridge`; Cortex + Terminal consume it. | Single terminal source of truth (the right version of Phase D). |
| 4 | Strangler surgery | Behind `WshRpcInterface`, replace `wstore`/`filestore`/`blockcontroller` with Crowe-owned Go, each step a release. | Ownership surgery on a stable boundary, mesh already proven. |
| 5 | Mesh maturity | Parallel/async tool execution in `stream_response`; multi-surface presence; **AICL (merged from `quality-stack`) becomes the live agent↔agent semantic layer over CMP transport**, promoting agent-to-agent calls from `azure_agent_invoke` to first-class, auditable AICL messages. | The real "years ahead" capability, built on solid ground. CMP carries the bytes; AICL carries the meaning. |
| 6 | Substrate decision | Decide Electron→Tauri convergence (or not) with data from Phases 1–5. | Deliberately deferred; it's a migration, and you'll know far more by then. |

## Phase 1 — broken out for immediate implementation

**Objective:** Foundry's Python agent loop can call Crowe Terminal's local, surface-aware
tools, so an agent turn issued in (or routed to) the terminal can read the live editor
context and run a read-only command in that terminal's environment.

### Current state (verified)
- Crowe Terminal already serves tools at `127.0.0.1:8012`:
  `GET /v1/tools` (catalog with JSON schema), `POST /v1/call` (X-AuthKey gated),
  `GET /v1/events` (WS lifecycle). Source: `crowe-terminal/pkg/agent/transport/agenthttp/server.go`,
  registry in `pkg/agent/registry/registry.go`.
- Foundry has `tools/crowe_terminal.py` with `discover_and_register()`, per-tool proxy
  generation, in-place `user_functions` mutation, `_TOOL_CACHE` busting, and `system_prompt()`.
  The endpoint contract and auth wiring on the Foundry side are incomplete.

### Work
1. **Define the discovery contract** (in `@crowelogic/mesh-protocol` (CMP) draft, or inline for
   Phase 1 then migrate in Phase 2): tool descriptor shape returned by `GET /v1/tools`, the
   `POST /v1/call` request/response envelope, and the `X-AuthKey` handshake. **Auth key
   sharing (decided): Terminal writes the `X-AuthKey` to a token file under `~/.crowe-logic/`
   (chmod 600); Foundry reads it.** Matches the existing `~/.crowe-logic/` state convention and
   survives independent app launches (an env var would not).
2. **Complete `tools/crowe_terminal.py`:** point `discover_and_register()` at `:8012`
   (configurable; gated behind an env flag such as `CROWE_AGENT_TOOLS=1`), implement the auth
   header, map each remote descriptor to a Python proxy that POSTs to `/v1/call` and returns
   the result, handle Terminal-not-running gracefully (no-op, no crash).
3. **Wire registration into the loop:** ensure injected tools land in `user_functions` before
   `build_tool_schemas` runs for a turn, and that `system_prompt()` addendum is included
   (already pulled in `cli/session_runtime.py:build_runtime_system_instructions`).
4. **Scope/safety:** respect Terminal's existing `pkg/agent/scope/` grants; `terminal.exec_safe`
   stays read-only/denylist-gated as implemented; no auto-Enter on `propose_command`.
5. **Prove end-to-end:** with Terminal running and `CROWE_AGENT_TOOLS=1`, a
   `crowe-logic run` / headless turn calls `editor.get_active_context` and gets the live
   cursor/selection snapshot; calls `terminal.exec_safe` and gets command output.

### Phase 1 acceptance criteria
- `GET /v1/tools` on `:8012` is consumed by Foundry; injected tools appear in
  `crowe-logic tools list` when the flag is on and Terminal is running.
- An agent turn successfully calls `editor.get_active_context` and receives a real snapshot
  reflecting the focused Crowe Code editor.
- An agent turn successfully calls `terminal.exec_safe` and receives real output.
- With Terminal not running or the flag off, behavior is unchanged (no tools injected, no
  errors). Existing tests pass; new tests cover discovery success, Terminal-absent no-op, and
  auth rejection.

### Phase 1 risks / open questions
- **`id(user_functions)` cache key** — confirm in-place mutation + cache-bust works for every
  consumer (CLI, headless, control_plane provider path), not just the CLI.
- **Sync, single-threaded loop** — Phase 1 calls remote tools sequentially over HTTP; acceptable
  now, motivates Phase 5 parallelism.
- **Two registries coexist** — Phase 1 does not merge them; Foundry proxies into Terminal's Go
  registry. Consolidation (if ever) is post-Phase-4.

## Testing strategy (mesh-wide)
- Protocol package (Phase 2): typed contract tests; round-trip encode/decode parity between
  Python and TS fixtures.
- Tool-contribution (Phase 1): unit tests for proxy generation + cache busting; integration
  test with a stub `:8012` server; absent-Terminal no-op test; auth-rejection test.
- Terminal-pane (Phase 3): bridge-interface conformance tests for both Tauri and Electron impls.
- Surgery (Phase 4): each replaced backend component ships behind `WshRpcInterface` with parity
  tests against the prior implementation; release-gated.

## Resolved decisions (2026-05-23)
1. **Protocol name:** new neutral name — **Crowe Mesh Protocol (CMP)**, package
   `@crowelogic/mesh-protocol`. CSEP becomes a legacy alias mapped onto CMP.
2. **Shared-package home:** a new npm workspace at **`~/Projects/crowe-logic-shared/`** holding
   all `@crowelogic/*` packages. Phase 1 inlines the contract; Phase 2 extracts it into the
   workspace.
3. **Phase 1 auth handoff:** **token file under `~/.crowe-logic/`** (chmod 600), written by
   Terminal and read by Foundry.
4. **Memory corrections:** investigated during this design pass and **reversed** — the AICL and
   `doctor-and-rebrand-sot` memory entries were *accurate*; an earlier draft of this spec was
   wrong. Verified via git: AICL lives on `origin/quality-stack` (commit `cfd0859`, unmerged to
   `main`), and `config/crowelm/rebrand_map.py` exists on `main`. The spec — not the memory —
   was corrected. The `aicl` memory gets a one-line note that it is unmerged on `quality-stack`.
