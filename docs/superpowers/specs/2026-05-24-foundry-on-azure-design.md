# Foundry on Azure: Design Spec

Date: 2026-05-24
Status: Design (no resources provisioned). Needs user go-ahead + Azure account
ready before any execution plan runs.
Funding: Microsoft for Startups Founders Hub Azure credits ($5k now, $25k on
upgrade).

## Problem

`crowe-foundry` (the control_plane) is down. Root cause confirmed 2026-05-24: it
fails its Railway healthcheck because no database URL is configured -
`control_plane/db.py` `init_pool()` raises `RuntimeError: No database URL configured`
in the FastAPI lifespan, so the container exits and the public URL 404s. No live
foundry endpoint exists anywhere (chat./api./foundry.crowelogic.com all dead), so
crowecode-web's AI routes fail and the web IDE is unusable (blocks David Gordon).

Rather than re-patch Railway, consolidate the control_plane onto managed, credited
Azure infrastructure. This also ends the recurring "Azure dead zone" firefighting
by giving foundry a stable, owned home.

## Goals / non-goals

Goals:
- A healthy, always-on foundry control_plane with a real database.
- crowecode-web AI routes work end-to-end (unblocks the web IDE).
- Funded by Founders Hub credits; portable enough to move clouds later.

Non-goals (defer):
- Migrating crowecode-web itself off Railway (separate, later).
- Whole-stack consolidation (the broader reference-architecture phase).
- Multi-region / HA. Single region is fine for now.

## Target architecture

- **Compute:** Azure Container Apps. The control_plane is already a Dockerized
  FastAPI/uvicorn app with `scripts/control_plane_entrypoint.sh` binding
  `0.0.0.0:$PORT`. Container Apps injects `PORT`; entrypoint already honors it.
  Map the platform health probe to `/health`.
- **Database:** Azure Database for PostgreSQL Flexible Server. Provide the
  connection string as `CONTROL_PLANE_DATABASE_URL` (the entrypoint also mirrors a
  generic `DATABASE_URL`). `init_pool()` succeeds -> lifespan completes -> /health
  green. Run `scripts/run_migrations.py` on deploy (entrypoint already does, best
  effort; with a real DB it now succeeds).
- **Registry:** Azure Container Registry (or build via Container Apps from source).
- **Models:** Azure OpenAI / Azure AI Foundry deployments behind the existing
  `/v1/chat/completions` adapter on control_plane. Keep the multi-provider gateway
  so OpenAI-direct remains a documented dead-zone fallback. Preserve virtual-tier
  badge abstraction - no raw model names on customer surfaces
  (see [[feedback-no-model-name-leakage]]).
- **Secrets:** Container Apps secrets / Azure Key Vault for DB URL + model keys.
  Do not bake secrets into the image.
- **Web wiring:** set crowecode-web `CROWE_FOUNDRY_BASE_URL` to the Container Apps
  ingress URL. Keep `src/lib/ai-provider.ts` OpenAI-direct path as fallback.

## Data flow

crowecode-web (Railway) -> HTTPS -> foundry control_plane (Azure Container Apps)
-> Postgres (Azure) for auth/billing/workspace state; -> Azure OpenAI for inference.
Health probe: Container Apps -> GET /health -> 200 once init_pool connects.

## Open decisions (resolve at plan time)

1. Region: pick one close to crowecode-web's Railway region to cut latency
   (Railway US West -> Azure West US 3?).
2. Postgres tier: Burstable B1ms is enough to start (cheap on credits); size up later.
3. Build path: prebuilt image in ACR vs Container Apps source build. Lean ACR for
   reproducibility.
4. Domain: keep the `*.azurecontainerapps.io` URL initially; map a Crowe domain
   later (and only once TLS is sorted - crowecode.com TLS is currently broken).
5. Data: control_plane DB starts empty (migrations build schema). Confirm no prior
   foundry data must be preserved (investigation found none locally).

## Migration / build sequence (for the future plan, not executed here)

1. Provision: resource group, ACR, Postgres Flexible Server, Container App env.
2. Wire secrets (DB URL, model keys) via Key Vault / Container Apps secrets.
3. Build + push the control_plane image to ACR.
4. Deploy Container App; health probe `/health`; confirm lifespan connects (the
   exact log line that was failing on Railway should now read "migrations ok" +
   uvicorn serving).
5. Smoke-test `/health`, `/v1/chat/completions`, and one domain router.
6. Repoint crowecode-web `CROWE_FOUNDRY_BASE_URL`; verify the web IDE AI route
   end-to-end (the David Gordon unblock).
7. Decommission / archive the dead Railway crowe-foundry project.

## Risks

- Credits burn: Container Apps scale-to-zero + Burstable Postgres keep idle cost
  near zero; set a budget alert.
- Model backend: even with foundry healthy, inference needs working Azure OpenAI
  deployments (quota filings exist per [[project-azure-quota-filings-2026-05-13]]);
  confirm a live deployment or use OpenAI-direct fallback initially.
- Cross-cloud latency web(Railway)->foundry(Azure): acceptable for chat; revisit if
  it matters.

## Success criteria

1. `https://<app>.azurecontainerapps.io/health` returns 200.
2. `/v1/chat/completions` returns a completion.
3. crowecode-web AI route works for a signed-in user.
4. Idle cost negligible against credits.
