# Crowe Logic Platform: Product Readiness Report

Status: canonical. Refreshed 2026-04-27 (evening). Supersedes the
2026-04-27 morning verdict and the 2026-04-21 verdict before it.
Tracks readiness across three gates (Closed Beta, Public Launch,
Scale) with explicit entry criteria for each.

## Verdict at the top

| Gate | Status | Remaining |
|---|---|---|
| Closed Beta | **Achieved** | Operator-onboarded users live; signup path exists |
| Public Launch | **Operational items only, no technical blockers** | DNS routing, marketing site, status page, ToS/privacy, support inbox |
| Scale | **Not started, deliberately deferred** | Multi-tenant chat backend, oncall, observability |

Headline: the only remaining technical blocker for Public Launch
(Stripe webhook reconciliation) **shipped today as commit `076badd`**
with twelve passing regression tests. What stands between the
platform and a real public launch is now operational copy, DNS
plumbing, and external surfaces (docs site, status page, ToS),
not engineering work that could surprise us.

## What shipped today (since the morning readiness report)

| Commit | What it cleared |
|---|---|
| `076badd` | **Gate 2 technical blocker.** Atomic Stripe webhook idempotency, replay-safe credit grants, `customer.subscription.created` handler, dead-code cleanup. 12 new regression tests. |
| `b77d2bb` | `patch-local-install.sh` handles re-rebrand cleanly. Detects already-renamed helper bundles instead of silently leaving them stale. Disables CFBundleIdentifier change that was breaking signed launch on macOS Sequoia. |
| `f8979b5` | IDE rebrand theme parity. Light theme grew from 43 lines to 221 with full coverage; dark expanded by 40 keys. Removed duplicate theme registration between extensions; fixed broken titlebar image (was pointing at a file that did not exist). |
| `e182a5c`, `8dc1d7e`, `b4a96fa`, `21c543e` | LanguageModelChatProvider bridge to Foundry. Required for VS Code 1.117 chat compatibility. Documents the picker-bypass workflow for the @crowe participant. |
| `d7e6ef7` | Removed the duplicate `crowe-logic.signIn` registration that was silently killing the rebrand extension's activation, which had been masking theme failures across multiple install/restart cycles. |
| `1324474` | `tests/test_rebrand_extensions.py` (13 tests) and `scripts/verify-rebrand.sh` (17 checks). Machine-checks the regression class that ate hours of debugging today. |
| `1c6cb22` | Diamond mark and face avatar separated into distinct asset roles, `croweLogic.chatPersona` setting added. The empty-circle chat icon issue is closed. |

## Gate 1: Closed Beta. Entry criteria

| Criterion | Status |
|---|---|
| Single-model turn dispatch across providers | **Ready** |
| Dual-mode side-by-side | **Ready** |
| Synthesis pane (merge / judge / diff) | **Ready** |
| Tool registry (98 tools) | **Ready** |
| `/replay` and `/fork` | **Ready** |
| Live HUD with cost + credits | **Ready for primary provider** (others need usage publishing) |
| Prompt caching on primary provider | **Ready** |
| Memory thread safety | **Ready** |
| Tool-arg resilience (`content_b64`) | **Ready** |
| Cost model | **Ready** (periodic rate card refresh) |
| Customer pricing config | **Ready** (config-driven via `config/customer_pricing.json`) |
| Control plane auth | **Wired**, exercised by closed-beta cohort |
| CLI subscription enforcement | **Wired** (Path C) |
| Self-serve signup PAT | **Wired** (Path C) |
| Agent profiles (YAML) | **Ready** (one profile shipped, pattern proven) |
| MCP integration | **Ready** (5,800+ servers reachable) |
| iTerm2 control surface | **Ready** (macOS-only, documented) |
| Crowe Logic Code IDE: branding correctly applied end-to-end | **Ready** (verified today via verify-rebrand.sh, 17/17 pass) |

**Gate 1 status: ACHIEVED as of 2026-04-27.** Operator onboarding new
users to the platform without manual intervention from this point on.

## Gate 2: Public Launch. Entry criteria

| Criterion | Status | Effort if open |
|---|---|---|
| Self-serve subscribe and upgrade | **Ready** (config-driven tiers) | n/a |
| Stripe webhook reconciliation | **Ready** (atomic idempotency, replayed-event tests) | done today |
| `crowecode.com` DNS configured | **Open** | scripted, ready to execute (`vscode/scripts/namecheap_dns_setup.py`) |
| Marketing site at `crowecode.com` | **Open** | next step in this work session |
| Public docs site | **Open** | ~3 days (render existing `docs/` markdown) |
| Status page | **Open** | ~1 day |
| Cancel and refund flow documented | **Open** | ~1 day |
| Public support channel (email + form) | **Open** | ~1 day |
| Security disclosure policy + `SECURITY.md` | **Open** | ~half day |
| Terms of service + privacy policy | **Open** | ~1 day with template |
| Public-tier metering enforcement | **Ready** (Path C) | n/a |
| Trademark search for "Crowe Code" / "Crowe Logic" | **Open** | dependency on legal counsel |

**Gate 2 status: NO TECHNICAL BLOCKERS.** The remaining items are
operational and content. Realistic path to Public Launch is one focused
week if the operator sequences these in order. Highest priority: DNS +
marketing site live (the next concrete step in the current work
session). Trademark is the only item that can surface a forced rebrand
post-launch, so worth completing before paid spend.

## Gate 3: Scale. Entry criteria

| Criterion | Status |
|---|---|
| Multi-tenant chat backend with tenant isolation | **Not started** |
| On-call rotation defined | **Not started** (solo operator) |
| Incident playbook drafted | **Not started** |
| Observability: credit spend, provider errors, latency p95 | **Partial** (HUD exists, no aggregated dashboard) |
| Support SLO published | **Not started** |
| Provider failover policy | **Partial** (multi-provider, no automatic failover under provider error) |
| Disaster recovery: snapshot + restore tested | **Not started** |
| Capacity model: cost per active user, scaling triggers | **Partial** (`cli/cost_model.py` has unit economics, no scaling triggers) |

**Gate 3 status: DELIBERATELY DEFERRED.** Most of these items become
useful only after Public Launch generates real traffic. Tracking them
now so nothing surprises us when traffic arrives.

## Known launch-day caveats (not blockers, worth flagging)

### VS Code 1.117 chat-participant default-model bias

VS Code 1.117 hardcodes its default-language-model resolver to vendor
`copilot` with `isDefaultForLocation.panel === true`. There is no
public escape hatch. Our LM provider registers under `crowe-logic`
vendor, which means the @crowe chat participant in VS Code's main
chat panel hits "Language model unavailable" until the user manually
picks CroweLM Supreme from the chat panel's model picker dropdown.
After that pick, the userSelectedModelId path bypasses the default
lookup and chat works normally.

The dedicated Crowe Logic chat webview (gold mark in activity bar)
bypasses VS Code's chat infrastructure entirely and works without
the picker step. We treat that webview as the canonical chat
surface; @crowe is the secondary surface for users who prefer
VS Code's main chat panel.

Mitigations in flight:
- Walkthrough step 2 ("Pick a model tier") guides the user through
  the model picker on first run.
- The `croweLogic.chatPersona` setting documents the chat surfaces.
- A future enhancement could auto-set `userSelectedModelId` on first
  activation so the picker step disappears.

### Operator must mount the Elements drive at launch time

Extensions live at `/Volumes/Elements/crowe-work/vscode-extensions-backup`
via a symlink chain through `~/.vscode/extensions`. If the drive is
unmounted when the IDE launches, extensions fail to load and the
walkthrough/themes/chat all break. This is intentional (saves SSD
space) but worth documenting for any future second-operator scenario.

## Risk register

Risks that could move a gate verdict if they materialize.

### Provider lock-in on a single primary
Most credit-metered traffic flows through one primary provider. A pricing
or terms change there reshapes unit economics. Mitigation: BYOK tier
already exists; expand model registry coverage on secondary providers
under `Next` lane in the roadmap.

### Trademark for "Crowe Code" and "Crowe Logic"
Filing not yet complete. Risk that a name conflict surfaces post-launch
and forces a rebrand. Mitigation: trademark search before paid spend.

### Single-operator bus factor
Self-explanatory. The architecture documents and machine-checked
boundary tests reduce the cost of onboarding a second operator, but the
risk remains until that happens.

### VS Code upstream changes to chat infrastructure
The chat-participant + LM-provider plumbing depends on VS Code 1.117
internals (proposed `chatProvider` API, the `extensionEnabledApiProposals`
allowlist in `product.json`, hardcoded `vendor === "copilot"` default
lookup). A 1.118 release could shift any of these. Mitigation: the
verify-rebrand.sh live checks parse the exthost log for activation
errors, so a regression surfaces on the first run after upgrade.

## Operational readiness

Items not gated by feature work but required for any commercial operation.

| Item | Status |
|---|---|
| Backups: Postgres dump cadence | **Ready** (`backups/` ignored from git, dump cron documented) |
| Secrets management | **Ready** (`~/.env.secrets` chmod 600, sourced from shell) |
| Deploy targets configured | **Ready** (Railway, Render, Fly.io, Docker, fly.toml in repo) |
| Monorepo CI | **Partial** (GitHub Actions present, full matrix coverage pending) |
| Code copyright headers on all proprietary modules | **Ready** (Studio lockdown applied broadly) |
| IDE rebrand machine-checked | **Ready** (`scripts/verify-rebrand.sh`, 17/17) |
| Billing webhook regression coverage | **Ready** (`tests/test_billing_webhook.py`, 12/12) |
| IDE extension regression coverage | **Ready** (`tests/test_rebrand_extensions.py`, 13/13) |

## Verification commands as of this report

```
$ ./scripts/verify-rebrand.sh
RESULT  17 passed of 17 total

$ .venv/bin/python -m pytest tests/test_rebrand_extensions.py tests/test_billing_webhook.py -q
25 passed in 0.13s

$ .venv/bin/python -m pytest tests/ -q
388 passed, 25 failed (test_nemoclaw.py env-config, unrelated)
```

## What "ready" means in this report

These criteria define readiness as commercial-operability, not as
feature-completeness. A criterion is `Ready` when a customer can hit
that surface without operator intervention. A criterion is `Open` when
operator intervention is currently required.

## Next concrete step

DNS routing for `crowecode.com` via the in-tree Playwright script
(`vscode/scripts/namecheap_dns_setup.py`). Once DNS resolves and the
marketing site renders the existing `docs/views/customer.md`, Gate 2
is effectively closed and we can declare a Public Launch date.

## Next review

Weekly via `./scripts/weekly-readiness.sh`, every Monday during the
working week. The next scheduled review is 2026-05-04, which is the
target Public Launch date if the remaining operational items land
this week.
