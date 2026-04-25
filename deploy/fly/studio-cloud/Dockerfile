# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Crowe Studio cloud container — headless Ollama serving the
# shot-selection model(s) over an OpenAI-compatible endpoint.
#
# Sized for a 4-CPU / 8-GB Fly machine by default. Bump machine size
# if you move to larger models (DeepParallel 7B, 13B, etc).

FROM ollama/ollama:latest

ENV OLLAMA_HOST=0.0.0.0
ENV OLLAMA_MODELS=/models
ENV OLLAMA_KEEP_ALIVE=24h

# Model to preload. Override at deploy with --build-arg if you want a
# different one pulled into the image.
ARG CROWELM_MODEL=Mcrowe1210/DeepParallel:v2.2
ENV CROWELM_MODEL=${CROWELM_MODEL}

# Simple entrypoint that warms Ollama + pulls the model + then execs
# the server as PID 1 so Fly can send SIGTERM cleanly.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 11434

ENTRYPOINT ["/entrypoint.sh"]
