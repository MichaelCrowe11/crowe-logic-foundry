#!/usr/bin/env bash
# Deploy the CroweLM Frontier model lineup to the new Azure account.
# 2026-05-13 — "heavily upgraded vs old crowelogicos-* account" sweep.
#
# Run from anywhere; requires `az` logged into the
# mike@southwestmushrooms.com account (Azure subscription 1, sub
# 4ea8ab04-9d53-46cf-9d80-de7d625ba88a).
#
# Idempotent: re-running skips deployments that already exist.
# All SKUs are pay-as-you-go (Standard or GlobalStandard). Zero idle cost.
# Bumps capacity to 10 (10K TPM) for chat models; 1 for video/voice/sora.
#
# Total: 6 Cognitive Services (OpenAI) + 14 AI Foundry serverless endpoints
#       = 20 net-new deployments. Add to 2 existing (gpt-5.5, gpt-5.4-mini)
#       for a 22-tier floor.

set -euo pipefail

CS_ACCT="crowelm-prod-eastus2"
CS_RG="rg-crowelm-prod"
HUB_WS="crowelm-mlws-eastus2"
HUB_RG="rg-crowelm-prod"

log() { printf "\n\033[1;34m▶ %s\033[0m\n" "$*"; }
done_ok() { printf "  \033[1;32m✓ %s\033[0m\n" "$*"; }
skip()    { printf "  \033[1;33m· %s\033[0m\n" "$*"; }
fail()    { printf "  \033[1;31m✗ %s\033[0m\n" "$*"; }

# ── OpenAI / Cognitive Services deployments ────────────────────────────
deploy_oai() {
  local name=$1 model=$2 version=$3 sku=$4 cap=${5:-10}
  if az cognitiveservices account deployment show \
       --name "$CS_ACCT" --resource-group "$CS_RG" \
       --deployment-name "$name" >/dev/null 2>&1; then
    skip "$name already deployed"
    return 0
  fi
  if az cognitiveservices account deployment create \
       --name "$CS_ACCT" --resource-group "$CS_RG" \
       --deployment-name "$name" \
       --model-name "$model" --model-version "$version" --model-format OpenAI \
       --sku-name "$sku" --sku-capacity "$cap" \
       --output none 2>err.log; then
    done_ok "$name ($model:$version, $sku/$cap)"
    rm -f err.log
  else
    fail "$name failed: $(tail -1 err.log)"
    rm -f err.log
  fi
}

log "Wave 1 / 2 — OpenAI Cognitive Services deployments"
deploy_oai gpt-4o                 gpt-4o                      2024-11-20         GlobalStandard 10
deploy_oai o3-mini                o3-mini                     2025-01-31         GlobalStandard 10
deploy_oai text-embedding-3-large text-embedding-3-large      1                  GlobalStandard 10
deploy_oai model-router           model-router                2025-11-18         GlobalStandard 10
deploy_oai gpt-4o-realtime        gpt-4o-realtime-preview     2025-06-03         GlobalStandard 1
deploy_oai sora-2                 sora-2                      2025-10-06         GlobalStandard 1

# ── Phase 2: MaaS-gated deployments via cognitiveservices ──────────────
# These call PUT on the Microsoft.CognitiveServices/accounts/deployments
# resource directly. The publisher (Anthropic / Meta-Llama-4 / xAI /
# Moonshot) gate requires the user to FIRST click through the Subscribe
# flow once in Azure AI Studio per publisher:
#
#   https://ai.azure.com/explore/models/claude-opus-4-7/version/1/registry/azureml-anthropic
#   https://ai.azure.com/explore/models/Llama-4-Maverick-17B-128E-Instruct-FP8/version/1/registry/azureml-meta
#   https://ai.azure.com/explore/models/grok-4.3/version/1/registry/azureml-xai
#   https://ai.azure.com/explore/models/Kimi-K2.6/version/1/registry/azureml-moonshotai
#
# After clicking "Subscribe to model" on each (one-time per subscription),
# this script's Wave 2 deploys will succeed via the standard
# az cognitiveservices path.

SUB="$(az account show --query id -o tsv)"
deploy_maas() {
  local name=$1 model=$2 format=$3 ver=${4:-1} sku=${5:-GlobalStandard} cap=${6:-1}
  if az cognitiveservices account deployment show \
       --name "$CS_ACCT" --resource-group "$CS_RG" \
       --deployment-name "$name" >/dev/null 2>&1; then
    skip "$name already deployed"
    return 0
  fi
  printf "%-30s %-30s " "$name" "$model:$ver ($format)"
  if az cognitiveservices account deployment create \
       --name "$CS_ACCT" --resource-group "$CS_RG" \
       --deployment-name "$name" \
       --model-name "$model" --model-version "$ver" --model-format "$format" \
       --sku-name "$sku" --sku-capacity "$cap" \
       --query "properties.provisioningState" -o tsv 2>err.log; then
    done_ok "$name"
    rm -f err.log
  else
    local err
    err=$(grep -o 'InvalidModelProviderData\|ServerlessModelNotAvailable\|ServiceModelDeprecated\|UserError\|.\{80,\}' err.log | head -1)
    fail "$name: $err"
    rm -f err.log
  fi
}

log "Wave 2 / 2 — MaaS-gated frontier models (requires publisher subscribe per the URLs above)"

# Anthropic — Prime, Forge, Haste
deploy_maas claude-opus-4-7   claude-opus-4-7   Anthropic 1
deploy_maas claude-sonnet-4-6 claude-sonnet-4-6 Anthropic 1
deploy_maas claude-haiku-4-5  claude-haiku-4-5  Anthropic 1

# Moonshot — Lunar, Thinker
deploy_maas Kimi-K2-6        Kimi-K2.6         Moonshot 1
deploy_maas Kimi-K2-Thinking Kimi-K2-Thinking  Moonshot 1

# DeepSeek — Reason Premium, Vector Premium (V4 / V3.2 are NOT gated)
deploy_maas DeepSeek-V4-Flash DeepSeek-V4-Flash DeepSeek 1
deploy_maas DeepSeek-V3-2     DeepSeek-V3.2     DeepSeek 1

# xAI Grok — Oracle, Sage, Coder
deploy_maas grok-4-3                 grok-4.3                xAI 1
deploy_maas grok-4-1-fast-reasoning  grok-4-1-fast-reasoning xAI 1
deploy_maas grok-code-fast-1         grok-code-fast-1        xAI 1

# Meta / Llama 4 — Maverick, Scout (Llama 3.3 was NOT gated; deployed above)
deploy_maas Llama-4-Maverick Llama-4-Maverick-17B-128E-Instruct-FP8 Meta 1
deploy_maas Llama-4-Scout    Llama-4-Scout-17B-16E-Instruct          Meta 1

log "Done. Re-run to retry any deployments still pending publisher subscribe."
echo "  Status:"
echo "    az cognitiveservices account deployment list --name $CS_ACCT --resource-group $CS_RG -o table"
