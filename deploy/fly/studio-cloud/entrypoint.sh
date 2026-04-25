#!/bin/sh
# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Warm Ollama, pull the CroweLM model once per image build or volume
# mount, then hand off PID 1 to the server so Fly signals work.

set -e

echo "[crowe-studio-cloud] model=${CROWELM_MODEL}"
echo "[crowe-studio-cloud] models dir=${OLLAMA_MODELS}"

# Start the server in the background so we can pull.
ollama serve &
SERVER_PID=$!

# Wait for readiness (up to 30s)
for i in $(seq 1 30); do
  if curl -s -f http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# Idempotent pull. If the model already exists on the volume, this is
# fast (hash comparison only).
if ! ollama list | grep -q "${CROWELM_MODEL%:*}"; then
  echo "[crowe-studio-cloud] pulling ${CROWELM_MODEL}..."
  ollama pull "${CROWELM_MODEL}" || echo "[crowe-studio-cloud] pull failed, continuing with whatever is available"
fi

# Hand off to the server process.
wait "${SERVER_PID}"
