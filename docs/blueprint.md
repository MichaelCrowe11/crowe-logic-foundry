# Crowe Logic Platform: Blueprint

Status: canonical. Written 2026-04-27. The integrative narrative across
architecture, business, and launch. Each section is a one-page summary
that links into deeper docs.

## What this is

Crowe Logic is a multi-model AI platform. It runs two flagship language
models concurrently against the same prompt, synthesizes their outputs,
and exposes the result through a custom IDE (Crowe Code), a CLI
(`cl-agent`), a research engine (Crowe Research), and a content production
suite (Crowe Studio). All four surfaces share one credit ledger, one auth
system, one model gateway, and one tool registry. That shared spine is
what turns four useful products into one platform.

## Why this matters now

The market for AI coding tools and AI research assistants is fragmented
into single-model wrappers. Cursor wraps one model. Claude.ai wraps one
model. ChatGPT wraps one model. The wedge is straightforward: nobody
runs two flagship models concurrently with a synthesis layer on top, and
nobody bundles a content-production pipeline that uses the same model
infrastructure as the IDE. Crowe Logic does both, today.

## Architecture

One sentence: a shared spine of auth, billing, model gateway, tool
registry, and credit ledger powers four product surfaces.

```
+-------------------- Surfaces --------------------+
|  Crowe Code     CroweLM     Research     Studio  |
|  (IDE)          (model)     (CLI/API)    (video) |
+--------------------------------------------------+
                       |
+--------------- Shared spine --------------------+
|  Auth + PAT          Billing + Stripe           |
|  Model gateway       Tool registry (98)         |
|  Credit ledger       MCP integration (5800+)    |
|  Memory + threads    HUD + cost telemetry       |
+--------------------------------------------------+
                       |
+----------- Provider matrix (multi-model) -------+
|  Primary providers (premium)                     |
|  Secondary providers (cost-optimized)            |
|  BYOK (customer-supplied keys)                   |
+--------------------------------------------------+
```

For the layer contract and dependency rules, see `docs/ARCHITECTURE.md`.
For subsystem detail, see `STUDIO_ROADMAP.md` (Studio) and the
`docs/superpowers/specs/` directory (per-subsystem design history).

### Five subsystems

1. **Crowe Code (IDE)**. A VS Code fork with a dedicated chat webview,
   model picker, walkthrough, file context awareness, and a stop button.
   Ships from `deploy/ide/extensions/crowe-logic/`. Public surface at
   `crowecode.com`.
2. **CroweLM (model)**. The Crowe Logic-native fine-tune. Path:
   curate corpus, fine-tune on Azure AI Foundry, export to gateway.
   Lives in `training/`. Latest tier is CroweLM Prime (PR #14).
3. **Research Engine (CLI/API)**. Four-stage cached pipeline for deep
   research with citation export. Hosted endpoint metered through
   credits (PR #9). Lives in `control_plane/_research_engine/`.
4. **Studio (content)**. Multi-camera capture, audio-sync alignment,
   shot-selector, EDL renderer, tenant routing. Lives at the top level
   (`STUDIO_*` files). Currently at v0.9.0.
5. **Control plane (auth + billing + metering)**. FastAPI service with
   Postgres backing. Issues PATs, meters credits, receives Stripe
   webhooks, serves the dashboard. Lives in `control_plane/`.

### How the spine ties them together

A single user identity owns one credit balance. Every turn through
any surface decrements that balance through the gateway. Tier
membership controls credit ceiling, model access, and tool access.
This is what makes the platform claim accurate: the customer doesn't
manage four logins, four bills, or four credit budgets.

## Business

Multi-model orchestration as the priced product. Pricing rationale lives
in `docs/pricing-strategy.md`. Revenue model lives in
`docs/revenue-projection.md`.

### Tier table

| Tier | Price | Credits | Persona |
|---|---|---|---|
| BYOK | $19/mo | unlimited (customer keys) | Cost-conscious power user |
| Personal | $29/mo | 750/mo | Solo operator, mycologist, researcher |
| Pro | $99/mo | 3,000/mo | Power user living in dual mode |
| Team | $49/seat (3+ seats) | 1,500/seat pooled | Small shops, content teams, labs |
| Enterprise | $250/mo floor | unlimited | Compliance and audit needs |

Blended ARPU: ~$48 per account in Y1.

### Y1 revenue scenarios (from `revenue-projection.md`)

| Scenario | End-of-Y1 MRR | Y1 Total Revenue | Y1 Gross Margin |
|---|---|---|---|
| Conservative | $12,400 | ~$78,500 | ~$47,000 (60%) |
| Moderate | $41,300 | ~$228,000 | ~$141,000 (62%) |
| Aggressive | $122,800 | ~$615,000 | ~$394,000 (64%) |

All three pay back fixed costs within ninety days and remain self-funded
through Y1.

### Distribution channels (already operator-controlled)

- Mycology audience via Southwest Mushrooms and The Mushroom Grower.
- Developer audience via Skool community and the operator's GitHub
  presence.
- Inbound from public Crowe Logic Inc surfaces.
- Direct B2B to mycology and biotech labs.

No paid acquisition assumed in any Y1 scenario.

### Unit economics

Per-turn cost is computed in `cli/cost_model.py` against the live model
rate card. The credit ledger ensures customer billing never decouples
from upstream cost. BYOK tier offloads provider cost to the customer,
keeping margin near 100% on subscription revenue. Personal and Pro
tiers carry the bulk of credit-purchased turns, with the synthesis pane
intentionally priced to assume both models are always running.

## Launch

GTM sequence to Public Launch and through the first ninety days. Detail
in `docs/launch-plan.md`.

### Who the launch is for

Three personas, ranked by likelihood of converting in the first 30 days:

1. **Power user developers** who already pay for one of (Cursor, Claude,
   ChatGPT) and recognize the value of multi-model. Persona test: do
   they paste the same prompt into two tabs and compare? If yes, this
   is who Crowe Code is for.
2. **Researchers and mycologists** in the operator's existing audience,
   who buy on credibility and platform authority more than feature
   parity.
3. **Small content teams** producing multi-camera content who would
   pay for Studio-grade automation. Pulled in via the Pro tier with
   Studio extras.

### First ten minutes after signup

The conversion gate. The first ten minutes have to demonstrate something
the customer cannot get from a single-model tool. Sequence:

1. PAT issued instantly via `POST /api/auth/start-free`.
2. Walkthrough opens in Crowe Code IDE with the chat webview pinned.
3. Model picker shows the dual-mode pair.
4. First prompt runs both models, displays the synthesis pane.
5. HUD shows cost and credit delta in real time.

If steps 4 and 5 don't feel different from a single-model tool, the
customer cancels in the trial window. The synthesis pane is the moment
of conviction.

### Channels for Public Launch week

- Personal Twitter / X announcement from the operator.
- Skool community announcement.
- Show HN with a focused angle (multi-model orchestration, not "yet
  another AI IDE").
- One-paragraph pitch to four target newsletters in the AI tooling
  space.

No paid spend in launch week. Paid is gated until conversion data
proves the tier mix.

### Conversion path

Free PAT -> walkthrough -> first dual-mode turn -> hit free credit
ceiling -> upgrade prompt with the relevant tier (Personal for
individuals, Pro if they used dual mode more than five times, Team if
they invited a teammate). Clean cancel and refund flow per Gate 2
entry criteria.

## Risks

Risks that change the verdict if they materialize. Detail in
`product-readiness.md` risk register.

| Risk | Direction | Mitigation |
|---|---|---|
| Provider lock-in on a single primary | Margin compression | BYOK tier; expand secondary provider coverage |
| Trademark conflict on Crowe Code or Studio | Forced rebrand | Trademark search before paid spend |
| Stripe webhook race conditions | Gate 2 slip | Replayed-event test pass before declaring Gate 2 closed |
| Single-operator bus factor | All gates | Architecture contract tests reduce onboarding cost |

## What this blueprint does not cover

- Org chart, hiring plan, contractor management. Deferred until past
  solo-operator stage.
- Detailed financial model. See `revenue-projection.md`.
- Subsystem-level technical detail. See per-subsystem specs in
  `docs/superpowers/specs/`.
- Trademark and entity structure. Tracked separately under Crowe
  Logic Inc legal.

## Related documents

- `docs/product-readiness.md`. Gate-by-gate readiness verdict.
- `docs/roadmap.md`. Unified Now/Next/Later with milestone anchors.
- `docs/ARCHITECTURE.md`. Layer contract and dependency rules.
- `docs/launch-plan.md`. GTM detail.
- `docs/revenue-projection.md`. Y1 financial model.
- `docs/pricing-strategy.md`. Tier rationale.
- `docs/views/investor.md`. Sanitized view of this blueprint plus the
  readiness verdict.
- `docs/views/customer.md`. Positioning narrative without internal
  detail.
