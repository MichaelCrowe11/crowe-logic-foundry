#!/usr/bin/env bash
# entrypoint.sh — Custom entrypoint for Crowe Logic IDE containers.
#
# Handles per-user workspace setup, secrets injection, Python path
# configuration, and code-server launch. Run as the `coder` user
# (set by USER in the Dockerfile).
set -euo pipefail

# ── Workspace setup ─────────────────────────────────────────────────
WORKSPACE_DIR="${WORKSPACE_DIR:-/workspace}"
mkdir -p "$WORKSPACE_DIR"

# Seed the sandbox template if the workspace is empty (first launch)
if [ -z "$(ls -A "$WORKSPACE_DIR" 2>/dev/null)" ] && [ -d /opt/sandbox-template ]; then
  cp -r /opt/sandbox-template/* "$WORKSPACE_DIR/" 2>/dev/null || true
  echo "[entrypoint] Seeded workspace from sandbox template"
fi

# ── Git config (if user email is available from env) ────────────────
if [ -n "${CROWE_USER_EMAIL:-}" ]; then
  git config --global user.email "$CROWE_USER_EMAIL"
  git config --global user.name "${CROWE_USER_NAME:-Crowe Logic User}"
fi

# ── Python venv activation ──────────────────────────────────────────
export PATH="/opt/venv/bin:$PATH"
export PYTHONPATH="${WORKSPACE_DIR}/crowe-logic-foundry:${PYTHONPATH:-}"

# ── Control Plane connection (if configured) ────────────────────────
if [ -n "${CONTROL_PLANE_URL:-}" ]; then
  echo "[entrypoint] Control Plane: $CONTROL_PLANE_URL"
fi

# ── Jupyter config (if notebooks are enabled) ───────────────────────
JUPYTER_DIR="/home/coder/.jupyter"
if [ ! -f "$JUPYTER_DIR/jupyter_notebook_config.py" ]; then
  mkdir -p "$JUPYTER_DIR"
  cat > "$JUPYTER_DIR/jupyter_notebook_config.py" <<'PYEOF'
c.NotebookApp.ip = '0.0.0.0'
c.NotebookApp.open_browser = False
c.NotebookApp.token = ''
c.NotebookApp.password = ''
c.NotebookApp.allow_origin = '*'
c.ServerApp.ip = '0.0.0.0'
c.ServerApp.open_browser = False
c.ServerApp.token = ''
c.ServerApp.allow_origin = '*'
PYEOF
  echo "[entrypoint] Configured Jupyter defaults"
fi

# ── Launch code-server ──────────────────────────────────────────────
echo "[entrypoint] Starting code-server in $WORKSPACE_DIR"
exec code-server "$@" "$WORKSPACE_DIR"
