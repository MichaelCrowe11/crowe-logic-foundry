#!/usr/bin/env bash
# /usr/local/bin/crowe-agent — default integrated-terminal profile = the Crowe Logic
# CLI agent (not bash, not Claude Code). bash/Claude Code stay under "Diagnostics".
# Only crowe-logic wired; deepparallel/crowelm-cloud intentionally absent until real.
set -uo pipefail
if [ -x /opt/venv/bin/crowe-logic ]; then exec /opt/venv/bin/crowe-logic "$@"; fi
if command -v crowe-logic >/dev/null 2>&1; then exec crowe-logic "$@"; fi
if [ -d /workspace ] && /opt/venv/bin/python -c "import cli.crowe_logic" >/dev/null 2>&1; then
  exec /opt/venv/bin/python -m cli.crowe_logic "$@"
fi
echo "crowe-logic CLI not found; dropping to a shell. (Diagnostics profile = bash.)" >&2
exec "${SHELL:-/bin/bash}"
