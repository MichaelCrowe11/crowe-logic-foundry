# Crowe Logic IDE — code-server on Azure VM

## Summary

Embed a full VS Code IDE (code-server) into the Crowe Logic AI platform at `ide.southwestmushrooms.com`, backed by crowe-logic-foundry running on an Azure VM with per-user Docker container isolation.

## Goals

- Give the admin (Michael) a full-featured, browser-based IDE with direct access to crowe-logic-foundry
- Give paying subscribers a restricted sandbox IDE for hands-on exploration of CroweLM tools
- Future-proof with container isolation from day 1 — no painful migration when user count grows
- Keep the existing infrastructure split: Azure (compute/AI), Railway (Next.js frontend), Supabase/Upstash (data)

## Architecture

```
ai.southwestmushrooms.com (Railway / Next.js)
|-- /ide (launcher page)
|   |-- Shows IDE status, workspace info, usage
|   |-- "Launch IDE" button -> ide.southwestmushrooms.com
|   |-- Auth check via Supabase session
|
ide.southwestmushrooms.com (Azure VM)
|-- Nginx (TLS termination, reverse proxy, auth validation)
|-- Session Router (lightweight Node.js service)
|   |-- Validates Supabase JWT from cookie/header
|   |-- Looks up user tier (admin vs subscriber)
|   |-- Spins up Docker container if none exists for user
|   |-- Proxies WebSocket + HTTP to correct container
|
|-- Docker containers (one per active user)
|   |-- Admin container
|   |   |-- code-server (full access, terminal, all extensions)
|   |       |-- /workspace -> crowe-logic-foundry (bind mount)
|   |
|   |-- Subscriber container(s)
|       |-- code-server (restricted: curated extensions, read-only areas, limited terminal)
|           |-- /workspace -> subscriber sandbox (isolated volume)
|
|-- Shared infrastructure
    |-- crowe-logic-foundry repo (host filesystem, bind-mounted to admin)
    |-- Docker network (isolated per container)
    |-- Container lifecycle manager (idle timeout, cleanup)
```

## Azure VM Specification

- **Size:** Standard B2s (2 vCPU, 4 GB RAM) — handles ~5-8 concurrent containers
- **OS:** Ubuntu 24.04 LTS
- **Disk:** 64 GB OS disk + 128 GB data disk (container images, workspaces)
- **Region:** Same region as Azure AI Foundry project (low latency to gpt-oss-120b)
- **Networking:** NSG allows ports 80/443 only. SSH access via Azure Bastion or IP-locked rule.
- **Estimated cost:** ~$30-40/mo for B2s. Scale to B4ms (~$60-80/mo) for more concurrent users.

**Installed software:**
- Docker Engine + Docker Compose
- Nginx (reverse proxy + TLS)
- Certbot (Let's Encrypt auto-renewal for ide.southwestmushrooms.com)
- Session Router (custom Node.js service, managed by systemd)

## Container Design

### Base Image

Extends `codercom/code-server:latest` with a custom Dockerfile:

- Python 3.12 + crowe-logic-foundry pip dependencies
- Pre-installed VS Code extensions: Python, Git, Jupyter, theme
- Custom `settings.json` with branded defaults
- Entrypoint: code-server with per-user config injection

### Two Container Profiles

| Feature | Admin | Subscriber |
|---------|-------|------------|
| Terminal | Full shell access | Restricted (no sudo, no network tools) |
| Filesystem | Bind-mount to real foundry repo | Isolated Docker volume (sandbox) |
| Extensions | All — install anything | Curated whitelist only |
| Settings | Full control | Locked (read-only settings.json) |
| CPU/Memory | 2 vCPU / 2 GB | 0.5 vCPU / 512 MB |
| Idle timeout | None (always available) | 30 min -> container stopped, 24h -> container removed |
| Workspace | `/workspace/crowe-logic-foundry` | `/workspace/sandbox` with starter files |

### Subscriber Sandbox Content

Subscribers get a curated workspace, not the raw foundry repo:

- Pre-loaded example scripts demonstrating crowe-logic-foundry tools
- Read-only copy of tool documentation
- Scratch area for experiments
- Sample datasets for CroweLM pipeline exploration
- No access to `.env`, credentials, or production data

## Authentication Flow

```
1. User logs into ai.southwestmushrooms.com (Supabase auth)
2. Clicks "Launch IDE" on /ide page
3. Next.js API route generates a short-lived JWT (60s expiry, single-use)
4. Browser redirects to ide.southwestmushrooms.com?token=<jwt>
5. Nginx passes request to Session Router
6. Session Router:
   a. Validates JWT against Supabase (checks exp, signature, single-use)
   b. Queries Supabase for user role (admin vs subscriber tier)
   c. Sets a secure httpOnly cookie scoped to ide.southwestmushrooms.com
   d. Finds existing container for user OR creates new one
   e. Proxies all traffic (HTTP + WebSocket) to that container's code-server port
7. Subsequent requests use the cookie (no token in URL)
```

### Security Boundaries

- **JWT handoff:** Single-use, 60-second expiry. Only used for the initial redirect.
- **Session cookie:** httpOnly, Secure, SameSite=Strict, scoped to ide.southwestmushrooms.com.
- **Container isolation:** Each container runs as a non-root Linux user. Subscriber containers have no network access to the host or other containers (isolated Docker network with `internal: true`).
- **Admin container:** Can reach host filesystem via bind mount. Cannot reach subscriber containers.
- **No credential leakage:** Subscriber containers never see `.env`, API keys, or production secrets.

## Session Router

A lightweight Node.js service (~200-300 lines) running on the Azure VM. Responsibilities:

1. **JWT validation** — Verify Supabase JWT signature and expiry
2. **User lookup** — Query Supabase for user ID and subscription tier
3. **Container lifecycle** — Start, stop, and remove Docker containers via Docker Engine API
4. **Request proxying** — Route HTTP and WebSocket traffic to the correct container's code-server port
5. **Port allocation** — Assign unique ports per container (range: 10000-10100)
6. **Idle cleanup** — Background job checks container activity, stops idle containers after 30 min, removes after 24h

**Tech:** Express.js + `dockerode` (Docker API client) + `http-proxy` (WebSocket-aware proxy)

## Launcher Page (/ide on ai.southwestmushrooms.com)

A Next.js page on the existing Railway-hosted app. Route: `app/(app)/ide/page.tsx`.

**Content:**
- IDE status indicator (VM online/offline, user's container running/stopped)
- "Launch IDE" button — calls API route to generate JWT, redirects to subdomain
- For subscribers: workspace info, usage stats, tier limits
- For admin: quick links to foundry workspace, container management status

**API route:** `app/api/ide/launch/route.ts` — generates the short-lived JWT for the auth handoff.

## DNS and TLS

- **DNS:** A record for `ide.southwestmushrooms.com` pointing to Azure VM public IP
- **TLS:** Certbot with Nginx plugin. Auto-renewing Let's Encrypt certificate.
- **Nginx configuration:**
  - Listens on 443 (TLS) with HTTP->HTTPS redirect on 80
  - Proxies to Session Router on `localhost:3001`
  - WebSocket upgrade headers configured (required for code-server)
  - Proxy read timeout set to 3600s (long-lived WebSocket connections)

## Explicit Non-Goals (YAGNI)

- **No Kubernetes** — Docker Compose on a single VM is sufficient for current scale
- **No CI/CD for the VM** — Manual deploys via SSH. Add automation later if needed.
- **No separate billing** — Subscriber access gated by existing Supabase subscription tier
- **No collaborative editing** — Each user gets their own container. No shared sessions.
- **No GPU** — Code-server does not need GPU. AI inference calls go through Azure AI Foundry API.
- **No custom VS Code fork** — Stock code-server with configuration and extensions only.
- **No mobile support** — code-server requires a desktop browser. The launcher page is responsive but the IDE itself is desktop-only.

## Component Summary

| Component | Location | Complexity |
|-----------|----------|------------|
| Azure VM provisioning | Azure portal / CLI | Infrastructure setup |
| Docker + Compose config | VM: `/opt/crowe-ide/` | ~50 lines YAML |
| Custom code-server Dockerfile | VM: `/opt/crowe-ide/Dockerfile` | ~40 lines |
| Nginx config | VM: `/etc/nginx/sites-available/ide` | ~30 lines |
| Session Router | VM: `/opt/crowe-ide/session-router/` | ~200-300 lines Node.js |
| Launcher page | Railway app: `app/(app)/ide/page.tsx` | ~100 lines React |
| Launch API route | Railway app: `app/api/ide/launch/route.ts` | ~50 lines |
| Subscriber sandbox template | VM: `/opt/crowe-ide/sandbox-template/` | Starter files + docs |
