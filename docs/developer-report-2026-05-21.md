# Crowe Logic CLI and Azure AI Developer Report

Date: 2026-05-21
Scope: `crowe-logic` CLI live readiness, CroweLM model legend, Azure AI account state, and immediate developer follow-up.
Operator account verified: `mike@southwestmushrooms.com`

## Executive Summary

The Crowe Logic CLI is live and usable. The installed command reports `crowe-logic, version 0.3.0`, live single-turn routing returns the expected answer, headless Kernel returns the expected JSON stream, and the deploy health gate now reports `62/72` models online plus `2` virtual routing tiers when run with a 20 second probe timeout.

Azure CLI authentication has been restored for the active Azure for Startups account. The selected subscription is `Azure subscription 1` with subscription id `4ea8ab04-9d53-46cf-9d80-de7d625ba88a`, tenant `mikesouthwestmushrooms.onmicrosoft.com`, tenant id `167863f4-3fdb-4cbf-98d0-acc225823117`.

The active Azure AI production resource is `crowelm-prod-eastus2` in `rg-crowelm-prod`. It has 27 model deployments in `eastus2`, and every live production deployment is represented in `config/models.extra.json`. The smaller `mike-3585-resource` resource exists in `rg-mike-3585` with a `gpt-5.4-mini` deployment.

The primary remaining gaps are credentials/configuration gaps, not broad CLI failure:

- Anthropic-backed premium tiers are not configured locally.
- `CroweLM Talon Sandbox` has no sandbox endpoint configured.
- `CroweLM Cadence` timed out against its configured voice endpoint.
- Local Ollama models `CroweLM Unified Local` and `Gemma 4 Mycelium` error in the deploy probe.
- Neon Postgres is not configured for this local run.

## Model Legend

### Status Legend

| Status | Meaning | Action |
|---|---|---|
| `LIVE` | The deploy health check completed a successful probe. | Ready for CLI routing. |
| `virtual` | Router or orchestrator tier. It does not directly call one model in deploy health. | Expected for `CroweLM Auto` and `CroweLM DeepParallel`. |
| `no credentials` | Required endpoint/key env vars are missing or empty. | Configure the matching env vars before routing paid traffic. |
| `no endpoint` | Endpoint env var is missing. | Set the endpoint for that provider or keep the tier hidden. |
| `timeout` | Probe did not finish inside the configured timeout. | Re-test with longer timeout, then inspect endpoint health. |
| `error` | Provider was reachable enough to run, but the model call failed. | Inspect local provider logs and model availability. |

### Family Legend

| Family | Purpose | Primary backing |
|---|---|---|
| CroweLM Auto | Task classifier and tier router. | Virtual router in `config.agent_config`. |
| CroweLM Talon | Agentic/tool-use tiers with fast, super, flagship, and vision variants. | NVIDIA NIM through OpenAI-compatible endpoints. |
| CroweLM Vanguard | Sovereign Azure-hosted mirror of Talon. | Azure AI Foundry `crowelm-prod-eastus2`. |
| CroweLM Azure MaaS codenames | Production model catalog with leaky upstream names hidden behind CroweLM labels. | Azure AI Foundry MaaS/OpenAI-compatible deployments. |
| CroweLM Premium | Higher-end Anthropic/OpenAI-style paid tiers. | Azure Anthropic or Azure OpenAI env-gated endpoints. |
| CroweLM Domain | Mycology, grow operations, voice, and local domain models. | Azure OpenAI, OpenAI-compatible, or local Ollama. |
| CroweLM Local | Local/cloud Ollama paths for low-latency local routing. | Ollama and configured local model names. |
| CroweLM DeepParallel | Multi-perspective orchestration tier. | Virtual orchestration layer. |

### Live Health Snapshot

Run: `CROWE_LOGIC_DEPLOY_TIMEOUT_SECONDS=20 crowe-logic deploy`

Result: `62/72 models online (+2 virtual routing tiers)`

| Group | Live | Gaps |
|---|---:|---|
| Virtual routers | 2 | None. |
| Talon / NIM | 4 | `CroweLM Talon Sandbox` has no endpoint. |
| Vanguard / Azure sovereign | 3 | None. |
| Azure production MaaS and OpenAI-compatible tiers | Most live | None after 20s timeout on Hyphae, Helio Pro, Apex Premium, Depth. |
| Anthropic premium tiers | 0 local | `Supreme`, `Sovereign Premium`, `Prime Premium`, `Dense Managed` need credentials/config. |
| Local/domain tiers | Mixed | `Unified Local` and `Gemma 4 Mycelium` error; `Cadence` times out. |

### Operational Tier Guide

| Label | Use when | Notes |
|---|---|---|
| `CroweLM Auto` | Default route for users. | Picks a concrete tier based on prompt class. |
| `CroweLM Hyphae Legacy` | General chat fallback. | Live, used by smoke test. |
| `CroweLM Kernel` | Fast cultivation-tuned responses. | Live, headless smoke returned `kernel-ok`. |
| `CroweLM Grower` | Commercial mycology operations. | Live, points at Azure-hosted command tier. |
| `CroweLM Talon` | High-value agentic/tool-use work. | Live, NVIDIA-backed. |
| `CroweLM Talon Nano` | Fast/cheap agentic dispatch. | Live, low latency. |
| `CroweLM Talon Vision` | Screenshot/image reasoning. | Live. |
| `CroweLM Vanguard` | Sovereign Azure-hosted flagship. | Live, use when Crowe-owned Azure infrastructure matters. |
| `CroweLM Coder` / `CroweLM Dev` | Coding and implementation flows. | Live, Azure-hosted code-specialized deployments. |
| `CroweLM Helio` / `Helio Pro` / `Helio Mini` | General OpenAI-style reasoning lanes. | Live with 20s timeout. |
| `CroweLM Quasar` / `Nova` | gpt-5.5 backed lanes. | Live. |
| `CroweLM DeepParallel` | Expensive synthesis/orchestration. | Virtual tier, not a direct health probe. |
| `CroweLM Supreme` | Top premium Anthropic lane. | Not usable locally until Anthropic env vars are restored. |
| `CroweLM Cadence` | Michael Crowe voice/cadence model. | Configured but timed out in current probe. |
| `Gemma 4 Mycelium` | Local/offline mycology model. | Present in catalog, errored in current local probe. |

## Azure AI Account State

### Active Subscription

| Field | Value |
|---|---|
| Subscription name | `Azure subscription 1` |
| Subscription id | `4ea8ab04-9d53-46cf-9d80-de7d625ba88a` |
| State | `Enabled` |
| Tenant | `mikesouthwestmushrooms.onmicrosoft.com` |
| Tenant id | `167863f4-3fdb-4cbf-98d0-acc225823117` |
| User | `mike@southwestmushrooms.com` |

### Azure AI Resources

| Resource | Group | Region | Kind | Notes |
|---|---|---|---|---|
| `crowelm-prod-eastus2` | `rg-crowelm-prod` | `eastus2` | `AIServices` | Main production CroweLM resource. |
| `mike-3585-resource` | `rg-mike-3585` | `eastus2` | `AIServices` | Smaller resource with one deployment. |
| `crowelm-mlws-eastus2` | Azure ML workspace | `eastus2` | ML workspace | Available for ML/fine-tune/workspace flows. |

### Production Deployment Inventory

Resource: `crowelm-prod-eastus2`

| Deployment | Model | Version | Format | SKU | Capacity |
|---|---|---|---|---|---:|
| `gpt-5.5` | `gpt-5.5` | `2026-04-24` | OpenAI | GlobalStandard | 50 |
| `gpt-4o` | `gpt-4o` | `2024-11-20` | OpenAI | GlobalStandard | 10 |
| `text-embedding-3-large` | `text-embedding-3-large` | `1` | OpenAI | GlobalStandard | 10 |
| `model-router` | `model-router` | `2025-11-18` | OpenAI | GlobalStandard | 10 |
| `sora-2` | `sora-2` | `2025-10-06` | OpenAI | GlobalStandard | 1 |
| `DeepSeek-R1-0528` | `DeepSeek-R1-0528` | `1` | DeepSeek | GlobalStandard | 4 |
| `DeepSeek-V3-1` | `DeepSeek-V3.1` | `1` | DeepSeek | GlobalStandard | 4 |
| `Cohere-Command-A` | `cohere-command-a` | `1` | Cohere | GlobalStandard | 4 |
| `Cohere-embed-v4` | `embed-v-4-0` | `1` | Cohere | GlobalStandard | 1 |
| `Llama-3-3-70B` | `Llama-3.3-70B-Instruct` | `9` | Meta | GlobalStandard | 1 |
| `Codestral-2501` | `Codestral-2501` | `2` | Mistral AI | GlobalStandard | 1 |
| `Kimi-K2-6` | `Kimi-K2.6` | `2026-04-20` | MoonshotAI | GlobalStandard | 8 |
| `DeepSeek-V4-Flash` | `DeepSeek-V4-Flash` | `2026-04-23` | DeepSeek | GlobalStandard | 4 |
| `grok-4-3` | `grok-4.3` | `1` | xAI | GlobalStandard | 1 |
| `Llama-4-Scout` | `Llama-4-Scout-17B-16E-Instruct` | `1` | Meta | GlobalStandard | 1 |
| `Llama-4-Maverick` | `Llama-4-Maverick-17B-128E-Instruct-FP8` | `1` | Meta | GlobalStandard | 1 |
| `grok-4-1-fast-reasoning` | `grok-4-1-fast-reasoning` | `1` | xAI | GlobalStandard | 1 |
| `grok-4-20-reasoning` | `grok-4-20-reasoning` | `1` | xAI | GlobalStandard | 1 |
| `grok-4-1-fast-non-r` | `grok-4-1-fast-non-reasoning` | `1` | xAI | GlobalStandard | 1 |
| `Cohere-rerank-v4-pro` | `Cohere-rerank-v4.0-pro` | `1` | Cohere | GlobalStandard | 1 |
| `Cohere-rerank-v4-fast` | `Cohere-rerank-v4.0-fast` | `1` | Cohere | GlobalStandard | 1 |
| `gpt-chat-latest` | `gpt-chat-latest` | `2026-05-05` | OpenAI | GlobalStandard | 2500 |
| `Kimi-K2.5` | `Kimi-K2.5` | `1` | MoonshotAI | GlobalStandard | 4 |
| `gpt-5.4-nano` | `gpt-5.4-nano` | `2026-03-17` | OpenAI | GlobalStandard | 150 |
| `gpt-5.4-mini` | `gpt-5.4-mini` | `2026-03-17` | OpenAI | GlobalStandard | 10 |
| `gpt-5.4-pro` | `gpt-5.4-pro` | `2026-03-05` | OpenAI | GlobalStandard | 1 |
| `gpt-5.4` | `gpt-5.4` | `2026-03-05` | OpenAI | GlobalStandard | 10 |

Resource: `mike-3585-resource`

| Deployment | Resource group | Notes |
|---|---|---|
| `gpt-5.4-mini` | `rg-mike-3585` | Secondary/smaller resource deployment. |

## Azure Quota and Support State

### Azure ML Quota in `eastus2`

| Quota | Limit | Applicable | Notes |
|---|---:|---|---|
| `TotalDedicatedCores` | 350 | false | Regional total reported by quota API. |
| `standardNCFamily` | 6 | true | Only non-zero applicable GPU family found. |
| Most `NC*`, `ND*`, `H100`, `H200`, `MI300X` families | 0 | mixed | Not available without quota increase. |
| `TotalLowPriorityCores` | 0 | true | No low-priority quota. |

### Cognitive Services Quota

`az quota list` against `Microsoft.CognitiveServices/locations/eastus2` returns `BadRequest` through the current quota extension. Use Azure AI Foundry portal quota views or the Foundry-specific quota API path for model TPM/capacity planning. The generic quota extension is not reliable for this provider scope.

### Support Routing

Recent support ticket listing through `az support in-subscription tickets list` returned no current tickets.

Useful current support classifications:

| Service | Classification | Id |
|---|---|---|
| AI Foundry Portal | Anthropic models / Billing or quota issues | `97708886-08cb-f011-bbd3-6045bdd8afef` |
| AI Foundry Portal | Deployments / HTTP 429 Rate Limit Exceeded | `928712a0-4c34-f111-88b4-000d3a54b243` |
| AI Foundry Portal | Safety and Security / Quota or RPS limitation | `0ef47cde-3a1f-6aed-68ce-def430bf539d` |
| Azure OpenAI | API Errors and Exceptions / HTTP 429 Rate Limit Exceeded | `74253672-4f29-d03c-b16e-85024a8ee8bf` |
| Azure OpenAI | Model Availability or Access / Unable to find a model | `fb88f022-f401-3841-b451-a52ec67f3506` |

## CLI Verification

### Commands Run

| Check | Result |
|---|---|
| `crowe-logic --version` | `crowe-logic, version 0.3.0` |
| `CROWE_LOGIC_AUTO_ROUTE=1 crowe-logic run "Reply with exactly: crowe-live-ok"` | Returned `crowe-live-ok`; routed to `CroweLM Hyphae Legacy`; `API LIVE`. |
| `crowe-logic headless --model kernel` with JSON stdin | Emitted `ready`, token stream `kernel-ok`, and `done`. |
| `CROWE_LOGIC_DEPLOY_TIMEOUT_SECONDS=20 crowe-logic deploy` | `62/72 models online (+2 virtual routing tiers)`. |
| Targeted pytest suite | `80 passed in 0.64s`. |

### Tests Run

```bash
./.venv/bin/python -m pytest \
  tests/test_azure_openai.py \
  tests/test_cli_deploy.py \
  tests/test_foundry_api.py \
  tests/test_cli_model_switch.py \
  tests/test_cli_models_sync.py \
  tests/test_model_config.py \
  tests/test_prompt_loader.py \
  -q
```

Result: `80 passed`.

## Code Changes Made

The live CLI smoke tests exposed prompt-loader fallback warnings for `kernel` and `nexus`. The warnings were not fatal, but they made the operational path noisy and indicated that two live variants were still relying on inline prompts instead of the filesystem prompt contract.

Files added:

- `config/system_prompts/kernel.md`
- `config/system_prompts/nexus.md`

Files updated:

- `cli/crowe_logic.py`
- `tests/test_cli_models_sync.py`
- `tests/test_prompt_loader.py`

New CLI command:

- `crowe-logic models legend [--customer] [--only-ready]`

New tests:

- `test_live_smoke_variants_have_prompt_files_in_real_repo`
- `test_models_legend_operator_view_shows_provider_and_readiness`
- `test_models_legend_customer_view_hides_backend_details`
- `test_models_legend_only_ready_filters_operator_only_models`

Verification:

- `tests/test_cli_models_sync.py tests/test_prompt_loader.py`: `19 passed`
- Targeted Azure/CLI suite: `80 passed`
- `crowe-logic models legend --customer --only-ready`: renders customer-safe legend, `67/72 models shown`
- Direct prompt resolution for `kernel` and `nexus` emits no fallback warning.
- Post-fix live `crowe-logic run` emits no prompt-loader warning.

## Current Git State

Current branch: `chore/ruff-lint-sweep`

Known dirty files:

- `config/agent_config.py` has pre-existing broad formatting/model-catalog changes. This report did not rewrite or revert it.
- `.env.env.bak-cadence` is untracked and was not touched.
- This session added prompt files, the model legend command, legend tests, and this report.

## Developer Assessment

### What Is Healthy

- The real installed CLI works end to end.
- Azure CLI authentication is restored against the correct tenant and active subscription.
- The production Azure AI resource exists and has a large current deployment set.
- The local extra-model registry already covers the live Azure production deployment inventory.
- The 20 second deploy timeout gives a truer health signal than the default 8 second timeout for slower models.
- Tests covering Azure provider config, deploy classification, model switching, model sync, model config, and prompt loading are green.

### What Needs Work

1. **Make deploy timeout profile-aware.** The default 8 second timeout misclassified several slow-but-live tiers. A better default is either 20 seconds or per-provider/per-model timeout metadata.
2. **Hide or gate unconfigured premium tiers.** Anthropic-backed premium tiers currently show `no credentials`. That is useful for operators but should be hidden or marked unavailable for customer-facing selectors.
3. **Fix Cadence endpoint health.** `CroweLM Cadence` timed out even with a 20 second deploy timeout. Inspect `NEXUS_MIKE_ENDPOINT` and its serving process.
4. **Repair local Ollama model availability.** `CroweLM Unified Local` and `Gemma 4 Mycelium` error. Confirm `ollama list`, model tags, and local/cloud routing.
5. **Configure Neon or suppress local warning.** `Neon Postgres not configured` is expected locally, but the deploy report should distinguish local-dev warning from production blocker.
6. **Use Foundry-specific quota views for Cognitive Services.** The generic `az quota` extension is not sufficient for Azure AI model capacity.
7. **Keep model catalog source of truth tight.** `config/models.extra.json` is current, but `config/agent_config.py` is very large and currently dirty. Avoid mixing formatting churn with model-routing changes.

## Recommended Next Actions

1. Add a `CROWE_LOGIC_DEPLOY_TIMEOUT_SECONDS=20` default or per-tier timeout metadata.
2. Add a `--customer` flag to `crowe-logic deploy` that hides backend names and operator-only failures.
3. Create a small Azure audit script that outputs resources, deployments, quota, and support classifications without printing secrets.
4. Fix or disable `CroweLM Cadence` until its endpoint can pass health checks.
5. Decide whether Anthropic tiers should be re-enabled, hidden, or moved behind an explicit `CROWE_LOGIC_EXPERIMENTAL_PREMIUM=1` gate.
6. Cleanly split the current `config/agent_config.py` dirty diff from future model-routing work.
