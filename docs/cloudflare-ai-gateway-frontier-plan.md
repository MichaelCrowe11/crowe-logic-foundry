# Cloudflare AI Gateway — Frontier Model Plan

**Status:** Plan / not yet executed
**Date:** 2026-06-14
**Owner:** Michael Crowe

## Why this exists

The Cloudflare **Workers AI** lane is already wired (see the 5 `*-edge` tiers in
`config/models.extra.json` and the standalone `crowe-cf` CLI). That lane only
reaches Cloudflare-**hosted open models** (`@cf/...`: Llama, Kimi, Nemotron,
GPT-OSS, GLM).

It does **not** reach the frontier third-party models shown in the Cloudflare
dashboard catalog — **Claude Opus 4.8, Claude Fable 5, GPT-5.5, Grok 4.3,
DeepSeek V4 Pro**. Those are *partner* models that route through a different
Cloudflare product: **AI Gateway**. This document is the plan to add them.

## Key distinction (verified 2026-06-14)

| Surface | Endpoint | Models | Token |
|---|---|---|---|
| Workers AI (built) | `…/accounts/{acct}/ai/v1/chat/completions` | `@cf/...` open models | `CLOUDFLARE_API_TOKEN` (Workers AI scope) — already works |
| AI Gateway (this plan) | `https://gateway.ai.cloudflare.com/v1/{acct}/{gateway}/compat/chat/completions` | partner frontier models | gateway token + (BYOK or CF-billed) |

The Workers AI token returns `Authentication error` against partner models — they
are simply not on that surface. Confirmed empirically.

## Architecture

AI Gateway's **OpenAI-compatible (`/compat`) endpoint** lets one base URL reach
many providers. Requests use **provider-prefixed model ids**:

```
POST https://gateway.ai.cloudflare.com/v1/{account_id}/{gateway_id}/compat/chat/completions
Headers:
  Authorization: Bearer <see billing model below>
  cf-aig-authorization: Bearer <gateway auth token>   # if gateway auth is enabled
  Content-Type: application/json
Body:
  { "model": "anthropic/claude-opus-4.8", "messages": [...] }
  { "model": "openai/gpt-5.5", ... }
  { "model": "grok/grok-4.3", ... }
```

Because the surface is OpenAI-compatible, it plugs into the existing
`openai_compat` provider (`providers/hosted_openai.py`) with **zero new provider
code** — exactly like the Workers AI lane. The only new pieces are config + a
gateway + a billing decision.

## Billing models — and the sourcing-rule constraint

There are two ways to pay for partner models, and they are **not equal** under
Crowe's cloud-provider-exclusive sourcing rule (frontier models only via
Azure/GCP/AWS/IBM-class clouds, never direct vendor APIs):

1. **BYOK (bring your own key)** — you store each vendor's own API key (Anthropic,
   OpenAI, xAI) in the gateway; Cloudflare proxies + observes. ⚠️ **This violates
   the sourcing rule** — it *is* the direct vendor API, just proxied. Avoid.

2. **Cloudflare-billed partner models** — Cloudflare is the seller of record and
   bills your Cloudflare account directly; no vendor keys involved. ✅ **This
   honors the sourcing rule** — Cloudflare is the cloud provider, not the vendor.
   This is the recommended path.

**Therefore: use Cloudflare-billed partner models, not BYOK.** This keeps the
"route via the cloud endpoint, never the direct vendor" posture intact and adds
Cloudflare as a *fifth* sanctioned cloud alongside Azure/GCP/AWS/IBM.

## Gating dependency (ties to the Startups billing email)

Cloudflare-billed partner models require a **valid payment method on the
Cloudflare account**. This is the same item as the open `startups@cloudflare.com`
"Add a Payment Method" email. So:

> **The frontier Gateway lane is blocked until the Cloudflare account has a
> confirmed payment method.** Resolving the billing email unblocks this plan.

(The Workers AI lane already built does *not* need this — open `@cf/...` models
run on the existing token within plan limits.)

## Execution steps (once billing is resolved)

1. **Create a gateway.** Dashboard → AI → AI Gateway → Create (name e.g.
   `crowe-frontier`). Note the `{gateway_id}`.
2. **Enable Authenticated Gateway** (recommended) → generates a gateway token →
   store as `CLOUDFLARE_GATEWAY_TOKEN` in `~/.env.secrets`. Sent as
   `cf-aig-authorization`.
3. **Confirm partner-model availability + exact slugs** for the account: hit the
   `/compat` endpoint with `anthropic/claude-opus-4.8` etc. and a 1-token probe
   (mirror the `crowe-cf`/`cf_smoke.py` pattern). Record the canonical ids — do
   not trust the dashboard display names blindly.
4. **Add env vars** to `~/.env.secrets`:
   ```
   export CLOUDFLARE_GATEWAY_ENDPOINT=https://gateway.ai.cloudflare.com/v1/9f3b1ed688d960bc9ea03569ca840dfd/crowe-frontier/compat
   export CLOUDFLARE_GATEWAY_TOKEN=<gateway token>
   ```
   Note: `hosted_openai.py` appends `/v1` only if the URL doesn't already end in
   it. The `/compat` path does **not** end in `/v1`, so it would become
   `…/compat/v1` — **wrong**. Either (a) pass the full
   `…/compat/chat/completions`-compatible base the OpenAI client expects, or
   (b) add a small `gateway_compat` branch / `skip_v1` flag to the provider.
   **This is the one code change the Gateway lane needs** — verify the exact base
   URL the OpenAI SDK wants and adjust provider URL handling accordingly.
5. **Add tiers** to `config/models.extra.json` (provider `openai_compat`,
   `endpoint_env: CLOUDFLARE_GATEWAY_ENDPOINT`, `api_key_env` for the bearer,
   `backend_name` = provider-prefixed id). Suggested:
   | label | backend_name |
   |---|---|
   | CroweLM Theory Edge | `anthropic/claude-fable-5` |
   | CroweLM Opus Edge | `anthropic/claude-opus-4.8` |
   | CroweLM Quasar Edge | `openai/gpt-5.5` |
   | CroweLM Crest Edge | `grok/grok-4.3` |
   The `cf-aig-authorization` header is not yet expressible in the JSON entry
   schema — see open question below.
6. **Cost caps.** AI Gateway supports rate-limiting + spend controls per gateway —
   set a monthly cap before routing real traffic, since partner models are
   metered at vendor-equivalent prices (far above the `@cf/...` open models).
7. **Smoke + register** exactly like the Workers AI lane: headless turn through
   `crowe-logic headless --model <tier>` proving end-to-end.

## Open questions / verification needed

- **Provider-id format**: confirm whether the account uses `anthropic/claude-…`,
  bare `claude-…`, or another scheme on `/compat`. Probe before wiring.
- **Two-header auth in the entry schema**: `hosted_openai.py` sends only a single
  `Authorization: Bearer`. The gateway may need *both* `Authorization` (provider
  routing) and `cf-aig-authorization` (gateway auth). If so, the provider needs a
  way to send the extra header — a small extension (e.g. `extra_headers_env` on
  the entry, or a dedicated `cf_gateway` provider kind). Scope this before step 5.
- **`/v1` suffix handling** (step 4 note) — the single confirmed code change.
- **Does the Crowe Logic account even have partner models enabled?** The dashboard
  lists them, but availability can be region/plan-gated. The step-3 probe answers
  this.

## Relationship to existing lanes

- **Azure remains primary** for frontier models (per sourcing strategy + the
  $150K credit path). This Gateway lane is a *second cloud* for the same frontier
  tier — resilience, not replacement.
- **Workers AI lane** (built) covers open-model failover.
- Together: Azure (primary) → Cloudflare Gateway (frontier failover) → Cloudflare
  Workers AI (open-model failover) → local Ollama. A genuinely multi-cloud chain.
