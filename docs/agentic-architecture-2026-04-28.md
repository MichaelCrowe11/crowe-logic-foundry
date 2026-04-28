# Crowe Logic Agentic Architecture — Field Notes & Innovation Vector

**Date:** 2026-04-28
**Status:** Field notes captured during the Crowe Terminal launch + crowe-portfolio knowledge plane buildout. Not a roadmap; a synthesis of what the day proved.

---

## What got built today

In two parallel sessions, the following landed end-to-end:

### Session A — Crowe Terminal (this session)
1. Forked Wave Terminal (Apache 2.0) → `MichaelCrowe11/crowe-terminal`
2. Replaced Wave green accent with Crowe gold across Tailwind theme
3. Replaced every `wave-*.png` with the actual Crowe Logic hex C mark
4. Replaced Wave AI panel header icon with the Crowe face avatar
5. Generated proper `.icns` from the hex C at all 10 macOS sizes
6. Default browser homepage → `crowecode.com`
7. Default AI preset → CroweLM Auto router (no upstream model name leaked)
8. Telemetry off by default; phone-home endpoints stripped
9. Built `cli/openai_bridge.py` (Foundry → OpenAI-compatible HTTP) at 127.0.0.1:8011
10. Built `emain-foundry-bridge.ts` (Electron auto-spawns bridge on launch)
11. Built v5 .dmg pair (arm64 192MB, x64 242MB), published as v0.14.5-beta.1
12. Wrote monetization plan aligning Crowe Terminal with the canonical 4-tier pricing

### Session B — crowe-portfolio (parallel session)
1. Inventoried 242 GitHub repos + 34 local-only project dirs
2. Built canonical-flag heuristic; 12 duplicate clusters auto-resolved
3. Cataloged 5 Foundry agent tiers + 9 datasets (Postgres KGs, training corpora, books)
4. Built file-level chunker with binary-density guard + NUL-byte stripping
5. Built embedding client (Ollama, Azure OpenAI, Voyage, OpenAI) with retry-with-backoff
6. Built vector store abstraction (pgvector, Azure AI Search, Pinecone)
7. Provisioned Neon Postgres free tier with pgvector 0.8.0
8. Discovered existing `text-embedding-3-small` deployment on `crowelogicos-4667-resource`
9. Built MCP stdio server (FastMCP) — registered with Claude Code, ✓ Connected
10. Built FastAPI HTTP wrapper with bearer auth — 8 tests passing
11. Wrote `tools/portfolio_tools.py` (9 tool functions) for Foundry agent registration
12. Wrote `app/api/portfolio/[...slug]/route.ts` for ai.southwestmushrooms.com proxy

### Convergence
13. Crowe Terminal's foundry bridge passes through `CROWE_PORTFOLIO_URL` and `CROWE_PORTFOLIO_TOKEN` env vars
14. Once portfolio HTTP is deployed, terminal AI block has portfolio-wide search with zero additional code

---

## The architecture that emerged (without anyone designing it that way)

```
┌─────────────────────────────────────────────────────────────────┐
│                     SURFACES (consume)                          │
│                                                                 │
│  Crowe Code IDE   Crowe Terminal   ai.southwestmushrooms.com    │
│  (LM provider)    (AI block)       (operator UI)                │
│         │              │                 │                      │
│         │              │                 │                      │
│  Claude Code (dev)   Cursor       Foundry control plane         │
│         │              │                 │                      │
│         └──────────────┴─────────────────┘                      │
│                        │                                        │
└────────────────────────┼────────────────────────────────────────┘
                         │ (MCP stdio | HTTP/JSON)
                         ↓
┌─────────────────────────────────────────────────────────────────┐
│              CROWE LOGIC AGENT RUNTIME (the core)               │
│                                                                 │
│   cli/headless.py  ←→  openai_bridge.py  ←→ OpenAI clients      │
│        │                                                        │
│        ↓                                                        │
│   config/agent_config.py MODEL_CHAIN                            │
│   (Auto router → Supreme/Apex/Titan/Oracle/Sovereign tiers)     │
│        │                                                        │
└────────┼────────────────────────────────────────────────────────┘
         │
         ↓
┌─────────────────────────────────────────────────────────────────┐
│               KNOWLEDGE PLANE (the differentiator)              │
│                                                                 │
│   crowe-portfolio MCP/HTTP server                               │
│        │                                                        │
│        ├─ Registry (276 repos, canonical flags)                 │
│        ├─ Agent catalog (5 tiers + system prompts)              │
│        ├─ Dataset catalog (9 corpora)                           │
│        └─ Code KB (Azure embeddings → Neon pgvector)            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

The three layers were not designed top-down. They emerged because:

- The terminal needed an AI block → built the bridge → it became OpenAI-compatible by default (any client can call it)
- The IDE needed model picker entries → built the LanguageModelChatProvider → exposed the same agent through a different surface
- The web platform needed agent answers → already had the bridge, just needed a proxy route
- The cross-repo questions started piling up → built the knowledge plane → exposed via MCP/HTTP → every existing surface inherits it

This is the right shape: **one agent runtime, multiple surfaces, one knowledge plane**. The mistake everyone else makes is building the agent into the surface (Cursor's agent is locked to Cursor, Copilot's agent is locked to GitHub). Crowe Logic's agent is the product; the surfaces are channels.

---

## What this unlocks that wasn't possible yesterday

### 1. Surface neutrality

Any new surface — VS Code extension, JetBrains plugin, mobile app, voice agent, CLI — gets the full Crowe Logic agent + portfolio awareness with one MCP/HTTP hookup. No agent reimplementation.

Concrete: when the Big O Tires phone agent needs to look up "what oil filter does a 2018 Tacoma take," it can call the same `search_code` (or a domain-equivalent `search_inventory`) the IDE uses. The surface is voice, the agent is identical.

### 2. Cross-repo memory

The portfolio KB makes "what have I built before" a primitive. Every agent decision can now condition on the user's full code history, not just the open file. This is the difference between an AI assistant and an AI partner.

Concrete: an agent asked to wire Stripe into a new project gets ranked code chunks from every prior Stripe integration in the portfolio, with canonical-flag filtering so it only sees the pattern that actually shipped.

### 3. Agent self-awareness

The agent catalog (`crowe-portfolio agents`) exposes every model tier's system prompt, capabilities, and routing logic to the agents themselves. A meta-agent can read this catalog and pick the right tier dynamically — instead of CroweLM Auto using a hardcoded heuristic.

Concrete: a long-context document synthesis task comes in → meta-agent reads the catalog, sees CroweLM Titan has 200K context + synthesis-tuned prompt → routes there. Today this is heuristic; tomorrow it's data-driven via the catalog.

### 4. Domain RAG is structurally trivial

The dataset catalog already lists Neon Postgres KGs (the-record, epstein-database, prison-industrial-complex), training corpora (mushroom-grower 478K words, masterclass, masterhandbook), and structured datasets (compound discovery, drug discovery). Each one becomes a RAG source with one chunker/embedder pass per corpus.

Concrete: ai.southwestmushrooms.com gains "ask the cultivation manuals" as a search source alongside grow logs. CroweLM is no longer a generalist with mycology bias; it has direct retrieval on your authored knowledge.

### 5. Workflow runner becomes a real possibility

Once registry + KB + agents are addressable as primitives, cross-repo workflows ("ship-sop", "publish-volume", "deploy-voice-agent-vertical") become composable DAGs of agent calls. This is Phase 5 of the portfolio plan; today's work made it cheap to build.

Concrete: `crowe workflow run ship-sop --volume 3` becomes one command that touches the chapter repo, audiobook pipeline, storefront, and Foundry routing — orchestrated by an agent that knows where each piece lives.

### 6. Safe portfolio cleanup

16 repos auto-flagged `superseded_by` are now archive-ready. This was structurally impossible before — there was no notion of "which crowe-logic-platform is the real one." Today the registry says it.

Estimated downside-risk-to-archival:
- Pre-registry: archiving any repo could break unknown dependencies → 0 archives possible
- Post-registry: archive candidates are listed, dependencies are tracked → 16+ safe archives identified

### 7. Onboarding via tool, not Slack

Contractors and new collaborators ask "where does X live?" → registry answers in seconds. Reduces the "Michael as the sole context" bottleneck that's been a real growth limit.

---

## What's genuinely new (not just re-packaged)

Most of what got built today is composition, not invention. But three patterns are worth flagging as actual innovations:

### Pattern 1: Bridge-as-default surface contract

The Foundry agent exposes itself as an OpenAI-compatible HTTP server. This sounds obvious in hindsight but isn't standard — most agent runtimes expose a custom protocol that requires custom client code per surface. By choosing OpenAI Chat Completions as the default protocol:

- Wave Terminal's existing AI block works with zero changes
- LangChain works with zero changes
- The OpenAI Python SDK works with zero changes
- Every IDE that has an "OpenAI base URL" setting works with zero changes

Cost: lose access to provider-specific features (Anthropic system prompts, OpenAI Responses API, Gemini multi-modal). For most surfaces, this cost is invisible.

### Pattern 2: Canonical-flag-gated indexing

The portfolio KB only embeds repos flagged `canonical` or `solo`. This avoids the problem every code-search tool has: results from forks, experiments, and dead-end branches drowning out the real code. Three flags (canonical / superseded / experiment) plus per-cluster heuristics give precision without manual tagging.

### Pattern 3: Convergence-by-environment-variable

Crowe Terminal doesn't import crowe-portfolio. Crowe-portfolio doesn't know about Crowe Terminal. They're connected by two env vars (`CROWE_PORTFOLIO_URL`, `CROWE_PORTFOLIO_TOKEN`) the Electron main process passes through to the bridge child process.

This is loose coupling at its best:
- Either side can deploy/upgrade independently
- The contract is two strings, not a binary protocol
- A user without the portfolio still gets a working terminal

Most "integrations" in modern dev tools are tightly bound (plugin imports the host's API). The env-var pattern lets the surfaces compose without negotiation.

---

## Honest gaps and tradeoffs

### Gaps
- **Notarization blocked**: Apple Developer Program access is unresolved (you're already an Account Holder of an existing membership but can't see the team in the dashboard). Without it, .dmg downloads trip Gatekeeper and conversion drops 70%+.
- **DNS not routed**: crowecode.com still parked at Namecheap; 3 records need manual entry the Namecheap UI can't accept programmatically.
- **Workflow runner not built**: Phase 5 is specified, not implemented.
- **One Foundry agent path uses gpt-5.4-pro-managed (Azure Responses API)**: TTFT ~18s. For interactive surfaces this is too slow; chat-completion-surface models needed for the terminal.
- **Free Neon tier hibernates after 5 min idle**: first query post-hibernation takes ~500ms-2s. Retry-with-backoff handles it but interactive UX may want the $19/mo Launch tier.

### Tradeoffs taken
- **OpenAI-compatible by default** loses provider-specific features. We accept this because surface-neutrality is more valuable than per-provider squeeze.
- **Apache 2.0 license** prevents selling the terminal binary. We accept this because the agent is the product, the terminal is the channel.
- **Local Ollama embeddings** were swapped to Azure mid-run (+10× speed, 1536-dim quality > 768). We accept the ~$0.05/run cost because Azure credits cover it.
- **No tech stack exposure** in user-visible copy means we can't say "Claude Opus 4.7" in marketing. We accept this because it preserves vendor optionality and brand sovereignty.

---

## Where this points

The system that emerged today is the **AI-native developer OS**, not a collection of tools:

- A terminal you live in (Crowe Terminal)
- An IDE for deep work (Crowe Code)
- A web platform for ops/cultivation (ai.southwestmushrooms.com)
- A voice agent for the field (Big O Tires, Mike Voice Agent)
- A CLI for scripting (`crowe`)

…all backed by the same agent runtime (`cli/headless.py`), drawing from the same knowledge plane (`crowe-portfolio`), serving the same model tiers (CroweLM Auto/Supreme/Apex/Titan/Oracle/Sovereign).

The competitive position this creates:

| Competitor | Their model |
|---|---|
| Cursor | One IDE + their agent locked to it |
| Copilot | One agent + GitHub-only |
| Claude.ai / ChatGPT | One chat surface + general-purpose |
| Crowe Logic | One agent + N surfaces + portfolio-wide knowledge |

Pricing follows from architecture. The agent is the product:
- BYOK $19/mo: bring keys, run agent on your hardware
- Personal $29/mo: hosted CroweLM Auto/Apex/Titan
- Pro $99/mo: adds Supreme/Oracle/Sovereign + unmetered dual mode + 5h memory
- Team $49/seat/mo: pooled credits + shared workspace
- Enterprise: custom, $250 floor + SSO + audit

Each tier is a slice of the agent runtime, not a different feature set per surface. This makes the pricing legible to customers and the engineering economics legible to you.

---

## Three concrete next steps that compound

**1. Resolve Apple Developer membership** → unblocks notarized .dmg → unblocks public HN/Product Hunt launch → unblocks the funnel the monetization plan depends on.

**2. Define one cross-repo workflow** ("ship-sop" is the obvious candidate) → forces Phase 5 of the portfolio plan into existence → sets the pattern for every future workflow ("publish-volume", "deploy-voice-vertical", "discover-novel-target") to be 2 hours of work each instead of a redesign.

**3. Wire crowe-portfolio HTTP into Foundry on Railway** → portfolio search becomes available to every Foundry agent in production → ai.southwestmushrooms.com gains the "ask the code" panel → the convergence becomes load-bearing infrastructure rather than env-var theory.

These three together convert today's scaffolding into a shipped product. The architecture is right; what's left is execution gates that mostly aren't engineering.

---

## Bottom line

A normal day produces commits. Today produced a **coherent agentic system that wasn't there yesterday**: terminal + IDE + web platform all draw from the same agent runtime, the same model tiers, the same portfolio knowledge — connected by loose contracts (OpenAI Chat Completions, MCP, two env vars) rather than tight imports.

The innovation isn't any one component. It's that the components compose without a master plan because each one chose generic interfaces over bespoke ones. That's what makes the architecture multiply forward instead of branch.

Estimated effort to reach this state from a more conventional architecture (one bespoke agent per surface): 4-6 weeks of refactoring. Estimated effort to extend this state with a new surface (mobile app, JetBrains plugin, voice agent vertical): ~1 week, mostly UI work.

That ratio — 1 week per new surface vs. 4-6 weeks of one-time setup — is the moat.
