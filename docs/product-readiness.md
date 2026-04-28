# Crowe Logic Platform: Product Readiness Report

Status: canonical. Refreshed 2026-04-27. Replaces the 2026-04-21 verdict.
Tracks readiness across three gates (Closed Beta, Public Launch, Scale)
with explicit entry criteria for each.

## Verdict at the top

| Gate | Status | Remaining |
|---|---|---|
| Closed Beta | **Achieved** | Operator-onboarded users live; signup path exists |
| Public Launch | **One blocker** | Stripe webhook reconciliation |
| Scale | **Not started** | Multi-tenant chat backend, oncall, observability |

Headline: the platform is operationally ready for closed beta today and
within roughly one focused week of being public-launch ready. Scale is a
deliberate post-launch investment, not a launch blocker.

## What changed since 2026-04-21

The Path C launch (commit `46ba2f5` on 2026-04-27) cleared two of the
three blockers that previously gated public launch.

| Previous blocker | Status now | Evidence |
|---|---|---|
| Subscription enforcement in the CLI | **Cleared** | `cli/headless.py` per-turn credit metering, fire-and-forget after each done event |
| Self-serve signup flow | **Cleared** | `POST /api/auth/start-free` issues an instant free PAT in the control plane |
| Billing webhook reconciliation | **Open** | `control_plane/billing.py` receives Stripe events; reconciliation into active-plan state changes still incomplete |

Also shipped since the last readiness review:

- RAG layer: `crowe-knowledge` vector store + populator + retrieval probe (PR #16, today).
- CroweLM Prime LoRA fine-tuning pipeline: curate + Azure FT (PR #14).
- Hosted Crowe Research Engine endpoint, metered through credits (PR #9).
- Crowe Logic Workstation IDE rebrand working end-to-end on Apple Silicon.
- IDE extension dedicated chat webview, model picker, walkthrough.

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

**Gate 1 status: ACHIEVED as of 2026-04-27.** Operator onboarding new users
to the platform without manual intervention from this point on.

## Gate 2: Public Launch. Entry criteria

| Criterion | Status | Effort if open |
|---|---|---|
| Self-serve subscribe and upgrade | **Ready** (config-driven tiers) | n/a |
| Stripe webhook reconciliation | **Open** | ~3 focused days |
| Public crowecode.com landing live | **In flight** | DNS work in `vscode/scripts/namecheap_dns_setup.py` |
| Cancel and refund flow documented | **Open** | ~1 day |
| Public-tier metering enforcement | **Ready** (Path C) | n/a |
| Public docs site | **Open** | ~3 days |
| Status page | **Open** | ~1 day |
| Public support channel (email or in-app) | **Open** | ~1 day |
| Security disclosure policy | **Open** | ~half day |
| Terms of service + privacy policy | **Open** | ~1 day with template |

**Gate 2 status: ONE TECHNICAL BLOCKER + a handful of operational items.**
Realistic path to Public Launch is one focused week if the operator
sequences these in order. The webhook reconciliation is the only item
that could surface unexpected complexity.

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

**Gate 3 status: DELIBERATELY DEFERRED.** Most of these items become useful
only after Public Launch generates real traffic. Tracking them now so
nothing surprises us when traffic arrives.

## Risk register

Risks that could move a gate verdict if they materialize.

### Provider lock-in on a single primary
Most credit-metered traffic flows through one primary provider. A pricing
or terms change there reshapes unit economics. Mitigation: BYOK tier
already exists; expand model registry coverage on secondary providers
under `Next` lane in the roadmap.

### Trademark for "Crowe Code" and "Crowe Studio"
Filing not yet complete. Risk that a name conflict surfaces post-launch
and forces a rebrand. Mitigation: trademark search before public launch
(item in roadmap `Next`).

### Stripe webhook race conditions
Webhook reconciliation under concurrent payment events is the only
remaining technical blocker. Solid library coverage but worth a focused
test pass with replayed Stripe events before declaring Gate 2 closed.

### Single-operator bus factor
Self-explanatory. The architecture documents and machine-checked
boundary tests reduce the cost of onboarding a second operator, but the
risk remains until that happens.

## Operational readiness

Items not gated by feature work but required for any commercial operation.

| Item | Status |
|---|---|
| Backups: Postgres dump cadence | **Ready** (`backups/` ignored from git, dump cron documented) |
| Secrets management | **Ready** (`~/.env.secrets` chmod 600, sourced from shell) |
| Deploy targets configured | **Ready** (Railway, Render, Fly.io, Docker, fly.toml in repo) |
| Monorepo CI | **Partial** (GitHub Actions present, full matrix coverage pending) |
| Code copyright headers on all proprietary modules | **Ready** (Studio lockdown applied broadly) |

## What "ready" means in this report

These criteria define readiness as commercial-operability, not as
feature-completeness. A criterion is `Ready` when a customer can hit
that surface without operator intervention. A criterion is `Open` when
operator intervention is currently required.

## Next review

Weekly via `./scripts/weekly-readiness.sh`, every Monday during the
working week. The next scheduled review is 2026-05-04.
