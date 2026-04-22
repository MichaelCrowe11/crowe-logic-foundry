# Crowe Logic Foundry: Product Readiness Report

Status: canonical. Written 2026-04-21. Assesses shipping readiness
across feature completeness, testing, infrastructure, security, and
go-to-market.

Summary verdict at the top: **ready for closed beta, not ready for
public launch.** The product works end-to-end for a solo operator
on their own machine. The gaps are in billing integration, production
SSO, observability, and a handful of testing artifacts that block
a pay-walled public opening.

## Feature readiness matrix

| Feature | Status | Blocks |
|---|---|---|
| Single-model turn dispatch (7 providers) | **Ready** | none |
| Dual-mode side-by-side | **Ready** | none |
| Synthesis pane (merge / judge / diff) | **Ready** | none |
| Tool registry (98 tools) | **Ready** | none |
| NemoClaw sandbox | **Ready for power users** | public-tier quota metering |
| Brev Launchable | **Unverified** | Brev ingestion test |
| /replay and /fork | **Ready** | none |
| /model resolve diagnostic | **Ready** | none |
| Live HUD with cost + credits | **Ready for Anthropic** | other providers need usage publishing |
| Anthropic prompt caching | **Ready** | none |
| Memory thread safety | **Ready** | none |
| Tool-arg resilience (content_b64) | **Ready** | none |
| Cost model (upstream) | **Ready** | periodic rate card refresh |
| Customer pricing config | **Config ready** | billing wiring, webhook handlers |
| Control plane auth | **Wired, unverified** | closed-beta test with real users |
| Control plane Stripe integration | **Scaffolded** | subscription enforcement path not exercised from CLI |
| Agent profiles (YAML) | **Ready** | one profile shipped (crowe-talon), pattern proven |
| Public records tools | **Ready** | AZ-specific, not blocking |
| MCP integration | **Ready** | 5,800+ servers reachable via `mcp_search` |
| iTerm2 control | **Ready** | macOS-only, documented |

## Production blockers

Strict definition: things that must be resolved before a paying customer
can onboard, subscribe, and use the product without manual intervention
from the operator.

### 1. Subscription enforcement in the CLI

The `control_plane/` FastAPI service has plan definitions, auth,
Stripe wiring, and entitlement tracking. The CLI does not currently
consult any of it. A user installing the Foundry today gets the full
surface whether or not they've paid. Fixing this is a multi-step
change:

- CLI startup must authenticate against the control plane (API key
  or OAuth)
- Each turn dispatch must decrement a credit balance via control
  plane API
- Rate limiting kicks in at zero balance with a clean "refill or
  upgrade" message
- BYOK tier must detect user-supplied keys and skip the credit
  decrement

Estimated effort: one focused week.

### 2. Billing webhooks

Stripe sends events (`invoice.paid`, `customer.subscription.updated`,
`invoice.payment_failed`). The control plane receives them in
`control_plane/billing.py` but does not yet reconcile them into
active-plan state changes that the CLI would respect. Without this,
plan upgrades don't take effect until manual override.

Estimated effort: three focused days.

### 3. Signup flow

No self-serve signup surface exists. Currently a new customer would
have to email the operator, who would create a control plane user
and issue an API key by hand. Acceptable for closed beta with under
twenty users. Not acceptable for a public launch.

Estimated effort: one week for a minimal Next.js or Django signup
+ checkout page connecting to the existing control plane.

### 4. Observability

No production logging, no error aggregation, no usage analytics.
Turn-level cost and token data lives only in the local
`SessionCostTracker` and is gone when the CLI exits. For a paying
product you need:

- Structured logging of turn outcomes (success, failure, cost)
- Error aggregation (PostHog, Sentry, or similar)
- Per-customer cost aggregation so unit economics can be watched
  and per-user abuse flagged
- Uptime monitoring on the control plane

Estimated effort: three focused days, assuming PostHog SDK
(user is already a PostHog customer based on the plugin surface).

### 5. 9 failing tests

`tests/test_model_config.py` and `tests/test_cli_model_switch.py`
assert retired aliases (`gpt-5.4`, `crowelm-pro`) against the
reshuffled chain. None of these failures relate to shipped
functionality; they are test drift. Red tests on main degrade
team confidence and make CI useless as a merge gate.

Estimated effort: two focused hours.

## Testing status

### Unit coverage

- 61 passing tests in the new surface (cost model, nemoclaw, history)
- 212 tests in the legacy surface with 9 red (test drift, not real
  regressions)
- 14 errors from missing `fastapi` in the test environment
  (test_domain.py, test_knowledge.py, test_control_plane.py). Install
  the dependency or mark those tests as requiring an extra.

### Integration coverage

**Weak.** No end-to-end smoke test that boots the CLI, dispatches
a turn, verifies the HUD, and cleans up. No test against real
provider APIs (Anthropic, Ollama Cloud). No test that a Brev
Launchable actually provisions.

The live smoke tests that matter can't run in CI because they cost
money and require credentials. Recommend a `tests/live/` directory
that's not run by default but can be invoked with
`pytest tests/live/ --live` using a `conftest.py` that reads creds
from the environment.

### Load testing

**None.** The dual-mode + synthesis path triples API call volume
per user turn. Under concurrent users this could saturate provider
rate limits (NIM free tier is ~40 RPM). Need a synthetic load
generator to know actual throughput per provider tier before
public launch.

## Infrastructure requirements

For a closed beta opening today:

- Python 3.11 runtime (user install)
- One Anthropic account with Opus 4.7 deployment (already have)
- One Ollama Pro subscription (already have, $20/month)
- Optional NVIDIA Developer Program membership (free)
- Optional Azure OpenAI resource (already have, Azure Foundry)
- Stripe account for billing (already have)
- Control plane deployed somewhere reachable (Railway, Fly, Render).
  **Not currently deployed.** Takes an afternoon.

For a public launch:

- All of the above plus:
- Observability stack (PostHog already integrated)
- Error tracking (Sentry already integrated as a plugin)
- Signup/checkout web surface
- Support email and helpdesk (Linear issues feed works for triage
  if the user volume is under 100)
- Status page (statuspage.io or a single-file page)
- SOC2 posture for enterprise conversations (Drata/Vanta, 3-6 months)

## Security and compliance gaps

### Critical (must fix before any customer)

- **Tool registry exposes operator filesystem** by default.
  `execute_shell` runs on the operator's machine. Fine when the
  operator is the customer, unsafe if the CLI is ever run on a
  shared host. NemoClaw sandbox isolates this for Talon but the
  default agent still hits the host. Fix with a session-level
  setting or an opt-in toggle.
- **No rate limiting on the CLI side.** Control plane handles
  rate limits once wired, but until then a runaway tool loop
  could burn real dollars against the Anthropic API. MAX_ROUNDS
  cap (20) is the only guard today.
- **Memory DB at `~/.crowe-logic/memory.db`** contains every
  tool call the operator ever made, including shell commands and
  API responses. That file is plaintext SQLite. Not encrypted.
  For a solo operator this is fine, for a shared workstation it
  is a leak. Document the path in the README.

### Important (fix before public launch)

- **No SSO.** Team tier markets SSO (Google Workspace, Okta). Not
  implemented. Cannot close a Team deal without this.
- **No audit logs.** Enterprise tier markets audit logs + SAML +
  SOC2. Nothing exists. Cannot close an Enterprise deal until
  these are built.
- **No secrets rotation path.** API keys are in `.env` files.
  Rotation is manual. Acceptable for solo, problematic for any
  multi-person install.
- **No data retention controls.** Enterprise markets these.
  Nothing implemented.

### Lower priority

- Dependency vulnerabilities: `pip-audit` not in CI. Ollama and
  httpx have had minor CVEs in the past twelve months; latest
  versions are clean but there's no gate.
- License audit: not performed. All direct deps are permissive
  (MIT, Apache 2.0, BSD) but no SBOM exists.

## Go-to-market readiness

### Positioning

Clear. The pricing strategy doc articulates the wedge: multi-model
concurrent orchestration with a synthesis layer and a sandboxed
shell. No competitor bundles this today. Positioning copy can be
lifted directly from `docs/pricing-strategy.md`.

### Pricing

Finalized in `config/customer_pricing.json` and rationale documented.
Four tiers plus BYOK. First-month 50% off promo (`FOUNDRY50`)
built into the config, needs Stripe coupon configured to match.

### Distribution channels

Strong for the operator. Michael Crowe has:

- Existing Skool community (mycology + AI audience)
- Southwest Mushrooms customer list
- Mushroom Grower book buyers (license keys already tie to AI)
- Google Ads infrastructure running for SW Mushrooms
- Credibility as a mycologist-developer crossover

None of this is currently pointed at Crowe Logic Foundry. Natural
first push: a dedicated landing page at `crowelogic.com/foundry` or
similar, a mailing-list announce to the three audiences above, a
demo video asciinema clip on the landing page.

### Support model

Acceptable for closed beta: the operator handles everything via
Linear and email. Not sustainable past one hundred paying customers
without a support SLA that can be honored by one person.

### Documentation

Present for the technical reader: `docs/nemoclaw-integration.md`,
`docs/ARCHITECTURE.md`, `docs/TESTER_ONBOARDING.md`,
`docs/pricing-strategy.md`, this report. Missing:

- User-facing getting-started guide (operator-onboard, not
  developer-onboard)
- FAQ
- Cookbook examples (dual mode use cases, synthesis patterns,
  `/replay` workflows)
- API reference for BYOK customers wiring their own keys

Estimated effort: three days to write all four if drafted by the
operator with Foundry assistance.

## Launch risk assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Anthropic raises Opus rates mid-flight | Medium | Credit system absorbs this; margin shrinks but doesn't vanish |
| Ollama deprecates `:cloud` tags | Medium | Fallback chain already in place, switches to direct Moonshot API |
| NIM free tier starts metering | Low | Talon/NemoClaw is the only production dependency; switch to Brev GPU rental |
| Concurrent user load saturates rate limits | Medium | No load test yet; mitigate with per-user rate caps (not implemented) |
| Billing webhook bugs over/undercharge users | **High** | Must exercise end-to-end before first paying customer |
| Single-operator support model breaks at scale | Medium | Plan to hire or contract first support engineer at 200 customers |
| Competitor copies multi-model concurrent pattern | Medium | Moat is the synthesis quality and tool ecosystem, keep investing there |
| Data leak from unsecured memory.db | Low | Solo operator usage today; document before public launch |

## Readiness recommendations

**Gate 1: Closed beta (10-20 users)** shippable within one focused
week.

- Fix 9 red tests (2 hours)
- Deploy control plane to Railway (half day)
- Manual signup and API-key issuance via ops script
- Invite-only via existing Skool community
- Gather usage data, iterate

**Gate 2: Soft launch (50-200 users)** 3-4 weeks from now.

- Wire subscription enforcement in CLI
- Reconcile Stripe webhooks into plan state
- Build self-serve signup flow
- Deploy PostHog + Sentry
- Write user-facing docs

**Gate 3: Public launch (unbounded)** 6-8 weeks from now.

- Load-test dual mode at 50 concurrent users
- Build SSO for Team tier
- Complete SOC2 Type 1 (if pursuing Enterprise deals)
- Status page + support helpdesk

The first gate is close enough that a focused week could open it.
Most of what's between Gate 1 and Gate 2 is billing infrastructure,
which is scary to get wrong and deserves its own pass. Gate 3 is a
genuine quarter-scale project.

## What to not do

Some things that look like product work but would be wasted motion
today:

- **Do not add more agent profiles** until crowe-talon has a
  customer. One profile is a pattern demo; five profiles is a
  catalog that needs maintaining.
- **Do not build a web UI.** The CLI is the product. A web UI
  doubles the surface area and halves the focus. Revisit at
  ten thousand customers.
- **Do not pursue SOC2 proactively.** Pursue it when an
  Enterprise deal asks for it. Drata/Vanta is 3-6 months; don't
  start that clock without revenue behind it.
- **Do not chase every provider.** The seven-provider chain is
  already at the complexity ceiling. New providers require
  justification (customer demand or cost advantage).

## Final call

**Ready for Gate 1 inside one week.** The shipping work is billing
wiring, test triage, and a Railway deploy. None of it is
architectural. The product is architecturally sound; the remaining
effort is connecting already-built pieces to money.
