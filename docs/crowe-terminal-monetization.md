# Crowe Terminal Monetization

Status: draft. Aligns with `docs/pricing-strategy.md` (canonical) and
`config/customer_pricing.json`. Crowe Terminal is a *new acquisition
surface* for Crowe Logic, not a new product line — same tiers, same
credits, new install path.

## Premise

Crowe Terminal is a fork of Wave Terminal (Apache 2.0) re-skinned with
Crowe Logic branding and wired to the Foundry agent via a local
OpenAI-compatible bridge (`cli/openai_bridge.py` → `cli/headless.py`).

Because the upstream license is Apache 2.0 we **cannot** sell the
terminal binary itself — source must remain available. The terminal is
the channel; the agent is the product. Same model as Cursor → API,
GitHub Copilot → backend, Wave → Wave Cloud.

## Distribution

| Channel | Asset | Cost | Purpose |
|---|---|---|---|
| GitHub release | signed `.dmg` (arm64 + x64) | $0 | Developer-facing, indexed by search |
| Homebrew tap | `brew install --cask crowe-terminal` | $0 | Frictionless install for power users |
| crowecode.com | direct download + Pro signup CTA | $0 | Top-of-funnel conversion |
| Apple notarization | Developer ID + notarytool | $99/yr | Removes Gatekeeper warning, mandatory for trust |
| Future: Setapp / Mac App Store | sandboxed build | revenue share | Long-tail, post-launch consideration |

The `.dmg` ships with the bridge auto-spawn code but **does not bundle
Python or the Foundry repo**. On first launch the terminal:

1. Looks for `$CROWE_FOUNDRY_PATH` or `~/Projects/crowe-logic-foundry`
2. If missing, shows a "Connect Crowe Logic" panel with two buttons:
   - **Sign in to Crowe Logic** → opens browser to `crowelogic.com/onboard`,
     returns a PAT, configures the AI block to call our hosted bridge
     at `api.crowelogic.com/v1/chat/completions`.
   - **BYOK / Local foundry** → docs link explaining how to clone the
     foundry repo and set the env var.

This makes the **hosted path the default** for new users while
preserving local-only operation for power users.

## Pricing surfaces inside the terminal

The same four tiers from `customer_pricing.json` apply. The terminal
adds zero new SKUs. What changes is *where* tier features show up:

| Tier | What lights up in Crowe Terminal |
|---|---|
| BYOK ($19/mo) | All CroweLM models via local bridge using user's API keys. No hosted memory. No telemetry credits. |
| Personal ($29/mo) | Hosted CroweLM Auto + Apex + Titan. 750 credits/mo. 1-hour session memory. |
| Pro ($99/mo) | Adds Supreme + Oracle + Sovereign. Unmetered dual-mode. 5-hour session memory. Priority queue. |
| Team ($49/seat/mo, ≥3 seats) | Pooled credits, shared workspace state across teammates, admin cost reporting in the AI block sidebar. |
| Enterprise (custom, $250 floor) | Self-hosted bridge option, SSO at the terminal sign-in step, audit log shipped to customer's SIEM. |

## Conversion funnel

The IDE bundles at $499/mo Supreme tier. Crowe Terminal is the
**low-commitment cousin** — same agent, no IDE switch required. Funnel
intent:

```
GitHub stars / Brew installs (free)
        ↓
First-launch BYOK (token cost on user)
        ↓
"Sign in for hosted" upsell at session start (Personal $29)
        ↓
Heavy-usage upsell when burst cap hits (Pro $99)
        ↓
"Add IDE for $400 more" cross-sell (Supreme $499)
```

Critical: surface the **upsell triggers in the terminal UI**, not
in email. Specifically:

- When a BYOK user hits 50 dual-mode turns in a week → toast: "You'd
  save ~$40 on tokens this month with Personal. [Sign up]"
- When a Personal user hits the 200-turn burst cap → modal: "Burst cap
  reached. Upgrade to Pro for unmetered dual mode."
- When a Pro user runs Supreme via BYOK with their own Anthropic key →
  toast: "CroweLM Supreme is included free in our IDE bundle ($499/mo).
  Save $X/mo over your current Anthropic spend."

## Acquisition tactics specific to the terminal

1. **Open-source it loudly.** Apache 2.0 fork already exists at
   `MichaelCrowe11/crowe-terminal`. Submit to:
   - `awesome-terminals`, `awesome-electron`, `awesome-cli` lists
   - HN Show post timed for Tue-Thu morning EST
   - r/MacApps, r/commandline, r/programming
   - Product Hunt (don't do PH if HN goes well — pick one)

2. **Wave Terminal users.** Wave has ~10K weekly actives. Reach via:
   - Wave Discord with a "We forked Wave with a real AI agent" post
     (be respectful of the upstream maintainers — call it a friendly fork)
   - Crosspost to r/wavetermdev

3. **Crowe Logic Foundry users.** Existing foundry CLI users are the
   warmest leads. Email blast: "If you live in `crowe` on the CLI, you
   want this terminal — same agent, no context switch."

4. **Content angle.** "Why I built a terminal that has Claude Opus 4.7
   built into it" → blog post → cross-post to Dev.to + Medium. Embed
   asciinema demo of dual-mode in action.

5. **Pricing page lift.** Add Crowe Terminal as a row to the
   crowelogic.com pricing matrix: "Included: AI Terminal, AI IDE
   (Pro+), Hosted Foundry CLI". Makes the bundle look richer at the
   same price.

## What stays free forever

- The terminal binary and source (legal requirement under Apache 2.0).
- The bridge code (`cli/openai_bridge.py`, `emain-foundry-bridge.ts`).
- BYOK mode — bring your own Anthropic / OpenAI key, run the bridge
  locally, get full agent capabilities. This is the "developer
  goodwill" tier; we do not chase users who run BYOK forever.
- The terminal-only feature set (split panes, themes, browser block,
  etc.) — these are upstream Wave features and stay open.

## What's behind the paywall

- Hosted CroweLM Supreme/Sovereign access (we eat the Anthropic cost).
- Cross-machine session memory and history sync.
- Hosted tools that need a server: web search rate-limited by us,
  Crowe Vision RAG, Foundry Studio routing.
- Team / Enterprise admin features (audit log, cost dashboard, SSO).
- Bundled IDE access at the Supreme tier.

## Decision points still open

1. **Notarization timing.** Notarized build requires a $99 Apple
   Developer ID enrollment and a 24-hour Apple review for the first
   submission. Worth doing before HN launch but not before private
   beta. Decide by 2026-05-15.

2. **Auto-update channel.** Wave uses electron-builder's update
   manifest hosted on S3. We need either an S3 bucket on
   `dl.crowelogic.com/terminal/` or a GitHub Releases-based feed. The
   GitHub path is free and works fine for v0.

3. **Telemetry posture.** Wave Terminal phones home for crash reports
   and feature usage by default. We need to (a) replace their endpoint
   with ours and (b) be explicit about what we collect — the IDE has a
   no-tech-stack-exposure rule that the terminal should match. Pick
   privacy posture by 2026-05-04.

4. **Free-tier rate limit.** BYOK is "free" because the user pays
   provider costs, but our bridge still spawns Foundry processes that
   consume our memory/CPU on the user's machine. No server cost — so
   no rate limit needed. Skip this concern.

## North-star numbers (preliminary, validate before publishing)

If 1% of monthly Crowe Terminal active users convert to Personal at
$29/mo and the GitHub release pulls 10K downloads in the first month:

- 10,000 downloads × 30% activation × 1% Personal conversion = 30 paying users / month at $29 = $870/mo recurring
- Same funnel hitting Pro at 0.3% = 30 × $99 = $2,970/mo recurring
- Combined Year 1 ARR contribution from terminal channel alone: $46K
  (assuming flat 1% monthly growth, no churn)

This is sandbagged on purpose — these numbers exist to set a floor,
not a ceiling. Real upside is if the terminal becomes a top-100
GitHub trending repo in the first week, which 10x's the funnel.
