# Crowe Logic Platform: Architecture

Status: canonical. Refreshed 2026-04-27. Layer contract for the runtime
plus subsystem map and deploy topology. Machine-checked by
`tests/test_architecture_boundaries.py`.

## The big picture

Crowe Logic is one platform with five product surfaces sharing one
runtime spine. The spine handles auth, billing, model routing, tool
dispatch, and credit metering. The surfaces handle UX and product-level
features.

```
                 +--------- Surfaces ----------+
                 |  Crowe Code (IDE)            |
                 |  CroweLM (model)             |
                 |  Research Engine (CLI/API)   |
                 |  Studio (content)            |
                 +-----------+------------------+
                             |
                 +-----------v------------------+
                 |        Shared spine          |
                 |  control_plane (FastAPI)     |
                 |  providers (model gateway)   |
                 |  tools (98 tools)            |
                 |  config (rate cards, tiers)  |
                 |  cli (terminal + headless)   |
                 +-----------+------------------+
                             |
                 +-----------v------------------+
                 |     Provider matrix          |
                 |  Primary (premium models)    |
                 |  Secondary (cost-optimized)  |
                 |  BYOK (customer keys)        |
                 |  MCP (5,800+ servers)        |
                 +------------------------------+
```

## Core layers

This repo is organized around runtime layers. The goal is to keep
provider logic, tool logic, host surfaces, and HTTP surfaces from
bleeding into each other as the product expands.

- `config`: environment loading, model registry, static runtime config,
  tier definitions (`config/customer_pricing.json`).
- `providers`: model backends and shared tool-calling loops.
- `tools`: callable capabilities and thin backend adapters used by
  providers and domain routes.
- `cli`: terminal and headless host surfaces that drive the runtime.
- `control_plane`: FastAPI composition root for HTTP routes, auth,
  billing, metering, dashboard entry, and the hosted Research Engine.
- `domain`: product-facing API routers that call tools.
- `knowledge`: knowledge-plane routers, including `crowe-knowledge`
  RAG.
- `crowe_synapse_engine`: orchestration and pipeline core.
- `dashboard`: control-plane UI routes.
- `iterm`: iTerm-specific adapter code.

### Studio subsystem

Studio runs in parallel to the runtime layers. Its top-level modules
include `training/`, `studio_data/`, the multi-camera capture pipeline,
the shot-selector, and the EDL renderer. Studio integrates with the
spine through the credit ledger (every CroweLM-driven shot decision
decrements credits) and through the model gateway (CroweLM inference).

## Dependency directions

- `config` is a leaf. Nothing inside it should depend on other runtime
  layers.
- `crowe_synapse_engine`, `knowledge`, `dashboard`, and `iterm` are
  leaves.
- `domain` may depend on `tools`.
- `tools` may depend on `providers` for thin backend adapters.
- `providers` may depend on `config` and `tools`.
- `cli` may depend on `config`, `providers`, `tools`, `iterm`, and
  `crowe_synapse_engine`.
- `control_plane` is the HTTP composition root and may depend on
  `config`, `providers`, `domain`, `knowledge`, and `dashboard`.

## How the credit ledger glues the surfaces together

Every customer has one identity in the control plane and one credit
balance. Every turn through any surface decrements that balance:

- Crowe Code IDE -> `cli/headless.py` -> per-turn credit publish
  (fire-and-forget after `done` event) -> control plane.
- `cl-agent` CLI -> same path.
- Research Engine API -> control plane decrements directly per pipeline
  stage.
- Studio shot-selector turns -> credit publish per CroweLM call.

This is what makes "one platform, five surfaces" descriptive instead
of aspirational. The customer has one bill, one tier, one balance.

## Deploy topology

| Component | Target | Notes |
|---|---|---|
| Crowe Code IDE | Distributed as a signed app + VS Code extension | Mac and Windows builds; Linux pending |
| Marketing site (`crowecode.com`) | Vercel | DNS via NameCheap automation |
| Control plane | Railway primary, Fly.io secondary | Postgres on Railway |
| Research Engine endpoint | Hosted alongside control plane | Same FastAPI process |
| Studio capture | Local Mac runtime | iOS app pending; cloud fleet in `Later` lane |
| CroweLM training | Azure AI Foundry | Production fine-tunes deployed to gateway |

Multiple deploy targets are configured (`railway.toml`, `render.yaml`,
`fly.toml`) so the platform can move between providers without code
changes if a hosting decision shifts.

## Architecture contract test

`tests/test_architecture_boundaries.py` is the machine-checked contract
for the dependency rules above. If a new import crosses a forbidden
boundary, the test fails before the coupling becomes part of the repo
by accident. Adding a new layer means updating this test.

## Temporary exceptions

There is one intentional layer-crossing exception today: some provider
modules import the CLI `StreamRenderer` as a fallback when no renderer
is passed in. That dependency is currently allowed only in:

- `providers/_shared.py`
- `providers/azure_openai.py`
- `providers/anthropic.py`
- `providers/watsonx.py` (deprecated, will be removed)

The exception is tracked by the architecture contract test so it cannot
spread to additional files unnoticed.

## What this architecture optimizes for

- **Adding a new provider.** Drop a module in `providers/`, register
  it in the model registry, no other layer changes. Used regularly.
- **Adding a new tool.** Implement against the tool registry contract,
  ship without touching providers. The tool count grew from 0 to 98
  this way.
- **Adding a new surface.** Surfaces depend on the spine, not on each
  other. Crowe Code, CroweLM, Research, Studio do not import each
  other's modules. Adding a fifth surface follows the same pattern.

## What it deliberately does not optimize for

- **Multi-tenant runtime isolation at the chat layer.** Today the chat
  backend runs in single-tenant mode. Multi-tenant routing is a
  Gate 3 (Scale) entry criterion, not a current concern.
- **Provider failover.** Today providers are independent. Automatic
  failover under provider error is in the `Next` lane.
- **Hot-reload of tier configuration.** `config/customer_pricing.json`
  changes require a control-plane restart. Acceptable until tier
  changes happen more than monthly.

## Related documents

- `docs/blueprint.md`. Architecture in business and launch context.
- `docs/product-readiness.md`. Architecture-driven readiness gates.
- `docs/superpowers/specs/`. Per-subsystem design history.
- `STUDIO_ROADMAP.md`. Studio subsystem detail.
