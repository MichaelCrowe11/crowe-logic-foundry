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

# ── AI Foundry serverless endpoints ────────────────────────────────────
deploy_serverless() {
  local name=$1 registry=$2 model=$3
  if az ml serverless-endpoint show \
       --name "$name" --workspace-name "$HUB_WS" --resource-group "$HUB_RG" \
       >/dev/null 2>&1; then
    skip "$name already deployed"
    return 0
  fi
  local model_id="azureml://registries/${registry}/models/${model}/labels/latest"
  if az ml serverless-endpoint create \
       --name "$name" \
       --workspace-name "$HUB_WS" --resource-group "$HUB_RG" \
       --set model_id="$model_id" \
       --no-wait \
       --output none 2>err.log; then
    done_ok "$name (queued: $registry / $model)"
    rm -f err.log
  else
    fail "$name failed: $(tail -2 err.log | tr '\n' ' ')"
    rm -f err.log
  fi
}

log "Wave 2 / 2 — AI Foundry serverless endpoints (queued in parallel)"

# Anthropic — Prime, Forge, Haste, Vision
deploy_serverless claude-opus-4-7   azureml-anthropic claude-opus-4-7
deploy_serverless claude-sonnet-4-6 azureml-anthropic claude-sonnet-4-6
deploy_serverless claude-haiku-4-5  azureml-anthropic claude-haiku-4-5

# Moonshot — Lunar, Thinker
deploy_serverless Kimi-K2-6         azureml-moonshotai Kimi-K2.6
deploy_serverless Kimi-K2-Thinking  azureml-moonshotai Kimi-K2-Thinking

# DeepSeek — Reason, Vector
deploy_serverless DeepSeek-V4-Flash azureml-deepseek DeepSeek-V4-Flash
deploy_serverless DeepSeek-V3-2     azureml-deepseek DeepSeek-V3.2

# xAI Grok — Oracle, Sage, Coder
deploy_serverless grok-4-3                 azureml-xai grok-4.3
deploy_serverless grok-4-1-fast-reasoning  azureml-xai grok-4-1-fast-reasoning
deploy_serverless grok-code-fast-1         azureml-xai grok-code-fast-1

# Meta / Llama — Maverick, Scout
deploy_serverless Llama-4-Maverick azureml-meta Llama-4-Maverick-17B-128E-Instruct-FP8
deploy_serverless Llama-4-Scout    azureml-meta Llama-4-Scout-17B-16E-Instruct

# Cohere — Continental, Rerank, Embed
deploy_serverless Cohere-Command-A    azureml-cohere COHERE-COMMAND-A
deploy_serverless Cohere-rerank-v4    azureml-cohere Cohere-rerank-v4.0-pro
deploy_serverless Cohere-embed-v4     azureml-cohere embed-v-4-0

log "Queued. Foundry serverless endpoints provision asynchronously (~5-10 min each)."
echo "  Poll status with:"
echo "    az ml serverless-endpoint list --workspace-name $HUB_WS --resource-group $HUB_RG -o table"
echo ""
echo "  Cognitive Services deployments complete synchronously above; check with:"
echo "    az cognitiveservices account deployment list --name $CS_ACCT --resource-group $CS_RG -o table"
