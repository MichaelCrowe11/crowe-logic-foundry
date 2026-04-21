#!/usr/bin/env bash
# End-to-end smoke: start preview control plane, issue a tester key,
# call /api/gateway/chat with it, and print the response.
#
# Leaves the running preview server up if --keep is passed; otherwise
# cleans it up on exit.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

# Start preview in background
export PYTHONPATH="$ROOT"
"$PY" control_plane/preview.py >/tmp/crowe_preview.log 2>&1 &
PV=$!
cleanup() { [[ $KEEP -eq 1 ]] || kill "$PV" 2>/dev/null || true; }
trap cleanup EXIT

# Wait for /health
for _ in {1..30}; do
  if curl -sf http://127.0.0.1:8001/health >/dev/null; then break; fi
  sleep 0.3
done

echo "▶ Issuing tester key (local SQLite)…"
KEY=$("$PY" scripts/issue_tester_key.py --label e2e-smoke --plan lab --json \
        | python3 -c "import json,sys;print(json.load(sys.stdin)['key'])")
echo "  key_prefix=${KEY:0:11}"

echo "▶ Calling /api/gateway/chat with issued key…"
curl -sS -H "Authorization: Bearer $KEY" \
        -H "Content-Type: application/json" \
        -d '{"model":"gpt-5.4-nano","messages":[{"role":"user","content":"Say hello in 5 words"}],"max_tokens":20}' \
        http://127.0.0.1:8001/api/gateway/chat
echo
echo "✓ End-to-end OK"
if [[ $KEEP -eq 1 ]]; then
  echo "  preview server still running on :8001 (pid $PV)"
fi
