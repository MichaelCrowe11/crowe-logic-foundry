#!/usr/bin/env bash
# Container entrypoint for the Foundry control plane on Railway.
# 1. Run migrations (best effort; app can still start if migrations fail)
# 2. Start uvicorn on $PORT
#
# We intentionally do NOT use `set -e` because we want migration failures
# to be visible in logs rather than silently killing the container before
# uvicorn ever starts.

set -uo pipefail

log() { printf '[entrypoint] %s\n' "$*" >&2; }

log "starting entrypoint (PORT=${PORT:-unset})"

# Railway exposes Postgres credentials as DATABASE_URL when the Postgres
# plugin is referenced. Mirror it into CONTROL_PLANE_DATABASE_URL so the
# app picks it up without extra config.
if [[ -n "${DATABASE_URL:-}" && -z "${CONTROL_PLANE_DATABASE_URL:-}" ]]; then
  export CONTROL_PLANE_DATABASE_URL="$DATABASE_URL"
  log "mirrored DATABASE_URL into CONTROL_PLANE_DATABASE_URL"
fi

log "running migrations"
if python -u scripts/run_migrations.py; then
  log "migrations ok"
else
  rc=$?
  log "migrations FAILED (exit $rc) -- continuing so app can at least answer /health"
fi

log "starting uvicorn on 0.0.0.0:${PORT:-8001}"
exec python -u -m uvicorn control_plane.main:app \
     --host 0.0.0.0 \
     --port "${PORT:-8001}" \
     --proxy-headers \
     --forwarded-allow-ips='*'
