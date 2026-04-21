#!/usr/bin/env bash
#
# NemoClaw reconnaissance. Run this INSIDE the Brev VM (via `brev shell
# <vm-name>`) to discover the live inference + OpenShell endpoints and emit
# an .env snippet ready to paste into your local
# ~/.config/crowe-logic/.env.
#
# Why this exists: NemoClaw is alpha software and the OpenShell API path
# has been moving. Rather than hardcode assumptions, we probe the box.
#
# Usage (on the VM):
#   curl -fsSL https://raw.githubusercontent.com/<your-org>/crowe-logic-foundry/main/scripts/nemoclaw_recon.sh | bash
# or if you've already cloned Foundry to the VM:
#   bash scripts/nemoclaw_recon.sh
#
# The script is read-only. It does not install anything.

set -u

echo "==> NemoClaw reconnaissance"
echo "    host: $(hostname)"
echo "    date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# 1. Candidate inference + sandbox ports.
INFERENCE_PORTS=(8000 8080 8888 80)
SANDBOX_PORTS=(8001 8081 9000 80)

# 2. Candidate OpenShell paths (best-guess from the NVIDIA Agent Toolkit
# documentation surface).
EXEC_CANDIDATES=(
  "/openshell/v1/exec"
  "/v1/openshell/exec"
  "/v1/shell/exec"
  "/sandbox/v1/exec"
  "/api/v1/shell/exec"
)
HEALTH_CANDIDATES=(
  "/openshell/v1/health"
  "/v1/openshell/health"
  "/v1/shell/health"
  "/healthz"
  "/health"
)

# 3. Candidate inference paths.
MODELS_CANDIDATES=(
  "/v1/models"
  "/openai/v1/models"
  "/api/v1/models"
)

found_inference_port=""
found_inference_path=""
found_model=""
found_sandbox_port=""
found_exec_path=""
found_health_path=""

probe_get() {
  local url="$1"
  curl -fsS --max-time 5 -o /tmp/nemoclaw_recon_body -w "%{http_code}" "$url" 2>/dev/null || echo "000"
}

echo "==> Probing inference surface..."
for port in "${INFERENCE_PORTS[@]}"; do
  for path in "${MODELS_CANDIDATES[@]}"; do
    url="http://127.0.0.1:${port}${path}"
    code=$(probe_get "$url")
    if [ "$code" = "200" ]; then
      found_inference_port="$port"
      found_inference_path="$path"
      # Try to pick the first model id out of the listing.
      if command -v python3 >/dev/null; then
        found_model=$(python3 -c "
import json, sys
try:
    with open('/tmp/nemoclaw_recon_body') as f:
        d = json.load(f)
    for m in d.get('data', []):
        print(m.get('id', ''))
        break
except Exception:
    pass
" || true)
      fi
      echo "    [hit] ${url} (model: ${found_model:-unknown})"
      break 2
    fi
  done
done
if [ -z "$found_inference_port" ]; then
  echo "    [miss] no inference endpoint found on common ports/paths"
fi
echo ""

echo "==> Probing OpenShell sandbox surface..."
for port in "${SANDBOX_PORTS[@]}"; do
  for hp in "${HEALTH_CANDIDATES[@]}"; do
    url="http://127.0.0.1:${port}${hp}"
    code=$(probe_get "$url")
    if [ "$code" = "200" ] || [ "$code" = "204" ]; then
      found_sandbox_port="$port"
      found_health_path="$hp"
      echo "    [hit] health ${url} -> ${code}"
      break 2
    fi
  done
done

# Probe exec paths (POST so we don't 404 on a GET-only handler).
if [ -n "$found_sandbox_port" ]; then
  for ep in "${EXEC_CANDIDATES[@]}"; do
    url="http://127.0.0.1:${found_sandbox_port}${ep}"
    code=$(curl -fsS --max-time 5 -X POST -H 'Content-Type: application/json' \
      -d '{"command":"echo recon","timeout_seconds":5}' \
      -o /tmp/nemoclaw_recon_exec -w "%{http_code}" "$url" 2>/dev/null || echo "000")
    # 200 is ideal, but 401/403 also mean the path exists (just needs auth),
    # and 422 means the schema is close but our payload is slightly off.
    case "$code" in
      200|202|401|403|422)
        found_exec_path="$ep"
        echo "    [hit] exec   ${url} -> ${code}"
        break
        ;;
    esac
  done
fi

if [ -z "$found_sandbox_port" ]; then
  echo "    [miss] no OpenShell endpoint found on common ports/paths"
  echo "           (try: ss -tlnp | grep LISTEN  to see what's bound)"
fi
echo ""

# 4. Brev secure link for the public-facing URL. The $80 port on the VM is
# proxied to the brevlab.com secure link shown in the Brev UI. If that's
# still what the VM exposes, the public base URL is:
public_base=""
if command -v hostname >/dev/null; then
  short=$(hostname | tr '[:upper:]' '[:lower:]')
  public_base="https://${short}.brevlab.com"
fi

echo "==> .env snippet (paste into ~/.config/crowe-logic/.env on your laptop)"
echo ""
echo "# --- NemoClaw (generated $(date -u +%Y-%m-%dT%H:%M:%SZ)) ---"
if [ -n "$public_base" ]; then
  echo "NEMOCLAW_ENDPOINT=${public_base}"
else
  echo "NEMOCLAW_ENDPOINT=https://<fill-in-brevlab-url>"
fi
echo "NEMOCLAW_API_KEY=<paste your Brev access token or NemoClaw bearer>"
if [ -n "$found_exec_path" ]; then
  echo "NEMOCLAW_SANDBOX_EXEC_PATH=${found_exec_path}"
fi
if [ -n "$found_health_path" ]; then
  echo "NEMOCLAW_SANDBOX_HEALTH_PATH=${found_health_path}"
fi
if [ -n "$found_sandbox_port" ] && [ "$found_sandbox_port" != "80" ]; then
  # The sandbox listens on a non-default port so we need a direct URL.
  echo "# Sandbox on non-default port; add a Brev port-share or tunnel:"
  echo "# NEMOCLAW_SANDBOX_URL=https://<brev-port-share-url-for-${found_sandbox_port}>"
fi
echo ""
echo "==> Model entry for config/models.extra.json"
if [ -n "$found_model" ]; then
  echo "    Update crowelm-talon-nemoclaw.backend_name to: ${found_model}"
else
  echo "    Could not auto-detect a model id. Manually hit"
  echo "    ${public_base:-http://127.0.0.1:<inference-port>}/v1/models and"
  echo "    read the first \"id\" field."
fi
echo ""
echo "==> Done. If both [hit] lines appeared above, you are ready to wire this up."
