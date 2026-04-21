#!/usr/bin/env bash
# Container entrypoint for the Foundry control plane on Railway.
# 1. Run migrations (idempotent)
# 2. Start uvicorn on $PORT
set -euo pipefail

# Railway exposes Postgres credentials as DATABASE_URL when the Postgres
# plugin is referenced. Mirror it into CONTROL_PLANE_DATABASE_URL so the
# app picks it up without extra config.
if [[ -n "${DATABASE_URL:-}" && -z "${CONTROL_PLANE_DATABASE_URL:-}" ]]; then
  export CONTROL_PLANE_DATABASE_URL="$DATABASE_URL"
fi

echo "▶ Running migrations…"
python scripts/run_migrations.py

echo "▶ Starting uvicorn on :${PORT:-8001}"
exec uvicorn control_plane.main:app \
     --host 0.0.0.0 \
     --port "${PORT:-8001}" \
     --proxy-headers \
     --forwarded-allow-ips='*'
