#!/usr/bin/env bash
#
# Crowe Talon on NemoClaw: first-boot bootstrap.
#
# Runs inside the Brev-provisioned VM as root the first time the Launchable
# starts. Installs the NemoClaw stack (NIM + OpenShell), clones the Foundry
# repo, runs the recon script, and prints the .env snippet the operator
# pastes into their laptop.
#
# Idempotent: re-running is safe and only repeats steps whose state check
# fails. That makes it useful as a "repair" script too.

set -euo pipefail

FOUNDRY_REPO="${CROWE_FOUNDRY_REPO:-https://github.com/MichaelCrowe11/crowe-logic-foundry.git}"
FOUNDRY_BRANCH="${CROWE_FOUNDRY_BRANCH:-main}"
FOUNDRY_DIR="${CROWE_FOUNDRY_DIR:-/opt/crowe-logic-foundry}"
LOG=/var/log/crowe-talon-bootstrap.log
mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1

echo "==> Crowe Talon bootstrap starting at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---- 1. System prerequisites --------------------------------------------

if ! command -v git >/dev/null 2>&1; then
  echo "==> Installing git"
  apt-get update -qq
  apt-get install -y -qq git curl jq
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "==> Installing python3"
  apt-get install -y -qq python3 python3-pip python3-venv
fi

# ---- 2. Pull Foundry -----------------------------------------------------

if [ ! -d "$FOUNDRY_DIR/.git" ]; then
  echo "==> Cloning Foundry to $FOUNDRY_DIR"
  git clone --depth 1 --branch "$FOUNDRY_BRANCH" "$FOUNDRY_REPO" "$FOUNDRY_DIR"
else
  echo "==> Foundry already cloned; fetching latest"
  git -C "$FOUNDRY_DIR" fetch --depth 1 origin "$FOUNDRY_BRANCH"
  git -C "$FOUNDRY_DIR" reset --hard "origin/$FOUNDRY_BRANCH"
fi

cd "$FOUNDRY_DIR"

# ---- 3. NIM inference server --------------------------------------------

# The Brev base images generally ship with the NGC CLI but not a running
# NIM container. This block pulls and runs a NIM image if one is not yet
# listening on port 8000.
if ! curl -fsS --max-time 3 "http://127.0.0.1:8000/v1/models" >/dev/null 2>&1; then
  echo "==> NIM inference not detected on :8000, starting it"
  # Default model; override with NIM_IMAGE and NIM_MODEL env vars.
  NIM_IMAGE="${NIM_IMAGE:-nvcr.io/nim/meta/llama-3.1-nemotron-70b-instruct:latest}"
  docker pull "$NIM_IMAGE"
  docker run -d --restart=unless-stopped --gpus all \
    -p 8000:8000 \
    -e NGC_API_KEY="${NGC_API_KEY:-}" \
    --name crowe-nim \
    "$NIM_IMAGE" >/dev/null

  # Wait up to 5 minutes for the model to load.
  echo "==> Waiting for NIM to respond on /v1/models"
  for attempt in $(seq 1 60); do
    if curl -fsS --max-time 3 "http://127.0.0.1:8000/v1/models" >/dev/null 2>&1; then
      echo "    NIM ready after $((attempt * 5))s"
      break
    fi
    sleep 5
  done
else
  echo "==> NIM already responsive on :8000"
fi

# ---- 4. OpenShell sandbox -----------------------------------------------

# OpenShell ships as part of the NVIDIA Agent Toolkit. If the operator has
# pre-baked an image with OpenShell, the port check will skip this block.
if ! curl -fsS --max-time 3 "http://127.0.0.1:8001/openshell/v1/health" >/dev/null 2>&1; then
  echo "==> OpenShell sandbox not detected on :8001, starting it"
  OPENSHELL_IMAGE="${OPENSHELL_IMAGE:-nvcr.io/nvidia/agent-toolkit/openshell:latest}"
  if docker pull "$OPENSHELL_IMAGE" >/dev/null 2>&1; then
    docker run -d --restart=unless-stopped \
      -p 8001:8001 \
      --name crowe-openshell \
      "$OPENSHELL_IMAGE" >/dev/null
  else
    echo "    (!) Could not pull $OPENSHELL_IMAGE. Set OPENSHELL_IMAGE or install"
    echo "        OpenShell manually per the NVIDIA Agent Toolkit docs."
  fi
else
  echo "==> OpenShell already responsive on :8001"
fi

# ---- 5. Recon --------------------------------------------------------------

echo ""
echo "==> Running recon to confirm endpoints"
bash scripts/nemoclaw_recon.sh || true

# ---- 6. Post-boot summary for the operator ------------------------------

cat <<'EOF'

==============================================================================
Crowe Talon bootstrap complete.

Next steps (on your laptop, not on this VM):

  1. Copy the .env block printed above into
     ~/.config/crowe-logic/.env on your operator machine.
  2. In crowe-logic CLI:
       /model resolve talon-nemoclaw
       /model talon-nemoclaw
       ask: "run nemoclaw_health"
     You should see reachable: true.
  3. Or launch the full agent profile:
       crowe-logic launch crowe-talon

If recon printed [miss] lines, re-run scripts/nemoclaw_recon.sh here after
your NIM / OpenShell services settle, or override the paths manually.
==============================================================================
EOF
