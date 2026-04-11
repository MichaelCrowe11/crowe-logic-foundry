#!/usr/bin/env bash
# Smoke test for the Crowe Logic IDE image.
#
# Spins up a transient container from crowe-logic-ide:test and runs a
# series of assertions: the Crowe Logic extension installed, the
# product.json overrides applied, the Foundry venv works, the headless
# protocol responds, and code-server boots far enough to serve HTTP.
#
# Exit code 0 = all green, 1 = any assertion failed. Designed to be
# the gating step before tagging the image for rollout.
set -uo pipefail

IMAGE="${IMAGE:-crowe-logic-ide:test}"
CONTAINER="crowe-logic-smoke-$$"
# Mount the Foundry source the same way production does
# (/opt/crowe-logic-foundry → /workspace/crowe-logic-foundry per the
# admin compose service). Default to the working copy two levels up
# from this script.
FOUNDRY_SRC="${FOUNDRY_SRC:-$(cd "$(dirname "$0")/../../.." && pwd)}"
RED=$'\033[31m'; GREEN=$'\033[32m'; DIM=$'\033[2m'; RESET=$'\033[0m'

pass=0; fail=0
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

cleanup() {
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "${DIM}Image:       $IMAGE${RESET}"
echo "${DIM}Container:   $CONTAINER${RESET}"
echo "${DIM}Foundry src: $FOUNDRY_SRC${RESET}"
echo

# Sanity: image exists locally
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "${RED}Image $IMAGE not found. Build it first:${RESET}"
    echo "  docker build -f Dockerfile.code-server -t $IMAGE ."
    exit 1
fi

if [ ! -f "$FOUNDRY_SRC/cli/headless.py" ]; then
    echo "${RED}Foundry source not found at $FOUNDRY_SRC${RESET}"
    echo "Set FOUNDRY_SRC to the crowe-logic-foundry checkout root."
    exit 1
fi

# Start the container detached, with the Foundry source bind-mounted
# the same way production does (compose admin service mounts
# /opt/crowe-logic-foundry → /workspace/crowe-logic-foundry). The
# entrypoint is overridden to keep it alive without code-server
# auto-starting; assertions run via docker exec.
docker run -d --rm --name "$CONTAINER" \
    -v "$FOUNDRY_SRC:/workspace/crowe-logic-foundry:ro" \
    --entrypoint sleep "$IMAGE" 600 >/dev/null

echo "── Filesystem layout ──"
assert "Foundry venv exists at /opt/venv" \
    docker exec "$CONTAINER" test -x /opt/venv/bin/python3
assert "ripgrep installed" \
    docker exec "$CONTAINER" test -x /usr/bin/rg
assert "apply-product-overrides.sh installed" \
    docker exec "$CONTAINER" test -x /usr/local/bin/apply-product-overrides.sh

echo
echo "── Foundry Python environment ──"
assert "openai package installed" \
    docker exec "$CONTAINER" /opt/venv/bin/python3 -c 'import openai'
assert "rich package installed" \
    docker exec "$CONTAINER" /opt/venv/bin/python3 -c 'import rich'

echo
echo "── VS Code extension ──"
# Pre-create code-server's config dir as the coder user. Without this,
# the first invocation writes "Wrote default config file..." to stdout
# and pollutes our extension list parsing. Idempotent: safe to run on
# every container start.
docker exec --user coder "$CONTAINER" sh -c '
    mkdir -p /home/coder/.config/code-server
    test -f /home/coder/.config/code-server/config.yaml || cat > /home/coder/.config/code-server/config.yaml <<EOF
bind-addr: 127.0.0.1:8080
auth: none
cert: false
EOF
' >/dev/null 2>&1
EXT_LIST=$(docker exec --user coder "$CONTAINER" code-server --list-extensions 2>/dev/null | grep -v '^$' || true)
echo "$EXT_LIST" | grep -qi "crowe-logic" \
    && { echo "  ${GREEN}✓${RESET} crowe-logic extension installed"; pass=$((pass+1)); } \
    || { echo "  ${RED}✗${RESET} crowe-logic extension installed (got: $EXT_LIST)"; fail=$((fail+1)); }
echo "$EXT_LIST" | grep -q "ms-python.python" \
    && { echo "  ${GREEN}✓${RESET} ms-python.python installed"; pass=$((pass+1)); } \
    || { echo "  ${RED}✗${RESET} ms-python.python installed"; fail=$((fail+1)); }

echo
echo "── product.json overrides ──"
PRODUCT_PATHS=(
    /usr/lib/code-server/lib/vscode/product.json
    /usr/lib/code-server/out/vs/platform/product/common/product.json
    /usr/lib/code-server/product.json
)
PRODUCT_FOUND=""
for p in "${PRODUCT_PATHS[@]}"; do
    if docker exec "$CONTAINER" test -f "$p"; then
        PRODUCT_FOUND="$p"
        break
    fi
done
if [ -n "$PRODUCT_FOUND" ]; then
    echo "  ${GREEN}✓${RESET} product.json located at $PRODUCT_FOUND"
    pass=$((pass+1))
    # Check the merge actually applied — nameShort should be "Crowe Logic"
    NAME=$(docker exec "$CONTAINER" python3 -c "
import json
with open('$PRODUCT_FOUND') as f: print(json.load(f).get('nameShort',''))
" 2>/dev/null)
    if [ "$NAME" = "Crowe Logic" ]; then
        echo "  ${GREEN}✓${RESET} product.json nameShort = 'Crowe Logic'"
        pass=$((pass+1))
    else
        echo "  ${RED}✗${RESET} product.json nameShort merge (got: '$NAME')"
        fail=$((fail+1))
    fi
    assert "product.json.orig backup exists" \
        docker exec "$CONTAINER" test -f "${PRODUCT_FOUND}.orig"
else
    echo "  ${RED}✗${RESET} product.json not found in any candidate path"
    fail=$((fail+1))
fi

echo
echo "── Headless protocol (no model call) ──"
# Validate the headless module imports cleanly and rejects bad input
# with a structured error event. This doesn't hit the LLM — that
# would require API keys in the container — but it does prove the
# whole import chain (cli.headless → providers._shared → tools) works.
HEADLESS_OUT=$(docker exec -i -w /workspace/crowe-logic-foundry "$CONTAINER" \
    /opt/venv/bin/python3 -m cli.headless 2>&1 <<<'{"messages":[]}' || true)
echo "$HEADLESS_OUT" | grep -q '"type": "error"' \
    && echo "$HEADLESS_OUT" | grep -q '"kind": "input"' \
    && { echo "  ${GREEN}✓${RESET} headless emits structured input error"; pass=$((pass+1)); } \
    || { echo "  ${RED}✗${RESET} headless protocol (got: $HEADLESS_OUT)"; fail=$((fail+1)); }

echo
echo "── code-server boot (HTTP) ──"
docker exec -d "$CONTAINER" code-server \
    --bind-addr 0.0.0.0:8080 \
    --auth none \
    --disable-telemetry \
    /workspace
sleep 4
HTTP_CODE=$(docker exec "$CONTAINER" curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/ 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "302" ]; then
    echo "  ${GREEN}✓${RESET} code-server serving HTTP ($HTTP_CODE)"
    pass=$((pass+1))
else
    echo "  ${RED}✗${RESET} code-server HTTP (got: $HTTP_CODE)"
    fail=$((fail+1))
fi

echo
echo "──────────────────────────────"
echo "  ${GREEN}$pass passed${RESET} · ${RED}$fail failed${RESET}"
echo "──────────────────────────────"

exit $([ $fail -eq 0 ] && echo 0 || echo 1)
