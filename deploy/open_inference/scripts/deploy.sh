#!/usr/bin/env bash
# deploy.sh — One-command deploy/update for the CroweLM open inference stack.
#
# Usage:
#   cd deploy/open_inference
#   bash scripts/deploy.sh --router-only
#   bash scripts/deploy.sh --with glm51 --with qwen35
#   bash scripts/deploy.sh --with deepseek-v3 --with kimi-k25 --with gemma4
#   bash scripts/deploy.sh --ollama-dev
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE_FILE="docker-compose.yml"
BUILD_FLAG=""
PROFILES="--profile router"

while [ $# -gt 0 ]; do
    case "$1" in
        --build)
            BUILD_FLAG="--build"
            shift
            ;;
        --router-only)
            shift
            ;;
        --with)
            [ $# -ge 2 ] || { echo "ERROR: --with requires a profile name"; exit 1; }
            PROFILES="$PROFILES --profile $2"
            shift 2
            ;;
        --core)
            PROFILES="$PROFILES --profile glm51 --profile qwen35 --profile deepseek-v3 --profile kimi-k25 --profile gemma4"
            shift
            ;;
        --ollama-dev)
            PROFILES="$PROFILES --profile ollama-dev"
            shift
            ;;
        *)
            echo "ERROR: Unknown argument: $1"
            exit 1
            ;;
    esac
done

if [ ! -f .env ]; then
    echo "ERROR: .env not found. Copy from template:"
    echo "  cp .env.example .env"
    echo "  # Then fill in the required values"
    exit 1
fi

set -a
. ./.env
set +a

mkdir -p \
    "${CROWE_OPEN_MODEL_CACHE_DIR:-./data/models}" \
    "${CROWE_OPEN_OLLAMA_DIR:-./data/ollama}"

echo "═══ CroweLM Open Inference Deploy ═══"
echo "  Compose: $COMPOSE_FILE"
echo "  Build:   ${BUILD_FLAG:-no}"
echo "  Profiles:${PROFILES}"
echo

echo "→ Validating compose config..."
docker compose -f "$COMPOSE_FILE" $PROFILES config --quiet

echo "→ Pulling base images..."
docker compose -f "$COMPOSE_FILE" $PROFILES pull --ignore-buildable --quiet 2>/dev/null || true

echo "→ Starting services..."
docker compose -f "$COMPOSE_FILE" $PROFILES up -d $BUILD_FLAG

echo
echo "═══ Service Status ═══"
docker compose -f "$COMPOSE_FILE" $PROFILES ps

echo
echo "═══ OpenAI-Compatible Endpoint ═══"
echo "  Router: http://localhost:${CROWE_OPEN_PORT:-4000}/v1"
echo "  Set CROWE_OPEN_ENDPOINT=http://localhost:${CROWE_OPEN_PORT:-4000}/v1"
echo
echo "Note: use Ollama only for local dev/testing. Keep frontier production on vLLM workers."
