FROM python:3.12-slim AS base

LABEL maintainer="michael@crowelogic.com"
LABEL org.opencontainers.image.title="Crowe Logic Agent"
LABEL org.opencontainers.image.description="Universal AI Agent powered by the CroweLM model stack on Azure AI Foundry"
LABEL org.opencontainers.image.vendor="Crowe Logic, Inc."

WORKDIR /app

# System dependencies (build-essential for C extensions, ripgrep for search)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ripgrep \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js for MCP servers
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# MCP servers
COPY package.json package-lock.json* ./
RUN npm install --production

# Pre-warm npm cache with popular MCP servers for faster first-use
# (npx -y will use cache instead of re-downloading on first agent use)
RUN npx -y @modelcontextprotocol/server-postgres@latest --help > /dev/null 2>&1 || true && \
    npx -y @modelcontextprotocol/server-sqlite@latest --help > /dev/null 2>&1 || true && \
    npx -y @modelcontextprotocol/server-memory@latest --help > /dev/null 2>&1 || true && \
    npx -y @modelcontextprotocol/server-fetch@latest --help > /dev/null 2>&1 || true

# Application code
COPY . .

# Install crowe-logic CLI
RUN pip install --no-cache-dir -e .

# Default: interactive chat
ENTRYPOINT ["crowe-logic"]
CMD ["chat"]

# ── GPU variant (build with: docker build --target gpu) ───
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04 AS gpu

LABEL maintainer="michael@crowelogic.com"
LABEL org.opencontainers.image.title="Crowe Logic Agent (GPU)"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3-pip git ripgrep curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir qiskit qiskit-aer cirq pennylane \
    synapse-lang synapse-qubit-flow \
    torch --index-url https://download.pytorch.org/whl/cu124

COPY package.json package-lock.json* ./
RUN npm install --production

# Pre-warm MCP servers (GPU variant)
RUN npx -y @modelcontextprotocol/server-postgres@latest --help > /dev/null 2>&1 || true && \
    npx -y @modelcontextprotocol/server-memory@latest --help > /dev/null 2>&1 || true

COPY . .
RUN pip install --no-cache-dir -e ".[quantum]"

ENTRYPOINT ["crowe-logic"]
CMD ["chat"]

# ── Default target for CI/Render/Fly (must be last FROM) ──
FROM base
