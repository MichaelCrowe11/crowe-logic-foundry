# Agentic Gateway — Tools for Signed-In Sessions — Design

- **Date:** 2026-06-21
- **Repo:** `crowe-logic-foundry`
- **Branch:** _(pending — cut `feat/agentic-gateway` from current)_
- **Status:** Approved (design); pending implementation plan
- **Scope:** Make the foundry gateway path agentic so signed-in Crowe ID users get
  the full tool loop. The local-mode workaround (`CROWE_LOGIC_LOCAL=1`) and the
  tool-registry import-guard hardening shipped 2026-06-21 as the immediate unblock;
  this spec is the durable fix.

## Problem

A signed-in `crowe-logic` session never uses tools. Every turn shows `TOOLS 0`,
`TOTAL 0 tok`, keeps no history between turns, and only ever chats — e.g. "run a
comprehensive compound discovery workflow" returns a request for inputs instead of
executing anything, and "yes please begin" → "What would you like me to begin?"
(no memory of the prior turn).

### Root cause

Two execution paths exist per turn (`cli/crowe_logic.py`):

1. **Local path** (`provider.stream_response(...)`, ~line 1977+). Loads tool schemas
   (`load_tools()`), runs the tool-execution loop, counts tokens. Fully agentic.
2. **Gateway path** (`_gateway_chat` → `gateway_client.chat`, ~lines 1911–1950 for
   interactive, ~3358–3395 for `run()`). A **stateless one-shot chat proxy**:

   ```python
   body = {"model": model, "messages": messages}   # gateway_client.chat
   # called as: messages=[{"role": "user", "content": user_input}]
   # response → console.print(Markdown(resp.get("content", "")))
   ```

   No `tools`, no `tool_choice`, no history, no system prompt, **no client-side tool
   loop**. The entire agentic system lives only in path (1).

When a user is logged in and `CROWE_LOGIC_LOCAL` is unset, path (2) runs for every
turn. Therefore signed-in users — the default — are structurally tool-less.

A secondary defect compounded it: the tool registry could not even import
(`tools/deepparallel.py` hard-imported `tenacity`, which was undeclared and missing),
so the local fallback would have errored too. Fixed 2026-06-21 (declared `tenacity`;
`tools/__init__.py` now registers each module under a guard so one missing optional
dep skips only its tools instead of nuking all 113).

## Key constraint (drives the whole design)

Most crowe-logic tools act on **the user's machine**: `execute_shell`, `filesystem`,
`git_*`, `iterm_*`, `browser`/playwright, `capture`. The gateway server
(`api.crowelogic.com`) **cannot** execute these. So the agentic loop **must** run
client-side; the gateway's only new job is to proxy the LLM round-trip with tools
passed through and `tool_calls` returned. Server-side tool execution is a non-goal.

This is the same split the local providers already implement — only the LLM HTTP
call moves from a direct provider endpoint to the gateway.

## Decisions (from brainstorming)

- **Approach:** add a **`gateway` provider** that implements the existing provider
  interface (`add_user_message` / `stream_response(console, render_tool_card,
  session_state, _get_orchestrator)`) but routes its model calls through
  `gateway_client`. It reuses the shared tool-loop in `providers/_shared.py`; only
  the transport differs. This slots into the `provider == "..."` dispatch in
  `cli/crowe_logic.py` with no special-casing of the gateway path.
- **Tool execution stays local.** Client receives `tool_calls`, executes via the
  `load_tools()` name→fn map, appends `role:"tool"` results, re-POSTs. Loop until a
  final answer or the tool-round budget (`providers/_shared.py` budget warnings) trips.
- **History is client-owned.** The orchestrator (`_get_orchestrator`/`orch.prepare`)
  already assembles context for the local path; the gateway provider sends the same
  `messages` array (system + history + tool turns), not a single user message.
- **No local provider keys.** The gateway holds provider keys server-side; that
  invariant (and the "no local cascade for signed-in users" rule) is preserved — the
  client sends tool *definitions* and executes tools, but never holds an LLM key.
- **`CROWE_LOGIC_LOCAL=1` stays** as the explicit local-keys escape hatch and the
  fallback when the Crowe ID session is inactive.
- **Autonomy levels still apply.** `load_tools()` already filters schemas by the
  active autonomy level; the gateway provider sends the filtered set, so restricted
  levels hide write/shell tools at the gateway boundary too.
- **Anonymous free tier stays chat-only.** The device-token path
  (`crowelm-mycelium`) remains a plain chat call — no tools — to bound abuse and cost.

## Architecture

### Client (`crowe-logic-foundry`)

1. **`providers/gateway.py`** — `GatewayProvider`:
   - `__init__(model_cfg, system_instructions)` — store model id + system prompt.
   - Build messages: system + orchestrator history + running tool turns.
   - `stream_response(...)`: loop —
     1. `gateway_client.chat(model, messages, tools=tool_schemas, tool_choice="auto")`
     2. if response has `tool_calls`: render cards, execute locally via `tool_map`,
        append results to `messages`, bump `session_state["tool_count"]`, repeat.
     3. else: render `content`, record token usage from the response, return.
   - Honor the soft/hard tool-round budget helpers in `_shared.py`.
2. **`cli/gateway_client.py`** — extend `chat()` to forward `tools`, `tool_choice`,
   and the full `messages` list, and to surface `tool_calls` + `usage` from the
   response. Keep the 401-refresh / 403-PlanDenied / 402-FreeTierCapped handling.
3. **`cli/crowe_logic.py`** — replace the inline `_gateway_chat` single-message
   blocks (interactive + `run()`) with dispatch to `GatewayProvider` for signed-in
   users, mirroring the local `provider == "..."` branches. Token + tool HUD update
   for free via the shared `session_state` plumbing.

### Server (`api.crowelogic.com` gateway — out of this repo)

`POST /api/gateway/chat` must:
- Accept `tools`, `tool_choice`, and a multi-message `messages` array.
- Forward them to the upstream provider (OpenAI-compatible tool calling).
- Return assistant `tool_calls` (OpenAI shape) when the model requests tools, and
  `usage` (prompt/completion tokens) on every response.
- Preserve plan/tier enforcement (403) and metering against the credit wallet.

## Out of scope

- Server-side tool execution (impossible for local-machine tools).
- Streaming token deltas over the gateway (start non-streaming; add SSE later —
  the gateway already speaks the Crowe Streaming Protocol elsewhere).
- Changing the anonymous free-tier behavior.

## Verification

- Signed-in `crowe-logic run "use git tools to show the last 2 commits"` fires
  `git_log` and reports non-zero `TOOLS` + `tok` (matches the local-mode result
  captured 2026-06-21: `git_log` → `execute_shell`, 84 tokens, 175 reasoning).
- Multi-turn interactive session retains context ("yes, begin" continues prior task).
- Autonomy `restricted` hides write/shell tools in the gateway request.
- `CROWE_LOGIC_LOCAL=1` and logged-out both still fall to the local path.
- Anonymous device-token path remains chat-only.

## Follow-ups

- Stream tokens over the gateway (CSP/SSE) for parity with the local renderer.
- Restore Claude-Opus-on-Azure backing once `AZURE_ANTHROPIC_*` creds exist.
- Bump `pyproject.toml` version (currently `0.4.2`) in line with PyPI/editable
  metadata (`0.5.0`) so the banner stops under-reporting.
