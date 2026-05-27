# Crowe Logic Platform State Report

Date: 2026-05-18
Prepared for: CroweLM DeepParallel ingestion
Author: Engineering working session

This document is a structured snapshot of the Crowe Logic platform at end of day 2026-05-18. It is designed to be parsed by a downstream model for analysis, not pitched at humans. Tone is honest about gaps; metrics are concrete; speculative claims are flagged.


## 1. Executive Summary

Crowe Logic is a vertical AI platform with two principal corporate vehicles:

- **Crowe Logic Inc.** owns the Foundry agent framework, the CroweLM model brand, and the developer surfaces.
- **Crowe Mycology LLC** owns the cultivation-OS product line (Crowe Logic Mycology at mycology.crowelogic.com), Southwest Mushrooms, and the new Azure subscription (4ea8ab04-...88a, account `mike@southwestmushrooms.com`).

The platform consists of four layered surfaces:

1. **Foundry control plane** at `~/Projects/crowe-logic-foundry`. FastAPI + Python. Multi-provider model gateway, agent orchestration, billing, knowledge lake. ~258 Python files, 66 test files, **718 passing tests**.
2. **Crowe Logic Mycology** (Next.js cultivation OS, live at mycology.crowelogic.com). Customer-facing product for mushroom growers.
3. **Crowe Logic chat** (`~/Projects/crowelogic-chat`, new this session). Next.js 15 + Vercel AI SDK + Tailwind. 13 routes. RAG-capable. Not yet deployed; ready for Vercel.
4. **CLI tooling**. `crowe-logic` (Python click) plus `pi` (third-party coding harness with custom Crowe extension). Used for operations, demos, and internal automation.

The platform shipped substantial capability in the 2026-05-18 working session. Five open foundry PRs (#28 through #32) form a stack that takes the platform from "CLI + Azure model registry" to "deployed web chat with knowledge-base grounding". One chat-repo PR (#1) ships server-side RAG.


## 2. Developer Report

### 2.1 Codebase shape

| Metric | Value |
|---|---|
| Foundry Python files | 258 (excluding venv, caches, node_modules) |
| Foundry test files | 66 |
| Foundry tests passing | 718 / 718 |
| Foundry largest module | `cli/crowe_logic.py`, 3686 lines |
| Foundry total LoC across CLI + control-plane + knowledge-lake + config | ~17 kLOC |
| Chat-app TypeScript files | 13 routes (mix of static and dynamic) |
| Chat-app dependencies | Next.js 15.5.18, React 19, Vercel AI SDK 4.3, Tailwind 3.4 |
| Chat-app build size | `/` static 22.4 kB, total bundle ~125 kB |

The codebase is mature for one engineer's output. The single largest risk is the size of `cli/crowe_logic.py` (3686 lines, 30+ click commands). It has not been refactored into per-command modules; the new doctor, discover, agents-deps, portfolio, and kb commands are each isolated in their own files and registered via lazy import. The legacy commands remain inline.

### 2.2 Shipping velocity (2026-05-18 single session)

Five open PRs against `crowe-logic-foundry`, forming a stack:

```
main
 ├── #28  fix(deploy): point Talon Sandbox at NEMOCLAW_SANDBOX_URL env var   (pre-existing, small)
 │   (this is the legacy branch that the rest extend)
 ├── #29  feat: chat.crowelogic.com launch + foundry hardening               (5 commits)
 │    └── #30  feat(knowledge-lake): SQLite+FTS5 foundation + markdown ingestor
 │         └── #31  feat(knowledge-lake): LaTeX + JSONL ingestors + /api/kb/search HTTP
 │              └── #32  feat(knowledge-lake): kb ingest-all
```

One chat-repo PR:

```
crowelogic-chat / main (initial scaffold + signup + history + kb proxy)
 └── PR #1  feat: server-side RAG with citation-aware system prompt
```

Net additions: ~5 new control-plane modules, ~1 new package (`knowledge_lake/`), ~5 new CLI subcommand groups, +21 control-plane tests, +20 knowledge-lake tests, +1 new GitHub repo, ~1500 lines of TypeScript in the chat app.

### 2.3 Test coverage

718 passing tests across 66 files. Notable additions this session:

- `test_model_sync.py`: 9 tests, covers the rebrand-map auto-apply.
- `test_openai_compat_stream.py`: 5 tests, OpenAI delta translation.
- `test_gateway_jwt_auth.py`: 8 tests, JWT auth path with default-workspace resolution.
- `test_chat_history.py`: 8 tests, full CRUD round trip with in-memory DB stub.
- `test_knowledge_lake.py`: 21 tests, store + chunking + glob + markdown/latex/jsonl ingestors + ingest-all CLI.
- `test_kb_search_http.py`: 8 tests, /api/kb/search HTTP route.

Coverage gaps acknowledged:

- No integration tests against a real Neon DB. The control-plane tests stub the DB lifespan and inject a fake.
- No end-to-end test against a live Azure model. Provider tests monkeypatch the SDK.
- No browser tests on the chat app. `npm run build` + `npm run typecheck` are the only CI signals.

### 2.4 Architecture

```
chat.crowelogic.com (Vercel, Next.js 15)
        │
        ├─ /api/chat  (proxy + optional RAG augmentation)
        │       │
        │       ├─ /api/kb/search (server-side fetch)
        │       └─ /v1/chat/completions (server-side fetch)
        │
        ├─ /api/sessions[CRUD]  (proxy)
        ├─ /api/auth/{login,signup,logout}  (proxy)
        └─ /api/kb/{search,sources}  (proxy)

                              ▼

api.crowelogic.com (Railway, FastAPI control plane)
        │
        ├─ /v1/chat/completions  (OpenAI-compatible SSE adapter)
        ├─ /v1/models
        ├─ /api/gateway/chat[,/stream]  (legacy metered surface)
        ├─ /api/sessions[CRUD]  (chat_sessions + chat_messages)
        ├─ /api/kb/{search,sources}  (knowledge_lake search)
        ├─ /api/auth/{register,login,refresh,me}
        ├─ /api/billing/*  (Stripe)
        └─ _resolve_api_key dispatcher: API key OR JWT

                              ▼

Provider chain (config/agent_config.py MODEL_CHAIN):
        │
        ├─ Azure OpenAI (CroweLM Helio, Quasar, Chat, Cinder, Beacon, ...)
        ├─ Azure Anthropic (Claude family, codenamed)
        ├─ Azure Cohere (Lattice, Filament Pro, Sift Fast/Pro)
        ├─ Azure DeepSeek (Cipher, Cipher Legacy, Flash)
        ├─ Azure Mistral / Llama / Grok / Kimi (all codenamed)
        ├─ NVIDIA NIM (Talon, Talon Sandbox)
        ├─ Ollama (local: CroweLM Unified Local, Crescent, Eclipse, LocalMesh, Gemma 4 Mycelium)
        └─ Watsonx (CroweLM Titan, Apex, Oracle, Sovereign, Prime, Nexus, Reason, Synapse)

Knowledge lake (~/.config/crowe-logic/knowledge.db, SQLite + FTS5):
        │
        ├─ foundry-docs           (markdown, 699 chunks, ingested)
        ├─ crowelm-unified-dataset (markdown, 18 chunks, ingested)
        ├─ mushroom-cultivators-masterclass        (latex, root missing locally)
        ├─ michael-crowe-mushroom-cultivation-handbook (latex, root missing)
        └─ themushroomgrower      (latex, root missing locally)
```

Total ingested today: 717 chunks across 2 corpora.


## 3. Product Readiness Report

### 3.1 Per-surface readiness

| Surface | State | Deploys to | Blocking issues |
|---|---|---|---|
| `crowe-logic` CLI | Production. 30+ subcommands. 718 tests pass. | Local Python install + npm wrapper at `@michaelcrowe11/crowe-logic`. | None. Ready to ship as part of any merge of #29-#32. |
| Foundry control plane | Production. Running on Railway as `foundry-control-plane`. | api.crowelogic.com via Railway. | Migration 009 (chat_sessions) must be applied to production Neon. `CROWE_STREAM_ENABLED=1` must be set to expose the OpenAI-compat surface. |
| Crowe Logic Mycology | Live at mycology.crowelogic.com. | Already deployed. | Separate codebase from foundry; not in this PR stack. |
| Chat app (crowelogic-chat) | Build-clean. Not yet deployed. | Vercel, target `chat.crowelogic.com`. | Needs `FOUNDRY_BASE_URL` + `FOUNDRY_API_KEY` env vars on Vercel and DNS pointed. |
| `pi` integration | Local-only, `.pi/` in foundry repo. | N/A (developer tool). | `@mariozechner/pi-*` deprecated. Migration to `@earendil-works/pi-*` deferred. |
| Knowledge lake | Local SQLite at `~/.config/crowe-logic/knowledge.db`. | Co-located with control plane on Railway when deployed. | `/api/kb/search` route exists; needs the database to be present at the configured path. |

### 3.2 Critical pre-launch checklist for chat.crowelogic.com

1. Merge PRs #29, #30, #31, #32 in that order against the foundry.
2. Apply `migrations/009_chat_sessions.sql` to production Neon. Idempotent.
3. Set `CROWE_STREAM_ENABLED=1` in Railway env on the foundry.
4. Run `crowe-logic doctor --fix-leaks` from the production shell. The runtime registry at `~/.config/crowe-logic/models.extra.json` very likely has the same 25 pre-rebrand labels the local one had until today. Doctor will repair in place with a timestamped backup.
5. Run `crowe-logic kb ingest-all` from the production shell to populate the lake with foundry-docs at minimum.
6. Push `crowelogic-chat` repo to GitHub: already done as private repo at MichaelCrowe11/crowelogic-chat. Merge chat-PR #1 (RAG).
7. Create Vercel project pointing at the chat repo. Set `FOUNDRY_BASE_URL=https://api.crowelogic.com` and `FOUNDRY_API_KEY=<workspace key>`.
8. Add `chat.crowelogic.com` as a custom domain on Vercel. CNAME from name.com.
9. Verify CORS: `chat.crowelogic.com` is in the foundry's allowlist as of PR #29.
10. End-to-end smoke: sign up at chat.crowelogic.com, flip kb-on toggle, ask a foundry-internals question, verify the response cites `foundry-docs/*`.

### 3.3 Known issues surfaced by `crowe-logic doctor`

| Severity | Issue | Source |
|---|---|---|
| FAIL | Disk on `/` runs at 99.1 percent used, 2.1 GB free | Local Mac. Memory rule routes audio scratch to /Volumes/Elements; production Railway is unaffected. |
| FAIL (repaired) | 25 leaky pre-rebrand labels in `~/.config/crowe-logic/models.extra.json` | Auto-repaired today via `doctor --fix-leaks`; production probably needs the same. |
| WARN | `.env.local` missing | Expected; production uses Railway env. |
| WARN | Anthropic env vars unset | Reflects the active Azure migration; the new Azure account at `mike@southwestmushrooms.com` does not yet have an Anthropic deployment. |
| WARN | 6 tool files have docstring schema gaps | `azure_agent`, `chatgpt_agent`, `deepparallel`, `crowe_terminal`, `training_store`, `agent_runner`. Total ~10 functions missing `:rtype:` or a `:param`. Each is a silent Azure schema-generation risk. Not addressed in this PR stack. |
| WARN | 1 orphan tool reference: `crowe-talon` agent declares `git_ops` which does not exist in `tools/` | Identified by `crowe-logic agents-deps`. Not fixed in this PR stack. |

### 3.4 Authentication state

- API-key auth: production. `cl_*`, `clk_*`, `pat_*` prefixes accepted via `Authorization: Bearer` or `X-API-Key`.
- JWT auth: new in PR #29. Browser sessions mint 24-hour JWTs via `/api/auth/login`. The gateway resolver accepts both transparently. `X-Workspace-Id` header scopes multi-workspace users; default-workspace lookup picks the most recently created active workspace the user belongs to.
- No JWT refresh wiring on the chat app yet. 24-hour cookie expires; user re-logs in. Backlog.


## 4. Market Position and Pain Points

### 4.1 Segments served

| Segment | Active product | Status |
|---|---|---|
| Commercial mushroom cultivators | Crowe Logic Mycology (mycology.crowelogic.com) | Live. MGAP compliance MVP shipped. ei.southwestmushrooms.com Mycelium EI Engine onboarding flow live. |
| Hobby and prosumer mushroom growers | The Mushroom Grower book series, Lion's Mane SOP, audiobook variants | Live on Amazon KDP. ISBN issued for Lion's Mane SOP. |
| Auto and tire shops (Arizona) | Auto-Logical Solutions cold-email program selling AI phone agent | Started 2026-05-14 after the Big O Tires deal died. Untested. |
| Content creators | ToxicTeeTv production stack, MediaSynth platform | CMA + co-performer agreement executed 2026-05-14. Revenue split locked: 60/40 on-site, 65/35 off-site, 40/60 co-performed Lane B work. |
| AI developers and labs | Crowe Logic Foundry agent framework (npm `@michaelcrowe11/crowe-logic`, PyPI `crowe-logic`) | Public npm + PyPI presence. Foundry MaaS deployed sovereign Kimi-K2.6 on 2026-05-16. |
| Drug discovery research | CroweChem, CriOS, DeepParallel-DrugDiscovery, crios-nova-chemoinformatics | 20+ repos. Several public on GitHub. No paid customer yet. Phase 1 of crowe-psychedelics shipped 2026-04-21 with `lfpsy` CLI live. |
| Legal services | Crowe Legal LDIAS | First matter accepted 2026-05-08: Davis Family Trust / Elizabeth Mary Adams. |
| Banking automation | Crowe Treasury (Mercury webhook -> Neon -> Retool, Stripe reconciliation, AI classification on Opus 4.7) | Phases 0a/0b/0c/1/2a/2b shipped 2026-05-06. Pending Neon DB bring-up. |

### 4.2 Pain points addressed

**Cultivation segment:**
- Scattered knowledge across PDFs, LaTeX manuscripts, and forum posts. The knowledge lake's LaTeX ingestor is built; ingesting `themushroomgrower` + `michael-crowe-mushroom-cultivation-handbook` + `mushroom-cultivators-masterclass` once their local clones land will add ~600K words of searchable cultivation expertise to the chat surface.
- Photo-based contamination diagnosis without a domain-specialized vision pipeline. Foundry already has `crowe_vision` tool and `cultivation.yaml` agent declaring it. Visible to chat.crowelogic.com via the agent runner.
- Lack of structured grow-log capture and longitudinal analysis. `crowe_grow_log` tool exists in the cultivation agent.
- Compliance documentation (MGAP) is manual. Crowe Logic Mycology shipped an MVP for this 2026-05-14.

**Drug discovery segment:**
- SMILES canonicalization and cross-repo dedup are unsolved at portfolio scale.
- Patent landscape monitoring is fragmented; `ai-compound-discovery-patent-alert` repo exists but is solo and unpaid.
- 194-PhD agent system in `crios-dr-crowe-coder` is differentiated capability but lacks a customer-facing surface.

**Developer platform segment:**
- Provider lock-in. Crowe Logic's codename layer (rebrand_map.py, virtual routing tiers) means callers select `CroweLM Helio` or `CroweLM Hyphae` without knowing the upstream is OpenAI or Moonshot Kimi. Upstream swaps without breaking the API contract.
- No clean OpenAI-compatible bridge for self-hosted models. `/v1/chat/completions` shipped today addresses this.
- Knowledge bases require expensive vendor SaaS (Pinecone, Weaviate, etc). The knowledge lake uses SQLite + FTS5; zero deploy deps and zero per-query cost.
- Multi-device chat history requires either Anthropic Projects (closed) or a custom stack. Migration 009 + `/api/sessions` ships an open one.

**Content creators:**
- Multi-platform distribution requires custom orchestration. Foundry Studio Agent (shipped 2026-04-22) does this tenant-agnostically. ToxicTeeTv plus southwest-mushrooms plus mushroom-grower-audio plus crowe-psychedelics plus scratch are registered tenants.
- Audience engagement triage drains creator time. SWM YouTube comment-triage system at `agent/` is production-deployed against a 195K-subscriber channel.

### 4.3 Competitive landscape

| Competitor | Overlap with Crowe Logic | Differentiation we hold |
|---|---|---|
| OpenAI ChatGPT Enterprise | General chat, web surface, model variety | Crowe Logic ships domain agents (mycology, cultivation, drug discovery) plus first-party knowledge corpora. ChatGPT is horizontal. |
| Anthropic Claude for Work | Same general chat shape | Crowe Logic is multi-provider behind one codename surface; Anthropic locks you to Claude. |
| Vercel AI SDK + LangChain ecosystem | The OpenAI-compatible chat surface itself | Crowe Logic is the substrate underneath: the platform that exposes the same surface but routes across 75 models with codenames and plan-gated access. |
| Cropwise, Granular, FarmLogs | Agricultural decision support | Crowe Logic owns the mycology niche specifically. 195K-subscriber YouTube channel is a marketing moat. |
| Schrodinger, Atomwise, Insitro | Drug discovery platforms | Crowe Logic is solo and not customer-facing in this segment yet. Capability is real (DeepParallel + 194-agent system), distribution is not. |
| Domain-specific RAG SaaS (Vectara, Hebbia) | Knowledge-lake surface | Crowe Logic ships RAG bound to first-party Crowe content (books, agents, foundry-docs). Generic RAG vendors do not. |

### 4.4 Market share commentary

Quantitative market share is not measurable from internal data. Qualitative position:

- **Mycology**: Crowe Logic Mycology and The Mushroom Grower book series have strong organic search and YouTube presence. Lion's Mane SOP has commercial customers. The cultivation OS at mycology.crowelogic.com is the only AI-native mycology product the team is aware of. Realistic ceiling: low-thousands of paying cultivator customers globally, with high-margin enterprise sales to commercial farms.
- **Developer platform**: PyPI `crowe-logic` and `synapse-lang` (separate but related) have public footprint but no published adoption metrics. PyPI 2.3.3 of synapse-lang has known broken entry points.
- **Drug discovery**: No commercial customers yet. Pure capability play.
- **Content creator stack**: One paid integration (ToxicTeeTv with locked revenue split). Single-vendor risk.

The strongest near-term market-share play is the cultivation niche because the product is live, the audience is large (195K YouTube), and the proprietary corpora (600K-word book backlog) are not replicable by competitors.


## 5. Innovation Pipeline

### 5.1 Shipped in the 2026-05-18 session

- `crowe-logic doctor`: 17-check preflight diagnostic with `--fix-leaks` self-repair. First live run caught 25 leaky pre-rebrand labels and 6 tool docstring gaps.
- `crowe-logic discover`: keyword + fuzzy search across models, agents, tools.
- `crowe-logic agents-deps`: cross-references YAML agent declarations against the live tool registry. Wildcard support for `talon_*` patterns.
- `crowe-logic portfolio`: 8 subcommands wrapping the crowe-portfolio MCP (242 repos, 9 datasets, agent catalog).
- `crowe-logic kb {sources, status, ingest, ingest-all, search}`: knowledge-lake CLI.
- `config/crowelm/rebrand_map.py`: source of truth for the 26 deployment-name to codename mappings. Auto-applied in `model_sync.build_extra_model_entry`. Mechanical label is preserved in aliases for legacy resolution.
- OpenAI-compatible `/v1/chat/completions` adapter. Translates crowe-stream v0 to OpenAI delta format. Both `stream=true` and `stream=false` supported. `/v1/models` lists plan-available codenames.
- JWT auth path in `_resolve_api_key` so browser sessions never hold workspace API keys.
- Migration 009 + `/api/sessions` CRUD for multi-device chat history with auto-bump trigger.
- `/api/kb/search` HTTP route for retrieval-augmented chat.
- Chat-app (`crowelogic-chat`): Next.js 15 + Vercel AI SDK with sign-in, sign-up, sidebar history, kb-on toggle, server-side RAG with citation-aware system prompt.
- `.pi/` workspace integration for the pi coding harness in the foundry repo.

### 5.2 Queued (Phase 4, deferred from this session)

- Visual citation chips in the rendered assistant message (parse `[Source: ...]` patterns, link them to the source file).
- Knowledge-base source picker in the chat UI so users can scope RAG to one corpus.
- pgvector Store sibling for hybrid recall (FTS plus embeddings).
- Auto-discover sources from the crowe-portfolio MCP (replacing the hand-maintained `sources.py`).
- Tool-call delta translation in `/v1/chat/completions` so the chat UI can show tool-use cards.
- Streamed-token durability (persist assistant row on first token, not after completion, to survive mid-stream crashes).
- LaTeX ingestor exercising against the three cultivation book repos once local clones are present.
- JSONL ingestor exercising against future training corpora.
- `@mariozechner/pi-*` to `@earendil-works/pi-*` migration (4 files, mechanical).
- Fix 6 tool docstring gaps and the one `git_ops` orphan from `agents-deps`.

### 5.3 Strategic bets

- **DeepParallel as a product surface, not just a backend.** Today DeepParallel is a model selection. The thesis: multi-chain reasoning with judge synthesis is a moat against single-provider chat UIs. Need to expose it cleanly to customers, not just internally.
- **The cultivation book backlog is the knowledge-lake's distribution moat.** Three books, ~600K words. Once they are in the lake, every mycology chat answer can cite them. No competitor has this corpus.
- **The codename abstraction is the dev-platform's positioning.** Customers using `CroweLM Helio` are insulated from OpenAI pricing, Anthropic outages, and provider deprecations. The rebrand SOT and `doctor --fix-leaks` operationalize this.
- **The 195K-subscriber YouTube channel is acquisition.** Free customer-facing chat at chat.crowelogic.com tied to mycology authority converts at higher rates than cold outbound.


## 6. Roadblocks

### 6.1 Technical

| Roadblock | Impact | Mitigation status |
|---|---|---|
| Azure dead zone between accounts | Legacy account `crowelogicos-*` returns 401. New account on `mike@southwestmushrooms.com` (sub 4ea8ab04-...88a) has no deployments yet. Chat traffic on mycology.crowelogic.com routes around via OpenAI direct (PR #70 in that repo, merged 2026-05-15). | Active. The 9 quota requests filed 2026-05-13 are pending. |
| Disk on local dev machine at 99.1 percent used | Cannot run audio jobs. Cannot pull large local models. | Mitigated by routing scratch to `/Volumes/Elements`. Production unaffected. |
| crowe-stream v0 does not carry `prompt_tokens` | Streaming `/v1/chat/completions` records 0 prompt tokens for billing | Acknowledged. Waiting on protocol v1 (gap #3 in the spec). |
| Streamed-token durability | Mid-stream crash loses the assistant turn | Acknowledged. Mitigation is to persist the assistant row at first token. Backlog. |
| Tool-call delta translation | `useChat` cannot show "agent is calling crowe_chat" cards | Acknowledged. OpenAI tool-delta format is verbose; deferred to Phase 4. |
| 6 tool docstring gaps | Silent Azure schema generation failures | Surfaced by `doctor`. Not fixed. |
| 1 agent-tool orphan | `crowe-talon` agent declares `git_ops`, which does not exist | Surfaced by `agents-deps`. Not fixed. |
| Pi packages deprecated | `@mariozechner/pi-*` namespace moving to `@earendil-works/pi-*` | Mechanical migration deferred. Current pi 0.73.1 still works. |
| Single SQLite file for the knowledge lake | Will not scale to multi-tenant production | Acceptable for Phase 1-3. pgvector Store sibling planned. |
| No CI on the foundry repo | Tests are run by the developer, not by GitHub Actions | The new `.github/workflows/pi-review.yml` exists but does AI review, not test execution. |

### 6.2 Business

| Roadblock | Impact |
|---|---|
| Big O Tires deal dead 2026-05-05 | Lost $7.5K/mo target customer. Pivoted to Auto-Logical AZ-wide cold email. |
| Square account shut down mid-build of swm-storefront | Physical-product commerce on Square is a write-off. |
| Single-developer organization | One bus factor for the entire stack. Documentation in MEMORY.md mitigates partially. |
| No paid customers in drug discovery despite 20+ repos | Capability without distribution. No GTM motion in this segment. |
| Solo Crowe Legal LDIAS practice | First matter accepted 2026-05-08, but capacity is bounded by Michael's time. |
| ToxicTeeTv is a one-creator concentration | Revenue split locked but the upside is capped by one creator. |
| Compliance unclear for psilocybin-adjacent content in some jurisdictions | Crowe Psychedelics platform is live (`lfpsy` CLI shipped 2026-04-21) but go-to-market gated by legal review. |

### 6.3 Infrastructure

| Roadblock | Impact |
|---|---|
| Multiple Azure subscriptions in flight | Migration window means runtime behavior depends on which account credentials are active. |
| Railway tracks-by-service-not-by-repo | `crowe-logic-foundry` has multiple Railway services; deploys are stitched together manually. |
| name.com TLD has DNS API quirks | Apex host is empty string, not `@`. Known via memory `reference-namecom-api`. |
| Lulu Cloudflare block on automated book uploads | Manual browser uploads only. |
| Google Drive on dev machine is stream-only | Customer files delivered via Dropbox only. |

### 6.4 Capital

- No external funding. All development is self-funded.
- Stripe + Mercury are the working capital infrastructure.
- The mushroom grower bundle ($499) and Lion's Mane SOP are the highest-volume revenue lines today.
- Foundry / dev-platform revenue is zero until chat.crowelogic.com launches and converts customers.


## 7. Recommendations and 30 / 90-day Outlook

### 7.1 Next 30 days

1. **Merge the PR stack and ship chat.crowelogic.com.** PRs #29 through #32 plus chat-PR #1 are the minimum viable web product. Following the §3.2 checklist takes a few hours of operational work.
2. **Apply doctor to production.** `crowe-logic doctor --fix-leaks` against the production registry. This is mechanical and high-value: the deploy table is customer-visible and currently has 25 known leaky labels.
3. **Ingest the cultivation books.** Clone `themushroomgrower`, `michael-crowe-mushroom-cultivation-handbook`, `mushroom-cultivators-masterclass` to local Projects/ and run `crowe-logic kb ingest-all`. Adds ~600K words of cultivation expertise to the lake. Then mycology.crowelogic.com chat goes from "generic LLM" to "Crowe-knowledge-grounded".
4. **Fix the 6 tool docstring gaps and the `git_ops` orphan.** Each is a silent prod risk; together they take an afternoon.
5. **Stand up a basic CI workflow.** Run `pytest -q` on PR. The infrastructure to do this is already in `.github/workflows/`; just add a separate test job.

### 7.2 Next 90 days

1. **pgvector Store sibling.** Hybrid recall doubles knowledge-lake quality on conceptual queries where FTS keyword matching misses.
2. **Visual citation chips in the chat UI.** Turns the existing RAG into something users can see and click.
3. **Source picker in the chat UI.** Users select "ask the cultivation books only" or "ask the foundry docs only". Same surface, scoped recall.
4. **Auto-Logical sales validation.** Either the AZ cold-email program produces a paying tire-shop customer or the segment gets dropped. Six weeks is enough signal.
5. **Crowe Psychedelics legal review.** Determine which jurisdictions the `lfpsy` CLI and Tier 1 are clear to ship into. Compliance work, not engineering.
6. **DeepParallel as a customer-facing tier.** Today `CroweLM DeepParallel` is selectable as a virtual model. Promote it to a marketing line with a price point ($497/month per the Talon master pricing reference is one plausible anchor).
7. **Migrate the foundry's Azure footprint fully off the legacy account.** Memory notes list 9 pending quota requests filed 2026-05-13. Close the migration; retire the legacy account.

### 7.3 Existential risks to monitor

- Single-developer bus factor. The MEMORY.md system mitigates but does not eliminate.
- Provider deprecation or pricing shock in Azure Foundry would invalidate a significant fraction of the model chain. The codename layer cushions but does not prevent.
- Knowledge-lake content is the moat for the cultivation segment. If a competitor licenses or scrapes the books, the moat erodes.
- The chat app passes API keys server-side through environment variables. A leak of `FOUNDRY_API_KEY` from the Vercel project would expose workspace-wide billing.


## 8. Appendix: Repository Index

### 8.1 crowe-logic-foundry layout

```
control_plane/         FastAPI app, auth, gateway, billing, streaming, chat_history, kb_search
cli/                   click commands. crowe_logic.py is the entry. doctor, discover,
                       agents_deps, portfolio, kb, kb (knowledge lake), headless, openai_bridge
config/                model registry. agent_config.py, model_sync.py, models.extra.json,
                       crowelm/rebrand_map.py, crowelm/brand_registry.py
agents/                YAML agent definitions: code, crowe-talon, cultivation, music,
                       quantum, research, studio, substrate
tools/                 39 Python files, 144 registered functions. Plus FastAPI service modules
                       (control_center.py, mobile_signaling.py) and registry/audit_log/mcp_client
providers/             Provider-layer abstraction. openai_compat, anthropic, azure_openai, etc.
knowledge_lake/        Phase 1-3 store + ingest + search package. SQLite + FTS5.
crowe_synapse_engine/  Proprietary DSL + tuning pipeline + AICL transport
migrations/            SQL migrations 001-009
.pi/                   pi coding harness integration: extension, skills, prompts, AGENTS.md
scripts/               Operational scripts. pi-build.mjs, verify-rebrand.sh, weekly-readiness.sh
docs/                  Mixed protocol specs, runbooks, this report
tests/                 66 test files, 718 passing tests
```

### 8.2 Repositories touched in this session

| Repo | Branch | Purpose |
|---|---|---|
| crowe-logic-foundry | feat/chat-launch-and-foundry-hardening | PR #29 |
| crowe-logic-foundry | feat/knowledge-lake | PR #30 |
| crowe-logic-foundry | feat/knowledge-lake-phase-2 | PR #31 |
| crowe-logic-foundry | feat/knowledge-lake-phase-3 | PR #32 |
| crowelogic-chat (new) | main | initial scaffold + sign-up + history + kb proxy |
| crowelogic-chat | feat/rag-wiring | chat-PR #1 server-side RAG |


## 9. Closing note for the ingesting model

This report is best used as background context for follow-up reasoning about platform strategy, customer-facing feature priorities, and engineering sequencing. It does not replace operational telemetry. Any claim flagged as "Acknowledged" or "Backlog" is a known unknown. Any claim flagged as "Realistic ceiling" or "Qualitative position" is a working assumption, not measured.

The platform is currently understaffed relative to its surface area but unusually capable per developer-hour. The single highest-leverage move in the next 30 days is to merge the PR stack, ship chat.crowelogic.com, and put the cultivation books in the lake. The single highest existential risk is the bus factor on Michael.
