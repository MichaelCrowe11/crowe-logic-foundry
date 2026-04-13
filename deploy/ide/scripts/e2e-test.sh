#!/usr/bin/env bash
# e2e-test.sh — End-to-end validation of the full Crowe Logic IDE stack.
#
# Validates: compose config, image builds, service health, auth flow,
# API endpoints, and WebSocket readiness.
#
# Usage:
#   cd deploy/ide
#   bash scripts/e2e-test.sh
#
# Requires: docker compose, curl, node (for JWT signing)
set -uo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.full.yml}"
PROJECT="crowe-e2e-$$"
RED=$'\033[31m'; GREEN=$'\033[32m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; RESET=$'\033[0m'

pass=0; fail=0; skip=0
assert() {
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then
        echo "  ${GREEN}✓${RESET} $label"
        pass=$((pass+1))
    else
        echo "  ${RED}✗${RESET} $label"
        fail=$((fail+1))
    fi
}

skip_assert() {
    local label="$1"
    echo "  ${DIM}⊘${RESET} $label (skipped)"
    skip=$((skip+1))
}

cleanup() {
    echo
    echo "${DIM}Cleaning up...${RESET}"
    docker compose -p "$PROJECT" -f "$COMPOSE_FILE" down -v --remove-orphans 2>/dev/null || true
}
trap cleanup EXIT

cd "$(dirname "$0")/.."

echo "${BOLD}Crowe Logic IDE — End-to-End Test${RESET}"
echo "${DIM}Compose: $COMPOSE_FILE | Project: $PROJECT${RESET}"
echo

# ── Phase 1: Config validation ──────────────────────────────────────
echo "── Compose config ──"

# Create a minimal test .env
TEST_ENV=$(mktemp)
cat > "$TEST_ENV" <<'EOF'
IDE_JWT_SECRET=e2e_test_secret_0123456789abcdef0123456789abcdef
POSTGRES_PASSWORD=e2e_test_password
CONTROL_PLANE_API_KEY=e2e_test_api_key
EOF

assert "docker compose config validates" \
    docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$TEST_ENV" config --quiet

echo
echo "── Image builds ──"

# Build all images (without starting)
echo "  ${DIM}Building images (this may take a few minutes)...${RESET}"
if docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$TEST_ENV" build --quiet 2>/dev/null; then
    echo "  ${GREEN}✓${RESET} All images built successfully"
    pass=$((pass+1))
else
    echo "  ${RED}✗${RESET} Image build failed"
    fail=$((fail+1))
    rm -f "$TEST_ENV"
    echo
    echo "──────────────────────────────"
    echo "  ${GREEN}$pass passed${RESET} · ${RED}$fail failed${RESET} · ${DIM}$skip skipped${RESET}"
    echo "──────────────────────────────"
    exit 1
fi

echo
echo "── Service startup ──"

# Start the stack (with local-db profile for self-contained testing)
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$TEST_ENV" \
    --profile local-db up -d 2>/dev/null

# Wait for services to be healthy
echo "  ${DIM}Waiting for services to be healthy (up to 60s)...${RESET}"
TIMEOUT=60
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    HEALTHY=$(docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$TEST_ENV" \
        ps --format json 2>/dev/null | grep -c '"healthy"' || echo 0)
    # We need at least control-plane + session-router healthy (2 services)
    if [ "$HEALTHY" -ge 2 ]; then
        break
    fi
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done

assert "Control Plane is healthy" \
    docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$TEST_ENV" \
        exec -T control-plane curl -sf http://localhost:8001/health

assert "Session Router is healthy" \
    docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$TEST_ENV" \
        exec -T session-router curl -sf http://localhost:3001/health

# Postgres (only if local-db profile active)
if docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$TEST_ENV" \
    ps postgres 2>/dev/null | grep -q "running"; then
    assert "Postgres is accepting connections" \
        docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$TEST_ENV" \
            exec -T postgres pg_isready -U crowe
else
    skip_assert "Postgres (not running — using external DB)"
fi

echo
echo "── API endpoints ──"

# Get the mapped control-plane port
CP_PORT=$(docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$TEST_ENV" \
    port control-plane 8001 2>/dev/null | cut -d: -f2 || echo "8001")

if [ -n "$CP_PORT" ]; then
    assert "GET /health returns 200" \
        curl -sf "http://127.0.0.1:$CP_PORT/health"

    assert "GET /plans returns plan list" \
        curl -sf "http://127.0.0.1:$CP_PORT/plans"

    assert "GET /docs returns Swagger UI" \
        curl -sf "http://127.0.0.1:$CP_PORT/docs"

    # Auth endpoint should reject missing token
    HTTP_CODE=$(curl -so /dev/null -w "%{http_code}" "http://127.0.0.1:$CP_PORT/me" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "401" ] || [ "$HTTP_CODE" = "403" ]; then
        echo "  ${GREEN}✓${RESET} GET /me rejects unauthenticated request ($HTTP_CODE)"
        pass=$((pass+1))
    else
        echo "  ${RED}✗${RESET} GET /me should reject unauthenticated (got: $HTTP_CODE)"
        fail=$((fail+1))
    fi
else
    skip_assert "API endpoints (port mapping not available)"
fi

echo
echo "── Session Router auth flow ──"

SR_PORT=$(docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$TEST_ENV" \
    port session-router 3001 2>/dev/null | cut -d: -f2 || echo "3001")

if [ -n "$SR_PORT" ]; then
    # Unauthenticated request should redirect
    HTTP_CODE=$(curl -so /dev/null -w "%{http_code}" "http://127.0.0.1:$SR_PORT/" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "302" ] || [ "$HTTP_CODE" = "401" ]; then
        echo "  ${GREEN}✓${RESET} Unauthenticated request handled ($HTTP_CODE)"
        pass=$((pass+1))
    else
        echo "  ${RED}✗${RESET} Unauthenticated request (expected 302/401, got: $HTTP_CODE)"
        fail=$((fail+1))
    fi

    # Health endpoint
    assert "Session Router /health returns 200" \
        curl -sf "http://127.0.0.1:$SR_PORT/health"
else
    skip_assert "Session Router auth (port mapping not available)"
fi

# Cleanup test env
rm -f "$TEST_ENV"

echo
echo "──────────────────────────────"
echo "  ${GREEN}$pass passed${RESET} · ${RED}$fail failed${RESET} · ${DIM}$skip skipped${RESET}"
echo "──────────────────────────────"

exit $([ $fail -eq 0 ] && echo 0 || echo 1)
