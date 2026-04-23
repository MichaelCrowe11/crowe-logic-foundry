# Crowe Home — Self-Hosted Foundry Edition

**Date:** 2026-04-22
**Author:** Michael Crowe / Crowe Logic, Inc.
**Status:** Draft — awaiting review
**Scope:** Strategic design + 2-phase implementation plan
**Related:** `launchable/brev.yaml`, `control_plane/gateway.py`, `cli/headless.py`, BlueBubbles (architectural inspiration)

---

## 1. Why this spec exists

Foundry today is designed around a single-user laptop CLI. That works well for hands-on development but leaves six gaps that matter for product reach:

1. No always-on server identity. Close the laptop, agents stop.
2. No push channel. Dispatched long-running work has no way to notify the operator.
3. No mobile or web client. VS Code extension is the closest remote surface.
4. Onboarding requires 7+ provider API keys in a hand-curated `.env`.
5. Session and memory state are laptop-local. Switch devices, lose context.
6. Control Plane (Railway) is positioned as sales infrastructure (billing, plan tiers), not as the operator's primary access point.

The BlueBubbles architecture solves exactly these six gaps for a different problem (iMessage bridging). Crowe Home applies the same pattern to agent orchestration: the operator's own hardware is the authoritative server, clients are thin, Cloudflare Tunnel handles exposure, push notifications close the async loop, and OAuth-based server discovery eliminates the `.env` ceremony for clients.

The strategic bet: Foundry becomes the only agent platform where **your data, memory, and tools live on hardware you own** while still getting cloud-scale inference and a professional mobile experience. That differentiation is defensible against Anthropic mobile, OpenAI mobile, Google AI Studio, and most agent startups — all of which require giving up data residency.

## 2. Non-goals

- Replacing the hosted SaaS. Home is a third SKU alongside Developer / Studio / Lab / Enterprise, not a migration.
- Building a generic self-hosting platform. Home is opinionated about Foundry; not a Portainer clone.
- Re-implementing iMessage bridging. We borrow BlueBubbles' pattern, not its protocol.
- Shipping before the Railway SaaS launch completes. `DEPLOYMENT.md` Phase 1 stays the current week's priority.

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│ Operator's hardware (Mac / Linux / NAS / eventually Pi for sandbox) │
│                                                                     │
│   ┌──────────────────────────────────────────────────────────┐      │
│   │ crowe-logic serve  (long-running process)                │      │
│   │ ├── Same provider layer (7 providers, 48 models)         │      │
│   │ ├── Same tool registry (+ nemoclaw_shell, studio, mcp)   │      │
│   │ ├── Local SQLite = authoritative session + memory store  │      │
│   │ ├── Optional Ollama for private-only inference           │      │
│   │ ├── REST API (parity with Control Plane gateway)         │      │
│   │ ├── WebSocket for streaming chat + turn events           │      │
│   │ └── Push dispatch to FCM/APNs on turn completion         │      │
│   └──────────────────────────────────────────────────────────┘      │
│                                │                                    │
│                     Cloudflare Tunnel                               │
│                  (auto-provisioned at first run)                    │
└────────────────────────────────┼────────────────────────────────────┘
                                 │
              https://<user>.crowelogic.home
                                 │
         ┌───────────────────────┼────────────────────────┐
         │                       │                        │
   ┌──────────┐           ┌──────────┐            ┌──────────────┐
   │ iOS app  │           │ Android  │            │ Web (crowelm │
   │ (native) │           │ app      │            │ .live shell) │
   └──────────┘           └──────────┘            └──────────────┘
         │                       │                        │
         └───────────────────────┼────────────────────────┘
                                 │
                  ┌──────────────┴──────────────┐
                  │ Control Plane on Railway    │
                  │ Role: registry + OAuth      │
                  │ - "Which server is mine?"   │
                  │ - Stripe Home subscription  │
                  │ - Push notification relay   │
                  └─────────────────────────────┘
```

**Invariants:**
- Authoritative state lives on the operator's hardware. Control Plane is stateless about user data.
- Clients never talk to providers directly. They talk to the operator's server, which talks to providers.
- Push is a one-hop relay. Control Plane does not see message contents, only `{user_id, event_type, deep_link}`.
- Existing CLI continues to work unchanged. `crowe-logic chat` on the operator's laptop still means "talk to my local server" — it just now means the server could also be reached from elsewhere.

## 4. Phase 1 — `crowe-logic serve` (2-3 weeks)

Build the long-running server mode. Everything local, no tunnel yet, no mobile yet. Success criterion: two laptops on the same LAN can both reach one server's REST API, receive streamed chat turns, and see the same transcript.

### 4.1 New entrypoint

```
crowe-logic serve [--host 0.0.0.0] [--port 8787] [--auth-mode token|oauth]
                  [--cors-origin https://ide.crowelogic.com,...]
                  [--ollama-url http://localhost:11434]
                  [--data-dir ~/.crowe-logic/home]
```

Default binds to `127.0.0.1:8787`. `--host 0.0.0.0` is opt-in; when set, `--auth-mode token` is required (no unauthenticated LAN exposure by accident).

### 4.2 API surface

Parity with `control_plane/gateway.py` plus the client-facing shapes:

| Endpoint | Purpose |
|---|---|
| `GET /health` | Identity + version + model count |
| `POST /v1/chat` | Non-streaming turn (deterministic short responses) |
| `WS  /v1/chat/stream` | Streaming turn with tool-call events, mirrors CLI's Rich output structure |
| `GET /v1/models` | Resolved model chain for the operator's server |
| `GET /v1/sessions` | List sessions, paginated |
| `GET /v1/sessions/:id` | Full transcript |
| `POST /v1/sessions/:id/fork` | Fork session at turn N (parity with CLI `/fork`) |
| `POST /v1/sessions/:id/replay` | Replay from turn N (parity with CLI `/replay`) |
| `GET /v1/tools` | Registered tools + schemas |
| `POST /v1/dispatch` | Fire-and-forget long-running turn, returns `dispatch_id`; client subscribes via WS or waits for push |
| `GET /v1/dispatch/:id` | Status + result for a dispatched turn |

### 4.3 Session runtime reuse

`cli/session_runtime.py` already owns the turn loop for both interactive and headless modes. `crowe-logic serve` is a third consumer: each WS connection gets its own session context, turns run through the same code path, rendering events route to a WS-emitter adapter instead of the Rich renderer.

Key non-trivial work:
- Rich event stream → JSON event stream mapper (`cli/ws_renderer.py`, new). Mirrors the dual-mode `QueueRenderer` pattern from `cli/queue_renderer.py`.
- Tool-call confirmation flow. CLI blocks on a prompt; over WS, we emit `{event: "confirm_tool", id, params}` and wait for `{event: "confirm_tool_response", id, allow: true|false}`.
- Memory store thread-safety is already solved (`crowe_synapse_engine/memory.py` has `_LockedConnection`).
- Auth middleware — bearer token in `Authorization`, token stored in `~/.crowe-logic/home/tokens.db`, rotateable via `crowe-logic serve issue-token`.

### 4.4 Client reference: CLI remote mode

Add `crowe-logic chat --server https://<host>:8787 --token <t>`. Proves the API surface end-to-end and gives us a client we dogfood before mobile.

### 4.5 Deliverables

- `cli/server.py` — FastAPI app, uvicorn entrypoint, mirrors `control_plane/main.py` conventions.
- `cli/ws_renderer.py` — event stream mapper.
- `cli/commands/serve.py` — Click subcommand.
- Tests: WS round-trip, fork/replay parity with CLI, concurrent-client isolation, tool-confirmation protocol.
- Docs: `docs/crowe-home-quickstart.md` — LAN-only first-run experience.

### 4.6 Explicit out-of-scope for Phase 1

- No tunnel.
- No mobile client.
- No push.
- No OAuth.
- No encrypted sync — SQLite on server is authoritative and only accessible over the REST/WS API.

## 5. Phase 2 — Tunnel, Discovery, Mobile (6-8 weeks after Phase 1 lands)

Close the UX loop. Success criterion: a new user runs one installer on their Mac, signs in on their phone with Google/NVIDIA SSO, and is chatting with their Foundry agents from outside their home network within 10 minutes.

### 5.1 Cloudflare Tunnel integration

- `crowe-logic serve --tunnel cloudflare` auto-provisions a Cloudflare Tunnel using an operator-supplied API token (stored in macOS Keychain / Linux Secret Service).
- Emits a stable hostname `https://<user-slug>.crowelogic.home` (domain owned by Crowe Logic, wildcard cert via Cloudflare).
- Fallback: `--tunnel ngrok` and `--tunnel none --lan-only` for users who want alternatives.
- Health check loop keeps the tunnel fresh across network changes.

### 5.2 Server discovery via Control Plane

Cribs BlueBubbles' OAuth pattern but uses the existing Crowe Control Plane instead of Firebase.

Flow:
1. Operator runs `crowe-logic serve`. On first start, server registers itself with Control Plane: `POST /api/home/register` with `{user_id, tunnel_url, public_key, capabilities}`.
2. Client signs in with Google/NVIDIA SSO on mobile app.
3. Client queries `GET /api/home/my-server` — Control Plane returns `{tunnel_url, public_key}`.
4. Client connects directly to the operator's server, verifying its cert fingerprint against the public key.
5. Control Plane never sees user messages. Its only role is the registry lookup.

Security: the `public_key` field is the operator's server's TLS pubkey. Clients pin it on first connect (TOFU). Control Plane rotation API is out-of-scope for Phase 2.

### 5.3 Push notifications

- Server emits `{user_id, event: "turn_complete", session_id, preview}` to Control Plane when a dispatched turn finishes.
- Control Plane routes to FCM (Android) or APNs (iOS).
- Mobile client taps notification → deep-links to `/v1/sessions/:id` on the operator's server.
- Message contents stay on the operator's server. Push payload carries only a 80-character preview for the notification body.

### 5.4 Mobile clients

- iOS: SwiftUI native app. Reuses Claude-style chat UI patterns. Under 4 screens: Chat, Sessions, Tools, Settings.
- Android: Kotlin Compose. Parity with iOS.
- Both ship with Google Sign-In + NVIDIA SSO (reuses Control Plane's existing Brev-style OAuth flow).
- No Talon-on-mobile. The phone is a client, not a server.

### 5.5 Web client

- `https://home.crowelogic.com` — thin Next.js shell.
- Same OAuth discovery flow.
- Renders streams via WS against the operator's server.
- Positioned as "fallback client" — use the mobile app when you can, the web when you can't.

### 5.6 Deliverables

- `cli/tunnel.py` — Cloudflare/ngrok abstraction.
- `control_plane/home_registry.py` — register + discover endpoints.
- `control_plane/push.py` — FCM + APNs relay.
- `mobile/ios/` — SwiftUI app.
- `mobile/android/` — Compose app.
- `apps/home-web/` — Next.js client.
- Marketing: `crowelogic.com/home` landing page.

## 6. Cross-cutting concerns

### 6.1 Revenue model

Crowe Home is a subscription SKU:

| Plan | Monthly | What you get |
|---|---|---|
| Home (included with Studio or Lab) | $0 add-on | Tunnel, mobile apps, push, web client |
| Home Standalone | $29 / mo | Same as above, no bundled inference |
| Home Family | $49 / mo | Up to 5 operators under one account, shared billing |

Inference is not bundled. Operators BYOK for provider keys (Anthropic, NVIDIA, Watsonx, etc.) or pay per-token through the existing metered gateway.

This is revenue BlueBubbles never captured because they're donate-ware. The subscription covers the tunnel CA, push relay infra, mobile app stores, and support.

### 6.2 Privacy posture as marketing

Tagline candidates:
- "Your agents. Your hardware. Anywhere."
- "Agent platform. No landlord."
- "Self-hosted intelligence, cloud-scale reach."

The story: Anthropic/OpenAI mobile means your conversations live on someone else's server. Crowe Home means they live on yours, and we just help you reach them.

### 6.3 Failure modes and mitigation

| Risk | Mitigation |
|---|---|
| Operator's Mac sleeps and clients appear offline | Doc: how to configure no-sleep. Future: small "Home Watch" resident daemon that keeps a socket warm. |
| Operator's IP changes | Cloudflare Tunnel handles this by design. |
| Operator loses their Mac | Control Plane has an unregister flow; clients show "server unreachable" with a link to restore from a backup. |
| Provider API key leaks via server | Server stores keys in OS keychain, not files, when `--auth-mode oauth` is active. |
| Control Plane outage breaks client discovery | Clients cache the last-known tunnel URL + pubkey for 30 days. Mobile keeps working; only first-run breaks. |
| Abuse: operator runs open tunnel for strangers | Auth-mode token is required whenever `--host` is not loopback. |

### 6.4 Relationship to existing work

This spec **reuses** rather than replaces:

- `cli/session_runtime.py` — shared turn loop
- `cli/headless.py` — JSON event model foundation
- `crowe_synapse_engine/memory.py` — thread-safe memory store (already done)
- `control_plane/gateway.py` — model entitlement + metering pattern
- `providers/*.py` — entire provider layer
- `tools/*.py` — entire tool registry
- `cli/dual_mode.py` — multi-stream rendering pattern (mobile's streaming UI cribs from it)

The new code is primarily the HTTP/WS surface, the push relay, and the mobile apps.

## 7. Open decisions for Michael

These are the architectural calls that should be made before Phase 1 kicks off, not during:

1. **Control Plane's role.** Do we extend the existing Railway Control Plane with home-registry endpoints, or stand up a separate `home.crowelogic.com` service that talks to the same Neon DB? Extending is faster but couples Home to the SaaS release cadence.
2. **Tunnel provider.** Cloudflare is the default in this spec. Tailscale Funnel is a viable alternative if you want no custom domain infrastructure, though it requires clients to install Tailscale (worse UX). Decision.
3. **Auth model for Phase 1.** Token-based (simpler, CLI-friendly) or OAuth-first (mobile-ready, more upfront complexity)? This spec assumes token in Phase 1, OAuth in Phase 2.
4. **Mobile platform priority.** iOS first (your users, Apple ecosystem) or Android first (broader reach, Tailscale users skew Android)? Both eventually, the question is order.
5. **Revenue bundling.** Is Home a free add-on for existing paid plans, or a distinct $29 SKU? This spec proposes both (included with Studio/Lab, standalone otherwise) but one of them has to be picked as primary.
6. **`nemoclaw_shell` relationship.** If the operator's server is also the sandbox host, Talon's sandbox problem is solved for free — the same machine runs both halves. Does the spec need a section on "Home serves as Talon's sandbox"? (Arguably yes; this would make Home the canonical Talon deployment target.)
7. **Relationship to Crowe Studio.** Studio is multi-camera capture on your hardware. Home is multi-agent serving on your hardware. They're the same pattern, pointed at different workloads. Worth converging the infrastructure (shared daemon, shared tunnel, shared OS-service unit) or keep separate?

## 8. Rough scope estimate

| Phase | Engineer-weeks (solo) | Engineer-weeks (with help) |
|---|---|---|
| Phase 1 (`crowe-logic serve` + LAN client) | 2.5 | 1.5 |
| Phase 2 tunnel + discovery | 1.5 | 1 |
| Phase 2 iOS app | 3 | 2 |
| Phase 2 Android app | 3 | 2 |
| Phase 2 web client | 1.5 | 1 |
| Phase 2 push relay infra | 1 | 0.5 |
| **Total** | **~12.5 weeks** | **~8 weeks** |

Phase 1 ships alone; Phase 2 has four sub-deliverables that parallelize well.

## 9. Recommendation

**Do not start until the Railway SaaS launch completes.** `DEPLOYMENT.md` says that's this week's goal. Home is additive and deserves focus.

**Once the SaaS is live, Phase 1 is the right first investment.** The long-running server mode unlocks:
- Overnight agent runs (the current pain point)
- Remote CLI (`crowe-logic chat --server ...`) for a Crowe Logic developer who works from a MacBook and a desktop
- The foundation every Phase 2 client needs

**Phase 2 is the product bet.** Committing to Phase 2 is committing to build mobile apps, which is a real investment. Worth doing if the thesis that "self-hosted agents + mobile reach = differentiation worth paying for" holds up after Phase 1 dogfooding.

---

## Appendix A: BlueBubbles pattern cross-reference

For each BlueBubbles pattern we adopt, the direct Crowe Home equivalent:

| BlueBubbles | Crowe Home |
|---|---|
| Mac server hooks iMessage via AppleScript + Private API | Mac/Linux server runs `crowe-logic serve` over existing provider + tool layer |
| Cloudflare Tunnel auto-provisioned at setup | Same |
| Google OAuth → Firebase project for push | NVIDIA/Google SSO → Crowe Control Plane for registry + push relay |
| Firebase Cloud Messaging | FCM (Android) + APNs (iOS), relayed through Control Plane |
| "Private API" optional power-user tier | "Private tools" — operator-registered tools (own code, DB, calendar) exposed only inside their session |
| Donate model | $29/mo Home SKU or bundled with Studio/Lab |
| Server update auto-download | Same, via `crowe-logic upgrade` |
| Client auto-update via app store | Same for iOS/Android; web is always current |

## Appendix B: Sample `crowe-logic serve` session

```
$ crowe-logic serve
  Crowe Home starting...
  data dir: /Users/crowelogic/.crowe-logic/home
  sqlite:  memory.db  (WAL)  4 sessions, 127 turns
  tools:   48 registered
  models:  37 / 48 live (Neon ok, Ollama ok)

  Bind:       127.0.0.1:8787
  Auth:       token (1 active)
  Tunnel:     off

  Operator client: crowe-logic chat --server http://127.0.0.1:8787 --token <...>
  Local web:       http://127.0.0.1:8787/app

  READY. Ctrl-C to stop.
```
