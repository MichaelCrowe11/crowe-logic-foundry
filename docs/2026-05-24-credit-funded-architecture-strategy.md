# Credit-Funded Architecture Strategy

Date: 2026-05-24
Entity: Crowe Mycology LLC (AI startup)
Author context: written after a session where the recurring blocker was scattered,
half-dead backend infra (foundry 404 on Railway with no DB, the Azure "dead zone,"
mock data on the live crowecode-web homepage).

Held credits today: ~$5,000 Azure (Microsoft for Startups Founders Hub, business
verification tier, expires 2027-01-13) + ~$2,300 Google Cloud.

## Strategic order

1. Secure runway (credits).
2. Fix the backend on that runway (foundry on Azure).
3. Generalize to a whole-stack target architecture.

These are sequential because each funds and de-risks the next: credits pay for the
Azure backend; a proven foundry-on-Azure pattern is the template the rest of the
stack consolidates onto.

## 1. Credits (runway)

I can map eligibility, draft applications, and verify current terms. I cannot grant
credits. The only step fully in Michael's control today is the Founders Hub unlock.

### Immediate, in our control: $5k -> $25k Azure
- Same program, no new application. In the Founders Hub portal, complete **domain
  verification + a product demo** to move from the business-verification tier
  ($5k) to the full original-offer tier ($25k).
- Highest leverage per unit effort available right now.

### Founders Hub ceiling
- Up to $150k Azure unlocks as the company demonstrates growth/usage; the top of the
  range historically leans on investor/accelerator association.

### Stackable AI-startup programs (verify current terms before applying)
- **Google for Startups Cloud, AI tier** — up to ~$350k over 2 years for AI
  startups. A GCP account already exists ($2.3k held), so this is an upgrade path,
  not a cold start.
- **Anthropic startup program** — model credits; relevant given CroweLM routes
  through Anthropic-compatible gateways.
- **NVIDIA Inception** — free; GPU/compute discounts + DGX Cloud access. Natural fit
  for an AI company; no credit grant but real compute leverage.
- **AWS Activate** — up to ~$100k at the higher tier (usually needs accelerator/VC
  referral); $1k self-serve otherwise.

### Notes / risks
- Azure $5k expires 2027-01-13. Unlocking $25k resets/extends the working balance;
  plan usage so credits are not stranded.
- Do not architect single-cloud lock-in just because credits are there. Keep the
  backend portable (containers + managed Postgres) so Azure vs GCP is a deployment
  choice, not a rewrite.

## 2. Credit-funded foundry on Azure (the concrete fix)

Problem this solves: crowe-foundry on Railway fails healthcheck (no DATABASE_URL),
no live foundry endpoint exists anywhere, and the web IDE (crowecode-web) 500s its
AI routes as a result. David Gordon (paying $29/mo) cannot get a working IDE.

Target: retire the flaky Railway foundry; run the control_plane on managed,
credited Azure infra.

- **Compute:** Azure Container Apps (the foundry control_plane is already a
  Dockerized FastAPI/uvicorn app; the Founders Hub "Azure AI Foundry hub" and
  "Azure Container Apps" templates match this directly).
- **Database:** Azure Database for PostgreSQL (Flexible Server). Set
  `CONTROL_PLANE_DATABASE_URL` from it; `init_pool()` lifespan succeeds; /health
  comes up; the 404 disappears. This is the missing-DB root cause fixed properly
  rather than patched on Railway.
- **Models:** Azure OpenAI / Azure AI Foundry deployments behind the existing
  `/v1/chat/completions` adapter, keeping virtual-tier badge abstraction (no raw
  model-name leakage to customer surfaces).
- **Web wiring:** point crowecode-web `CROWE_FOUNDRY_BASE_URL` at the Azure
  endpoint. Keep the OpenAI-direct path in `ai-provider.ts` as a documented
  fallback for dead-zone resilience.

Outcome: a single credited backend that unblocks the web IDE and ends the
dead-zone firefighting. Needs its own design + plan cycle before execution (Azure
resource provisioning is outward-facing and irreversible-ish).

## 3. Whole-stack reference architecture (after foundry proves the pattern)

Once foundry-on-Azure is the proven template, generalize:
- crowecode-web, mycology, and data services consolidate onto the same
  container + managed-Postgres pattern, Azure-primary with GCP as the portability
  hedge (funded by the Google AI-tier credits).
- One identity/auth story, one secrets story, one observability story across
  surfaces, replacing the current per-service Railway sprawl.
- Delivered as a design doc with a migration sequence, not same-day execution.

## Immediate next actions

- **Michael (only you can):** in the Founders Hub portal, do domain verification +
  product demo to unlock $5k -> $25k Azure.
- **Claude (on your go-ahead):** (a) draft the Google AI-tier / Anthropic / NVIDIA
  Inception / AWS Activate applications for Crowe Mycology LLC; (b) open a proper
  brainstorm + plan cycle for "foundry on Azure" (section 2) before touching any
  cloud resources.
