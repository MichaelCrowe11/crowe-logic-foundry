# Crowe Super-App Runtime, Architecture Design

Date: 2026-05-23
Status: Design (north-star architecture for the executing crowecode-platform build to follow)
Relationship: This is the surface/runtime layer that sits ON TOP of the Crowe Agent Mesh. See `2026-05-23-crowe-agent-mesh-design.md`. CMP (Crowe Mesh Protocol) defined there is the attach protocol used here.

## Thesis

Every super-app contender (Claude desktop, Codex, Gemini/Spark, Cursor, Chorus) is racing to the same endgame: one agent that does chat plus knowledge work plus coding, carries your skills and integrations everywhere, runs long autonomous tasks, and renders live interfaces on the side. They are all building N fragmented surfaces around a stateless agent.

Crowe inverts it: **build ONE stateful per-user agent runtime in the cloud, and make every surface (web, CLI, desktop, chat, notebook) a window into it.** Skills, memory, state, and sandbox live in the runtime, not the surface, so the "a skill I built over here does not exist over there" problem cannot occur by construction.

## The gaps nobody has unlocked (why this wins)

1. **Fake unification.** Competitors split cowork vs code vs knowledge into separate apps with separate state. No one has a single runtime where the same agent, skills, memory, and sandbox serve every surface identically.
2. **The agent is not stateful or ambient.** You go TO an app. No one has a persistent per-user runtime that web, mobile, CLI, desktop, and chat all attach to as live windows onto one session.
3. **Long-horizon execution is a prompt mode, not infrastructure.** No one offers durable, checkpointed runs that survive disconnects and run for days as first-class infra.
4. **Security is bolted on.** Competitors inject raw keys or run on your machine. Per-user microVM isolation and zero key leakage are afterthoughts.

Crowe already holds the hard primitives for all four: a per-user isolated Modal microVM runtime with `crowe-logic` preloaded, one unified agent (sub-agents, Synapse/virtual-tier routing, KB and portfolio tools) defined once in the foundry, a scoped-token gateway with zero key leakage, and the same CLI across surfaces.

## Architecture

```
                        Per-user STATEFUL runtime (source of truth)
        ┌───────────────────────────────────────────────────────────────┐
        │  Modal Sandbox (microVM)         Modal Volume (persistent)      │
        │   - crowe-logic headless          - workspace files + deps      │
        │   - sub-agents, virtual tiers     - ~/.crowe-logic/ (sessions,  │
        │   - KB + portfolio + local tools    memory.db, runtime state)   │
        │   - scoped-token gateway          - run checkpoints + audit log │
        └───────────────┬───────────────────────────┬───────────────────┘
                        │ CMP over WebSocket (attach / state deltas / tool events)
        ┌───────────────┼───────────────┬───────────┼───────────┬─────────────┐
     ┌──┴──┐        ┌────┴────┐     ┌────┴────┐  ┌───┴────┐  ┌───┴──────┐
     │ Web │        │ CLI /   │     │ Chat    │  │Notebook│  │ (future) │
     │(cc) │        │ Desktop │     │ SDK     │  │(Jupyter│  │ mobile / │
     │     │        │(Crowe   │     │(Slack,  │  │in-sbox,│  │ SMS      │
     │     │        │ Terminal)│    │ Tele,…) │  │ tunnel)│  │          │
     └─────┘        └─────────┘     └─────────┘  └────────┘  └──────────┘

        Durable runs:  Inngest orchestrates Modal Sandbox executions,
                       checkpointing into the per-user Volume (resumable for days).
        Provenance:    every agent action + tool call is a CMP event ->
                       Volume audit log + central pgvector Knowledge Lake (replayable).
```

## Components and boundaries

| Unit | What it does | Depends on |
|---|---|---|
| **Runtime core** | `crowe-logic` headless, per user, inside a Modal Sandbox. The brain: sub-agents, virtual-tier routing, tools. | foundry; Modal Sandbox |
| **Persistent workspace** | Modal Volume per user: files, installed deps, `~/.crowe-logic/` (session state, `memory.db`), run checkpoints, audit log. | Modal Volume |
| **Attach protocol** | CMP over WebSocket. A surface opens a session, receives state deltas + token/tool/reasoning events, sends turns. One protocol, every surface. | mesh CMP package |
| **Surfaces** | Web (crowecode-platform), CLI/desktop (Crowe Terminal), chat (Vercel Chat SDK: Slack/Telegram/Teams/Discord one codebase), Notebook (JupyterLab running in the per-user sandbox, exposed via Modal tunnel), later mobile/SMS. Each is a thin window. | attach protocol |
| **Durable runs** | Inngest functions orchestrate long Modal executions, checkpoint state into the Volume, resume after disconnect. | Inngest; Volume |
| **Provenance** | CMP event stream persisted to Volume audit log + KB; replay any run. | KB (pgvector); Volume |
| **Security** | Per-user microVM (Modal), scoped-token gateway (built), zero key leakage, virtual-tier model masking. | gateway; Modal isolation |

## Key architectural decisions

**D1. State sync = CMP-over-WebSocket to the runtime, NOT a reactive DB (Convex/Liveblocks).**
The runtime (foundry session state + Volume) is already the single source of truth. Adding Convex creates a SECOND authority to reconcile, which is the architectural smell the competitors accept because they lack a stateful runtime. Surfaces subscribe to the per-user runtime over CMP and render its deltas. Reserve a CRDT layer (Yjs/Liveblocks) ONLY if/when two humans concurrently co-edit one document; it is not needed for "same session on every surface."
Alternative if rejected: Convex as the sync+persistence layer, runtime writes through it. Heavier, dual source of truth.

**D2. Durable runs = Inngest orchestrating Modal, NOT Vercel Workflow.**
crowecode-platform runs on Railway, so Vercel WDK does not fit the deploy target. Inngest is host-agnostic, gives durable checkpointed steps, retries, and resume-after-disconnect, and coordinates Modal Sandbox calls cleanly. Run state checkpoints land in the per-user Volume so a resumed run rehydrates the exact workspace.
Alternative: Restate (more powerful, heavier to operate) or raw Modal `.spawn()` + polling (loses human-in-loop checkpointing ergonomics).

**D3. Persistent workspace = one Modal Volume per user, mounted by both Sandbox and Notebook.**
This is the single highest-leverage move: it turns the runtime from "a session" into "your durable cloud agent reachable from anywhere." The Volume is the shared state that makes D1 coherent. (In progress in the crowecode-platform session.)

**D4. The Notebook is a SURFACE inside the per-user Sandbox, not the backbone and not a separate hosted-notebook instance.** (Refined 2026-05-23 from the crowecode-platform implementation.)
Implementation: JupyterLab runs as a process INSIDE the same per-user Modal Sandbox as the terminal and the agent, exposed via a Modal tunnel. Terminal, notebook, and agent therefore share one sandbox and one Volume by construction, which preserves D5 more tightly than a separate hosted-notebook product would (no second compute that has to mount a shared Volume, no dependency on Modal's hosted Notebooks product semantics). It is the interactive knowledge-work and ML face onto the exact state the agent operates on, and the ideal home for the CroweLM unified-dataset + QLoRA work. The agent may author/read notebooks in the shared workspace, and the tunneled JupyterLab can be streamed into a side panel as an artifact. It is NOT the runtime and NOT the long-horizon executor (a kernel is interactive and fragile); those remain Sandbox + Inngest.

**D5. The runtime is defined ONCE in the foundry.**
Skills/sub-agents/tools/memory live in `crowe-logic`, not per surface. New surfaces are adapters over CMP; they add zero agent logic. This is the structural guarantee against fragmentation.

**D6. The Claude Agent SDK is the external/developer SDK surface, NOT a second runtime.** (Decided 2026-05-23.)
Foundry's `control_plane /v1/chat/completions` is the single gateway (OpenAI-compat, tool-federated after mesh Phase 1). A Claude Agent SDK app integrates by pointing at that gateway through a thin LiteLLM shim that exposes Crowe's virtual tiers behind an Anthropic-style endpoint (`ANTHROPIC_BASE_URL` + LiteLLM master key as `ANTHROPIC_AUTH_TOKEN`, no raw model IDs, honoring no-leakage). The `crowe-agent-lab` repo is therefore the published "build on Crowe" reference and external SDK (system-prompt-as-IP, custom tools such as `cultivation_lookup` backed by the real KB FTS query, subagents), NOT a deployed production brain.
Consequences for the in-flight agent-sdk build:
- DROP its planned standalone FastAPI `/v1` service: it duplicates `control_plane`. The SDK app consumes the gateway.
- Do NOT reimplement sessions or the tier map: Neon `chat_sessions`/`chat_messages` and the virtual-tier map already live in foundry and are reused through the gateway.
- The plan collapses to two things worth doing: (a) the starter/reference itself (prompt, KB-backed tools, subagents); (b) OPTIONALLY host a LiteLLM Anthropic-compat shim in front of `control_plane` so external Claude Agent SDK apps can run on Crowe tiers. The LiteLLM->Kimi wiring is dev/reference only.
In the architecture, the Agent SDK is not a peer surface; it is the client toolkit external developers (and Crowe's own surface teams) use to build clients of the runtime.

## Phasing (each phase later gets its own implementation plan)

- **Phase A, foundation (IN PROGRESS, crowecode-platform session):** persistent per-user Modal Volume; web + CLI attach to the same Sandbox/workspace; state survives across sessions.
- **Phase B, attach protocol:** formalize CMP-over-WS (ties to mesh Phase 2). Multiple surfaces attach to one live session with shared state.
- **Phase C, durable runs:** Inngest orchestrating Modal, checkpoint-to-Volume, resume-after-disconnect.
- **Phase D, ambient surfaces:** Vercel Chat SDK (Slack/Telegram/Teams/Discord) + SMS, all over CMP.
- **Phase E, Notebook-as-surface (partly in flight, crowecode-platform):** JupyterLab running in the per-user sandbox, exposed via Modal tunnel; shares the sandbox and Volume with the terminal and agent; folds in CroweLM dataset/QLoRA ML work.
- **Phase F, provenance and replay:** CMP event log to Volume audit + KB; replay any run; the enterprise moat.

## Testing strategy

- Attach protocol (B): contract tests for CMP frames; two surfaces attached to one session converge on identical state after a turn.
- Persistence (A): Volume survives sandbox teardown; a new sandbox rehydrates files + `~/.crowe-logic/` state; CLI and web see the same workspace.
- Durable runs (C): kill the orchestrator mid-run; on resume it continues from the last Volume checkpoint, not from zero.
- Notebooks (E): a notebook and the agent sandbox mounting the same Volume see each other's writes.
- Provenance (F): every tool call appears in the audit log; a run replays deterministically from the log.

## Decisions for review

1. D1: CMP-over-WS as the sync authority. CONFIRMED 2026-05-23 (no Convex; reserve a CRDT layer only for true concurrent co-editing).
2. D2: Inngest orchestrating Modal for durable runs, checkpointing to the per-user Volume. CONFIRMED 2026-05-23 (not Vercel WDK; crowecode-platform is on Railway).
3. RESOLVED (2026-05-23): the Notebook is JupyterLab inside the per-user sandbox via Modal tunnel (not Modal's hosted Notebooks product), so it shares the sandbox + Volume by construction and carries no external-product dependency. See D4.
4. Repo ownership: which repo hosts the CMP-over-WS attach server, foundry (control_plane) vs crowecode-platform. Recommendation: foundry control_plane, since the runtime is the authority.
