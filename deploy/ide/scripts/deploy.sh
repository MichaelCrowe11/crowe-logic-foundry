#!/usr/bin/env bash
# deploy.sh — One-command deploy/update for the Crowe Logic IDE stack.
#
# Usage:
#   cd deploy/ide
#   bash scripts/deploy.sh [--build] [--local-db] [--tls]
#
# Options:
#   --build     Force rebuild of all images
#   --local-db  Include local Postgres (otherwise uses CONTROL_PLANE_DATABASE_URL)
#   --tls       Include certbot auto-renewal
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE_FILE="docker-compose.full.yml"
BUILD_FLAG=""
PROFILES=""

for arg in "$@"; do
    case "$arg" in
        --build)    BUILD_FLAG="--build" ;;
        --local-db) PROFILES="$PROFILES --profile local-db" ;;
        --tls)      PROFILES="$PROFILES --profile tls" ;;
    esac
done

# Verify .env exists
if [ ! -f .env ]; then
    echo "ERROR: .env not found. Copy from template:"
    echo "  cp .env.full.example .env"
    echo "  # Then fill in the required values"
    exit 1
fi

echo "═══ Crowe Logic IDE — Full Stack Deploy ═══"
echo "  Compose: $COMPOSE_FILE"
echo "  Build:   ${BUILD_FLAG:-no}"
echo "  Profiles:${PROFILES:- (default)}"
echo

# Validate compose config
echo "→ Validating compose config..."
docker compose -f "$COMPOSE_FILE" $PROFILES config --quiet

# Pull base images
echo "→ Pulling base images..."
docker compose -f "$COMPOSE_FILE" $PROFILES pull --ignore-buildable --quiet 2>/dev/null || true

# Build and start
echo "→ Starting services..."
docker compose -f "$COMPOSE_FILE" $PROFILES up -d $BUILD_FLAG

# Wait for health
echo "→ Waiting for services to be healthy..."
TIMEOUT=90
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    STATUS=$(docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || true)
    if echo "$STATUS" | grep -q "(unhealthy)"; then
        sleep 5
        ELAPSED=$((ELAPSED + 5))
        continue
    fi
    if echo "$STATUS" | grep -c "(healthy)" | grep -q "^[2-9]"; then
        break
    fi
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done

echo
echo "═══ Service Status ═══"
docker compose -f "$COMPOSE_FILE" ps

echo
echo "═══ Endpoints ═══"
echo "  IDE:           https://ide.southwestmushrooms.com"
echo "  Control Plane: https://ide.southwestmushrooms.com/api/"
echo "  API Docs:      https://ide.southwestmushrooms.com/docs"
echo "  Health:        https://ide.southwestmushrooms.com/api/health"
echo
echo "Logs: docker compose -f $COMPOSE_FILE logs -f"
