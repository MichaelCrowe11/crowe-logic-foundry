# Crowe Logic IDE — Deployment Runbook

End-to-end deployment guide for `ide.southwestmushrooms.com`. Follow phases in order. Each command is paste-ready; substitute the marked placeholders before running.

> **Audience:** the operator deploying the IDE for the first time, or rebuilding after a VM loss. Assumes familiarity with `az` CLI, SSH, and basic Linux administration.

---

## Prerequisites

Before starting, confirm you have:

- [ ] **Azure CLI** installed and logged in: `az login` (verify with `az account show`)
- [ ] **Active Azure subscription** with permission to create VMs and public IPs
- [ ] **DNS access** to the `southwestmushrooms.com` zone (Cloudflare, Route53, etc.)
- [ ] **Existing Supabase project** that the `crowe-logic-ai` Railway app already uses
- [ ] **Railway dashboard access** for the `crowe-logic-ai` project
- [ ] **SSH key** on the local machine running this runbook (`ls ~/.ssh/id_rsa.pub`)
- [ ] This repo cloned at `/Users/<you>/Projects/crowe-logic-foundry` (paths assume macOS)

---

## Phase 0 — Generate the shared JWT secret

**This must be done first.** The same secret is used in two places: the Session Router on the Azure VM, and the `crowe-logic-ai` Railway app. If they drift, IDE handoff JWT verification fails with 401.

```bash
# Generate a 256-bit hex secret
openssl rand -hex 32
# → e.g. 8f3a9e4b2c1d6f0e... (save this somewhere safe — you need it twice)
```

Save it as `IDE_JWT_SECRET=<value>` in your password manager. You will paste it into:
1. The VM's `/opt/crowe-ide/session-router/.env` (Phase 5)
2. The Railway environment variables for `crowe-logic-ai` (Phase 7)

---

## Phase 1 — Provision the Azure VM

```bash
# Variables — edit these once, reuse below
RG=crowe-logic-ide
LOCATION=eastus2
VM_NAME=crowe-ide-vm
VM_SIZE=Standard_B2s

# Create resource group (idempotent)
az group create --name $RG --location $LOCATION

# Create the VM (Ubuntu 24.04 LTS, 2 vCPU / 4GB RAM, 64GB OS + 128GB data disk)
az vm create \
  --resource-group $RG \
  --name $VM_NAME \
  --image Ubuntu2404 \
  --size $VM_SIZE \
  --admin-username crowelogic \
  --generate-ssh-keys \
  --os-disk-size-gb 64 \
  --data-disk-sizes-gb 128 \
  --public-ip-sku Standard \
  --nsg-rule SSH

# Open HTTP/HTTPS ports
az vm open-port --resource-group $RG --name $VM_NAME --port 80  --priority 1001
az vm open-port --resource-group $RG --name $VM_NAME --port 443 --priority 1002

# Capture the public IP — you need this for DNS in Phase 2
VM_IP=$(az vm show -d --resource-group $RG --name $VM_NAME --query publicIps -o tsv)
echo "VM_IP=$VM_IP"
```

**Verification:** `ping $VM_IP` should respond. `az vm show -d -g $RG -n $VM_NAME --query "powerState"` should report `VM running`.

**Cost:** Standard_B2s + 128GB premium disk + Standard public IP ≈ **$30–40/month**.

---

## Phase 2 — Configure DNS

Add an **A record** for `ide.southwestmushrooms.com` pointing at `$VM_IP`.

| Field | Value |
|---|---|
| Type | A |
| Name | `ide` |
| Value | (paste `$VM_IP` from Phase 1) |
| TTL | 300 (5 min) |
| Proxy | **OFF** if Cloudflare — Let's Encrypt needs direct origin |

**Verification:** wait 1-5 minutes, then:

```bash
dig +short ide.southwestmushrooms.com
# → should print the same IP as $VM_IP
```

Do not proceed to Phase 4 (TLS) until DNS resolves correctly — certbot will fail otherwise.

---

## Phase 3 — Bootstrap the VM

```bash
# Copy the bootstrap script to the VM
scp deploy/ide/setup.sh crowelogic@$VM_IP:/tmp/setup.sh

# SSH in and run it as root
ssh crowelogic@$VM_IP "sudo bash /tmp/setup.sh"
```

The script installs Docker, Nginx, certbot, Node.js 22, creates the `crowe-ide` system user, and adds it to the `docker` group so the Session Router can talk to `/var/run/docker.sock`. Total runtime ≈ 3–5 minutes.

**Verification:** `ssh crowelogic@$VM_IP "docker --version && nginx -v && node --version"` should report Docker 20+, Nginx 1.24+, Node 22.x.

---

## Phase 4 — Deploy application files

From your local machine (in the foundry repo root):

```bash
# Copy the entire deploy/ide tree (excluding node_modules and any local .env)
rsync -av --exclude 'node_modules' --exclude '.env' \
  deploy/ide/ crowelogic@$VM_IP:/opt/crowe-ide/
```

`deploy/ide/requirements.txt` is committed to the repo and rsynced along with everything else, so the Docker build context already contains it — no separate `scp` step needed. If you change the foundry's root `requirements.txt`, copy it into `deploy/ide/requirements.txt` before rsyncing so the image stays in sync.

**Verification:** `ssh crowelogic@$VM_IP "ls /opt/crowe-ide/"` should list `Dockerfile.code-server`, `docker-compose.yml`, `docker-compose.full.yml`, `requirements.txt`, `nginx/`, `session-router/`, `systemd/`, `sandbox-template/`, `scripts/`.

### Full-stack deployment (recommended)

After rsyncing files, use the one-command deploy script:

```bash
# SSH into the VM
ssh crowelogic@$VM_IP

# Set up .env from template
cd /opt/crowe-ide
sudo cp .env.full.example .env
sudo nano .env  # Fill in all required values

# Deploy with local Postgres + TLS auto-renewal
sudo bash scripts/deploy.sh --build --local-db --tls
```

This builds all images, starts Postgres, Control Plane, Session Router, Admin container, Nginx, and Certbot in one command.

---

## Phase 5 — Configure and start services

SSH into the VM for the rest of this phase: `ssh crowelogic@$VM_IP`

### 5a — Session Router dependencies + .env

```bash
cd /opt/crowe-ide/session-router
sudo npm install --omit=dev

# Create the real .env (NOT checked into git)
sudo cp .env.example .env
sudo nano .env
```

Set the following values in `.env`:

```bash
IDE_JWT_SECRET=<paste the secret from Phase 0>
PORT=3001
IMAGE_NAME=crowe-ide-codeserver
COOKIE_DOMAIN=ide.southwestmushrooms.com
IDLE_STOP_MINUTES=240
IDLE_REMOVE_HOURS=24
IDLE_CHECK_INTERVAL_MINUTES=5
```

`IDE_JWT_SECRET` is the **only** auth secret the router needs. It must be byte-identical to the value set in Railway for `crowe-logic-ai` (Phase 7) — the launcher signs handoff JWTs with this secret and the router both verifies handoffs and mints session cookies with it. Drift causes every handoff to 401.

Lock down permissions:

```bash
sudo chown crowe-ide:crowe-ide .env
sudo chmod 600 .env
```

### 5b — Nginx config + TLS certificate

```bash
sudo cp /opt/crowe-ide/nginx/ide.conf /etc/nginx/sites-available/ide
sudo ln -sf /etc/nginx/sites-available/ide /etc/nginx/sites-enabled/ide
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

# Issue Let's Encrypt cert. The --redirect flag adds HTTPS-only enforcement.
sudo certbot --nginx -d ide.southwestmushrooms.com --redirect \
  --agree-tos -m crowelogicos@gmail.com --no-eff-email
```

**Verification:** `curl -I https://ide.southwestmushrooms.com/health` should return `HTTP/2 502` (Session Router not started yet) — but the TLS handshake must succeed.

### 5c — Build the code-server image

```bash
cd /opt/crowe-ide
sudo docker compose build admin
```

This builds the custom `crowe-ide-codeserver` image (the compose service is named `admin`) with all extensions baked in. Takes 5–10 minutes on first run.

### 5d — Install and start the systemd service

```bash
sudo cp /opt/crowe-ide/systemd/crowe-ide-router.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now crowe-ide-router

# Verify it came up
sudo systemctl status crowe-ide-router
sudo journalctl -u crowe-ide-router -f
```

**Verification:** `journalctl` should print `Session Router listening on :3001` within 2 seconds.

### 5e — Start the admin container

```bash
cd /opt/crowe-ide
sudo docker compose up -d
sudo docker compose ps
```

**Verification:** the `code-server-admin` container should be in `Up (healthy)` state.

---

## Phase 6 — End-to-end verification

From your local machine:

```bash
# 1. Health endpoint
curl -s https://ide.southwestmushrooms.com/health | jq
# Expected: {"status":"ok","timestamp":...}

# 2. Anonymous request (no token) — should redirect to ai.southwestmushrooms.com/auth
curl -sI https://ide.southwestmushrooms.com/
# Expected: HTTP/2 302, Location: https://ai.southwestmushrooms.com/auth?next=/ide

# 3. Generate a test token locally and try the handoff
#    Claims must match what the launcher in crowe-logic-ai sets:
#      iss=crowe-logic-ai, aud=crowe-ide-router, alg=HS256
node -e "
const { SignJWT } = require('jose');
const secret = new TextEncoder().encode(process.env.IDE_JWT_SECRET);
new SignJWT({ role: 'admin', email: 'test@example.com' })
  .setProtectedHeader({ alg: 'HS256' })
  .setSubject('test-user')
  .setIssuer('crowe-logic-ai')
  .setAudience('crowe-ide-router')
  .setIssuedAt()
  .setExpirationTime('60s')
  .sign(secret)
  .then(t => console.log('https://ide.southwestmushrooms.com/?token=' + t));
" | tee /tmp/ide-test-url.txt

# Open the URL in a browser (or curl -L it). You should land on a VS Code session.
```

**Production smoke test:**
1. Log into `ai.southwestmushrooms.com` with your real account
2. Navigate to `/ide`
3. Status badge should show **IDE Server: Online**
4. Click **Launch IDE** — a new tab opens at `ide.southwestmushrooms.com`
5. Within 2–3 seconds, VS Code (Dracula theme) loads with the foundry workspace mounted
6. Open a terminal (`Ctrl+`` `) — verify Python, git, and node are available

---

## Phase 7 — Wire up the launcher in Railway

In the Railway dashboard for the `crowe-logic-ai` service:

1. **Variables** tab → Add:
   ```
   NEXT_PUBLIC_IDE_URL=https://ide.southwestmushrooms.com
   IDE_JWT_SECRET=<same secret as on the VM, from Phase 0>
   ```
2. Click **Deploy** to trigger a rebuild with the new env vars.
3. Wait for the green checkmark, then open `ai.southwestmushrooms.com/ide` and run the production smoke test from Phase 6 again.

---

## Operations cheat sheet

### View Session Router logs
```bash
ssh crowelogic@$VM_IP "sudo journalctl -u crowe-ide-router -f"
```

### View running IDE containers
```bash
ssh crowelogic@$VM_IP "sudo docker ps --filter label=crowe-ide.user"
```

### Force-stop a stuck user container
```bash
ssh crowelogic@$VM_IP "sudo docker rm -f crowe-ide-<user-id>"
```

### Restart Session Router (e.g., after .env edit)
```bash
ssh crowelogic@$VM_IP "sudo systemctl restart crowe-ide-router"
```

### Renew TLS cert manually (certbot has a cron, but if it ever fails)
```bash
ssh crowelogic@$VM_IP "sudo certbot renew && sudo systemctl reload nginx"
```

### Pull updated foundry code into the admin container's workspace
```bash
ssh crowelogic@$VM_IP "sudo docker exec crowe-ide-admin git -C /workspace pull"
```

### Rotate the JWT secret
1. Generate new secret: `openssl rand -hex 32`
2. Update `/opt/crowe-ide/session-router/.env` on the VM
3. `sudo systemctl restart crowe-ide-router`
4. Update Railway env var, redeploy
5. Existing handoff URLs become invalid immediately — users must re-launch

---

## Teardown (if you ever need to delete everything)

```bash
# Delete the entire resource group — VM, disks, IP, NSG, all gone
az group delete --name crowe-logic-ide --yes --no-wait

# Remove the DNS A record at your registrar
# Remove NEXT_PUBLIC_IDE_URL and IDE_JWT_SECRET from Railway
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Browser shows `502 Bad Gateway` | Session Router not running | `sudo systemctl status crowe-ide-router`, check journal |
| `401 Unauthorized` on launch | `IDE_JWT_SECRET` mismatch between VM and Railway | Re-verify both env vars are byte-identical |
| `Container is starting up. Refresh in a few seconds.` (stays > 30s) | First-time image pull or container OOM | `docker logs crowe-ide-<user>`, check VM disk space with `df -h` |
| `certbot` fails with "DNS problem" | DNS not propagated yet | `dig +short ide.southwestmushrooms.com`; wait 5 min; retry |
| WebSocket disconnects every few seconds | Nginx `proxy_read_timeout` too low | Confirm `nginx/ide.conf` has `proxy_read_timeout 3600;` |
| `out of memory` errors in subscriber containers | 512MB too small for some workloads | Edit `containers.js` `Memory` field, restart router |
| DNS resolves but TLS handshake fails | Cloudflare proxy enabled | Turn proxy off (gray cloud) — Let's Encrypt needs direct origin |

For deeper diagnostics, see `deploy/ide/README.md`.
