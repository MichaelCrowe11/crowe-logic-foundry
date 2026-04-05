# Crowe Logic IDE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy code-server on an Azure VM with per-user Docker container isolation, accessible via `ide.southwestmushrooms.com`, with a launcher page on the existing crowe-logic-ai Next.js app.

**Architecture:** Azure VM runs Nginx + a custom Session Router + Docker containers (one per user). The Session Router validates Supabase JWTs, manages container lifecycle via dockerode, and proxies WebSocket/HTTP traffic. The crowe-logic-ai app gets a `/ide` launcher page and `/api/ide/launch` API route for auth handoff.

**Tech Stack:** code-server (VS Code for Web), Docker + Docker Compose, Nginx + Certbot, Node.js (Express + dockerode + http-proxy), Next.js 15 (existing app), Supabase Auth (JWT validation)

---

## File Structure

### On Azure VM (`/opt/crowe-ide/`)

| File | Responsibility |
|------|---------------|
| `Dockerfile.code-server` | Custom code-server image with Python deps + extensions |
| `docker-compose.yml` | Defines network, admin container profile, volume mounts |
| `settings.json` | VS Code settings injected into containers |
| `extensions.txt` | List of VS Code extensions to pre-install |
| `session-router/package.json` | Session Router dependencies |
| `session-router/index.js` | Main entry — Express server, auth, proxy |
| `session-router/auth.js` | JWT validation + Supabase user lookup |
| `session-router/containers.js` | Docker container lifecycle (create, start, stop, remove) |
| `session-router/proxy.js` | HTTP + WebSocket proxy to containers |
| `session-router/cleanup.js` | Idle timeout background job |
| `session-router/.env` | Supabase URL, JWT secret, config |
| `session-router/__tests__/auth.test.js` | Auth module tests |
| `session-router/__tests__/containers.test.js` | Container lifecycle tests |
| `session-router/__tests__/cleanup.test.js` | Cleanup logic tests |
| `nginx/ide.conf` | Nginx site config for ide.southwestmushrooms.com |
| `systemd/crowe-ide-router.service` | systemd unit for Session Router |
| `sandbox-template/` | Subscriber workspace starter files |

### On crowe-logic-ai (`/Users/crowelogic/Projects/crowe-logic-ai/`)

| File | Responsibility |
|------|---------------|
| `app/ide/page.tsx` | IDE launcher page (status, launch button) |
| `app/api/ide/launch/route.ts` | Generates short-lived JWT for auth handoff |
| `lib/ide-client.ts` | Helper to check VM/container status |

### On crowe-logic-foundry (this repo — config tracked in git)

| File | Responsibility |
|------|---------------|
| `deploy/ide/Dockerfile.code-server` | Tracked copy of the code-server Dockerfile |
| `deploy/ide/docker-compose.yml` | Tracked copy of Docker Compose config |
| `deploy/ide/nginx/ide.conf` | Tracked copy of Nginx config |
| `deploy/ide/session-router/` | Full Session Router source (developed + tested here) |
| `deploy/ide/sandbox-template/` | Subscriber workspace template |
| `deploy/ide/setup.sh` | VM bootstrap script (install Docker, Nginx, Certbot, deploy files) |
| `deploy/ide/README.md` | Deployment instructions |

---

## Task 1: VM Bootstrap Script

**Files:**
- Create: `deploy/ide/setup.sh`
- Create: `deploy/ide/README.md`

This script is run once on a fresh Ubuntu 24.04 Azure VM to install all prerequisites.

- [ ] **Step 1: Create the bootstrap script**

```bash
#!/usr/bin/env bash
# setup.sh — Bootstrap Azure VM for Crowe Logic IDE
# Run as root on a fresh Ubuntu 24.04 LTS VM:
#   curl -sSL <raw-url>/setup.sh | sudo bash

set -euo pipefail

echo "=== Crowe Logic IDE — VM Bootstrap ==="

# 1. System updates
apt-get update && apt-get upgrade -y

# 2. Install Docker Engine
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 3. Install Nginx
apt-get install -y nginx

# 4. Install Certbot
apt-get install -y certbot python3-certbot-nginx

# 5. Install Node.js 22 LTS
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get install -y nodejs

# 6. Create application directory
mkdir -p /opt/crowe-ide/session-router
mkdir -p /opt/crowe-ide/sandbox-template
mkdir -p /opt/crowe-ide/nginx

# 7. Create non-root service user
useradd -r -s /bin/false crowe-ide || true

# 8. Clone crowe-logic-foundry (for admin bind mount)
if [ ! -d /opt/crowe-logic-foundry ]; then
  git clone https://github.com/crowelogic/crowe-logic-foundry.git /opt/crowe-logic-foundry
fi

echo "=== Bootstrap complete ==="
echo "Next steps:"
echo "  1. Copy deploy/ide/* files to /opt/crowe-ide/"
echo "  2. cd /opt/crowe-ide/session-router && npm install"
echo "  3. Copy nginx/ide.conf to /etc/nginx/sites-available/ide"
echo "  4. ln -s /etc/nginx/sites-available/ide /etc/nginx/sites-enabled/"
echo "  5. certbot --nginx -d ide.southwestmushrooms.com"
echo "  6. systemctl enable --now crowe-ide-router"
echo "  7. docker compose up -d"
```

- [ ] **Step 2: Create the README**

```markdown
# Crowe Logic IDE — Deployment

## Prerequisites
- Azure VM: Standard B2s, Ubuntu 24.04 LTS
- DNS: A record for ide.southwestmushrooms.com pointing to VM public IP
- NSG: Allow ports 80, 443. SSH via Bastion or IP-locked rule.

## First-time Setup

1. SSH into the VM
2. Run bootstrap: `curl -sSL <raw-url>/setup.sh | sudo bash`
3. Copy deployment files: `scp -r deploy/ide/* user@vm:/opt/crowe-ide/`
4. Install Session Router deps: `cd /opt/crowe-ide/session-router && npm install`
5. Configure environment: `cp /opt/crowe-ide/session-router/.env.example /opt/crowe-ide/session-router/.env` and fill in values
6. Install Nginx config: `sudo cp /opt/crowe-ide/nginx/ide.conf /etc/nginx/sites-available/ide && sudo ln -s /etc/nginx/sites-available/ide /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx`
7. Get TLS cert: `sudo certbot --nginx -d ide.southwestmushrooms.com`
8. Install systemd service: `sudo cp /opt/crowe-ide/systemd/crowe-ide-router.service /etc/systemd/system/ && sudo systemctl enable --now crowe-ide-router`
9. Build and start containers: `cd /opt/crowe-ide && sudo docker compose build && sudo docker compose up -d`

## Updating

1. Pull latest: `cd /opt/crowe-logic-foundry && git pull`
2. Copy new files: `scp -r deploy/ide/* user@vm:/opt/crowe-ide/`
3. Rebuild image: `cd /opt/crowe-ide && sudo docker compose build`
4. Restart: `sudo systemctl restart crowe-ide-router && sudo docker compose up -d`
```

- [ ] **Step 3: Commit**

```bash
git add deploy/ide/setup.sh deploy/ide/README.md
git commit -m "infra: add VM bootstrap script and deployment docs for Crowe Logic IDE"
```

---

## Task 2: Custom code-server Docker Image

**Files:**
- Create: `deploy/ide/Dockerfile.code-server`
- Create: `deploy/ide/settings.json`
- Create: `deploy/ide/extensions.txt`

- [ ] **Step 1: Create the extensions list**

```
# deploy/ide/extensions.txt
# VS Code extensions to pre-install in code-server
ms-python.python
ms-python.debugpy
ms-toolsai.jupyter
eamodio.gitlens
esbenp.prettier-vscode
dracula-theme.theme-dracula
```

- [ ] **Step 2: Create the VS Code settings**

```json
{
  "workbench.colorTheme": "Dracula",
  "editor.fontSize": 14,
  "editor.tabSize": 4,
  "editor.formatOnSave": true,
  "editor.minimap.enabled": false,
  "terminal.integrated.defaultProfile.linux": "bash",
  "files.autoSave": "afterDelay",
  "files.autoSaveDelay": 1000,
  "python.defaultInterpreterPath": "/usr/bin/python3",
  "workbench.startupEditor": "readme"
}
```

- [ ] **Step 3: Create the Dockerfile**

```dockerfile
# deploy/ide/Dockerfile.code-server
FROM codercom/code-server:latest

USER root

# System dependencies for crowe-logic-foundry
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    git \
    ripgrep \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create Python venv (avoids PEP 668 externally-managed-environment error)
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install crowe-logic-foundry Python dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Install VS Code extensions
COPY extensions.txt /tmp/extensions.txt
RUN while IFS= read -r ext || [ -n "$ext" ]; do \
      ext=$(echo "$ext" | sed 's/#.*//;s/^[[:space:]]*//;s/[[:space:]]*$//'); \
      [ -z "$ext" ] && continue; \
      code-server --install-extension "$ext" || true; \
    done < /tmp/extensions.txt && rm /tmp/extensions.txt

# Default VS Code settings
COPY settings.json /home/coder/.local/share/code-server/User/settings.json

# Fix ownership
RUN chown -R coder:coder /home/coder/.local

USER coder

WORKDIR /workspace
```

- [ ] **Step 4: Test the image builds locally**

Run (on a machine with Docker):
```bash
cd deploy/ide
# Copy requirements.txt from project root for build context
cp ../../requirements.txt .
docker build -f Dockerfile.code-server -t crowe-ide-codeserver .
rm requirements.txt
```

Expected: Image builds successfully, no errors.

- [ ] **Step 5: Verify code-server starts**

```bash
docker run --rm -p 8080:8080 crowe-ide-codeserver \
  --bind-addr 0.0.0.0:8080 --auth none
```

Open `http://localhost:8080` in a browser. Expected: VS Code UI loads with Dracula theme, Python extension installed.

Stop the container with Ctrl+C.

- [ ] **Step 6: Commit**

```bash
git add deploy/ide/Dockerfile.code-server deploy/ide/settings.json deploy/ide/extensions.txt
git commit -m "feat: add custom code-server Docker image with Python deps and extensions"
```

---

## Task 3: Docker Compose Configuration

**Files:**
- Create: `deploy/ide/docker-compose.yml`

- [ ] **Step 1: Create Docker Compose config**

```yaml
# deploy/ide/docker-compose.yml
# Defines the base code-server image build and the admin container.
# Subscriber containers are created dynamically by the Session Router.

services:
  admin:
    build:
      context: .
      dockerfile: Dockerfile.code-server
      args:
        - REQUIREMENTS_FILE=requirements.txt
    container_name: crowe-ide-admin
    restart: unless-stopped
    ports:
      - "10000:8080"
    volumes:
      - /opt/crowe-logic-foundry:/workspace/crowe-logic-foundry
      - admin-data:/home/coder/.local
    environment:
      - DOCKER_USER=coder
    command:
      - --bind-addr=0.0.0.0:8080
      - --auth=none
      - --disable-telemetry
    cpus: 2.0
    mem_limit: 2g
    networks:
      - ide-admin

networks:
  ide-admin:
    driver: bridge
  ide-subscribers:
    driver: bridge
    internal: true  # No external network access for subscriber containers

volumes:
  admin-data:
```

- [ ] **Step 2: Copy requirements.txt into deploy context and test**

```bash
cd deploy/ide
cp ../../requirements.txt .
docker compose build
docker compose up -d admin
```

Expected: Admin container starts, accessible at `http://localhost:10000`.
Verify: `docker compose ps` shows admin container running.

```bash
docker compose down
rm requirements.txt
```

- [ ] **Step 3: Commit**

```bash
git add deploy/ide/docker-compose.yml
git commit -m "feat: add Docker Compose config with admin container and isolated networks"
```

---

## Task 4: Nginx Configuration

**Files:**
- Create: `deploy/ide/nginx/ide.conf`

- [ ] **Step 1: Create the Nginx config**

```nginx
# deploy/ide/nginx/ide.conf
# Reverse proxy for ide.southwestmushrooms.com -> Session Router

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name ide.southwestmushrooms.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name ide.southwestmushrooms.com;

    # TLS — managed by Certbot (paths filled in by certbot --nginx)
    # ssl_certificate /etc/letsencrypt/live/ide.southwestmushrooms.com/fullchain.pem;
    # ssl_certificate_key /etc/letsencrypt/live/ide.southwestmushrooms.com/privkey.pem;

    # Proxy to Session Router
    location / {
        proxy_pass http://127.0.0.1:3001;
        proxy_http_version 1.1;

        # WebSocket support (critical for code-server)
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Forward client info
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Long timeouts for WebSocket connections
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;

        # Disable buffering for streaming
        proxy_buffering off;
    }
}
```

- [ ] **Step 2: Validate Nginx config syntax**

On the VM (or locally if nginx is installed):
```bash
sudo nginx -t -c /dev/stdin <<< "events {} http { include /opt/crowe-ide/nginx/ide.conf; }"
```

Expected: `syntax is ok` (may warn about missing SSL certs — that is fine before Certbot runs).

- [ ] **Step 3: Commit**

```bash
git add deploy/ide/nginx/ide.conf
git commit -m "feat: add Nginx reverse proxy config with WebSocket support for IDE"
```

---

## Task 5: Session Router — Auth Module

The Session Router is the core custom code. We build it module by module with TDD. All Session Router code lives in `deploy/ide/session-router/`.

**Files:**
- Create: `deploy/ide/session-router/package.json`
- Create: `deploy/ide/session-router/auth.js`
- Create: `deploy/ide/session-router/__tests__/auth.test.js`

- [ ] **Step 1: Initialize the Node.js project**

```json
{
  "name": "crowe-ide-session-router",
  "version": "1.0.0",
  "private": true,
  "description": "Session Router for Crowe Logic IDE — JWT auth, container lifecycle, WebSocket proxy",
  "main": "index.js",
  "scripts": {
    "start": "node index.js",
    "test": "jest --verbose"
  },
  "dependencies": {
    "cookie": "^1.0.0",
    "dockerode": "^4.0.0",
    "express": "^5.1.0",
    "http-proxy": "^1.18.0",
    "jose": "^6.0.0"
  },
  "devDependencies": {
    "jest": "^29.7.0"
  }
}
```

- [ ] **Step 2: Write the failing tests for auth module**

```javascript
// deploy/ide/session-router/__tests__/auth.test.js
const { createAuthModule } = require('../auth');
const { SignJWT, exportJWK, generateKeyPair } = require('jose');

let keyPair;
let jwkPublic;
let auth;

beforeAll(async () => {
  keyPair = await generateKeyPair('RS256');
  jwkPublic = await exportJWK(keyPair.publicKey);
  jwkPublic.kid = 'test-key-id';
  jwkPublic.alg = 'RS256';
  jwkPublic.use = 'sig';
});

beforeEach(() => {
  const mockFetchJWKS = async () => ({ keys: [jwkPublic] });
  auth = createAuthModule({
    supabaseUrl: 'https://test.supabase.co',
    fetchJWKS: mockFetchJWKS,
  });
});

async function makeJWT(claims = {}, expiresIn = '60s') {
  return new SignJWT({ sub: 'user-123', role: 'admin', ...claims })
    .setProtectedHeader({ alg: 'RS256', kid: 'test-key-id' })
    .setIssuer('https://test.supabase.co/auth/v1')
    .setAudience('authenticated')
    .setExpirationTime(expiresIn)
    .setIssuedAt()
    .sign(keyPair.privateKey);
}

describe('validateToken', () => {
  test('returns user data for valid token', async () => {
    const token = await makeJWT({ sub: 'user-123', role: 'admin' });
    const result = await auth.validateToken(token);
    expect(result).toEqual({
      userId: 'user-123',
      role: 'admin',
    });
  });

  test('rejects expired token', async () => {
    const token = await makeJWT({}, '-1s');
    await expect(auth.validateToken(token)).rejects.toThrow();
  });

  test('rejects token with wrong issuer', async () => {
    const token = await new SignJWT({ sub: 'user-123', role: 'admin' })
      .setProtectedHeader({ alg: 'RS256', kid: 'test-key-id' })
      .setIssuer('https://wrong.supabase.co/auth/v1')
      .setAudience('authenticated')
      .setExpirationTime('60s')
      .setIssuedAt()
      .sign(keyPair.privateKey);
    await expect(auth.validateToken(token)).rejects.toThrow();
  });

  test('rejects malformed token string', async () => {
    await expect(auth.validateToken('not-a-jwt')).rejects.toThrow();
  });

  test('rejects empty string', async () => {
    await expect(auth.validateToken('')).rejects.toThrow();
  });
});

describe('extractToken', () => {
  test('extracts token from query parameter', () => {
    const req = { query: { token: 'abc123' }, cookies: {} };
    expect(auth.extractToken(req)).toBe('abc123');
  });

  test('extracts token from cookie', () => {
    const req = { query: {}, cookies: { 'crowe-ide-session': 'def456' } };
    expect(auth.extractToken(req)).toBe('def456');
  });

  test('prefers query token over cookie', () => {
    const req = { query: { token: 'from-query' }, cookies: { 'crowe-ide-session': 'from-cookie' } };
    expect(auth.extractToken(req)).toBe('from-query');
  });

  test('returns null when no token present', () => {
    const req = { query: {}, cookies: {} };
    expect(auth.extractToken(req)).toBeNull();
  });
});
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd deploy/ide/session-router
npm install
npx jest --verbose
```

Expected: FAIL — `Cannot find module '../auth'`

- [ ] **Step 4: Implement the auth module**

```javascript
// deploy/ide/session-router/auth.js
const { createRemoteJWKSet, jwtVerify } = require('jose');

const COOKIE_NAME = 'crowe-ide-session';

function createAuthModule({ supabaseUrl, fetchJWKS }) {
  const issuer = `${supabaseUrl}/auth/v1`;
  const audience = 'authenticated';

  let jwks;
  if (fetchJWKS) {
    // Test mode: use provided JWKS fetcher
    jwks = {
      async resolve(protectedHeader) {
        const { keys } = await fetchJWKS();
        const key = keys.find((k) => k.kid === protectedHeader.kid);
        if (!key) throw new Error('Key not found');
        const { importJWK } = require('jose');
        return importJWK(key, protectedHeader.alg);
      },
    };
  } else {
    // Production: fetch JWKS from Supabase
    jwks = createRemoteJWKSet(
      new URL(`${supabaseUrl}/auth/v1/.well-known/jwks.json`)
    );
  }

  async function validateToken(token) {
    if (!token) throw new Error('No token provided');
    const { payload } = await jwtVerify(token, jwks, {
      issuer,
      audience,
    });
    return {
      userId: payload.sub,
      role: payload.role || 'subscriber',
    };
  }

  function extractToken(req) {
    if (req.query && req.query.token) return req.query.token;
    if (req.cookies && req.cookies[COOKIE_NAME]) return req.cookies[COOKIE_NAME];
    return null;
  }

  return { validateToken, extractToken, COOKIE_NAME };
}

module.exports = { createAuthModule, COOKIE_NAME };
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd deploy/ide/session-router
npx jest --verbose
```

Expected: All 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add deploy/ide/session-router/package.json deploy/ide/session-router/auth.js deploy/ide/session-router/__tests__/auth.test.js
git commit -m "feat: add Session Router auth module with JWT validation and cookie handling"
```

---

## Task 6: Session Router — Container Lifecycle Module

**Files:**
- Create: `deploy/ide/session-router/containers.js`
- Create: `deploy/ide/session-router/__tests__/containers.test.js`

- [ ] **Step 1: Write the failing tests**

```javascript
// deploy/ide/session-router/__tests__/containers.test.js
const { createContainerManager } = require('../containers');

function createMockDocker() {
  const containers = new Map();
  let nextPort = 10001;

  return {
    containers,
    listContainers: jest.fn(async (opts) => {
      return Array.from(containers.values())
        .filter((c) => {
          if (!opts || !opts.filters) return true;
          const labelFilter = JSON.parse(opts.filters).label || [];
          return labelFilter.every((l) => {
            const [key, val] = l.split('=');
            return c.Labels && c.Labels[key] === val;
          });
        })
        .map((c) => ({
          Id: c.Id,
          State: c.State,
          Labels: c.Labels,
          Ports: c.Ports,
        }));
    }),
    createContainer: jest.fn(async (opts) => {
      const id = `container-${containers.size + 1}`;
      const port = nextPort++;
      const container = {
        Id: id,
        State: 'created',
        Labels: opts.Labels || {},
        Ports: [{ PublicPort: port }],
        _hostPort: port,
      };
      containers.set(id, container);
      return {
        id,
        start: jest.fn(async () => {
          container.State = 'running';
        }),
        inspect: jest.fn(async () => ({
          State: { Running: container.State === 'running' },
          NetworkSettings: {
            Ports: {
              '8080/tcp': [{ HostPort: String(port) }],
            },
          },
        })),
      };
    }),
    getContainer: jest.fn((id) => {
      const container = containers.get(id);
      return {
        id,
        start: jest.fn(async () => {
          if (container) container.State = 'running';
        }),
        stop: jest.fn(async () => {
          if (container) container.State = 'exited';
        }),
        remove: jest.fn(async () => {
          containers.delete(id);
        }),
        inspect: jest.fn(async () => ({
          State: { Running: container ? container.State === 'running' : false },
          NetworkSettings: {
            Ports: {
              '8080/tcp': [{ HostPort: String(container?._hostPort || 10001) }],
            },
          },
        })),
      };
    }),
  };
}

describe('getOrCreateContainer', () => {
  test('creates a new admin container when none exists', async () => {
    const docker = createMockDocker();
    const mgr = createContainerManager({ docker, imageName: 'crowe-ide-codeserver' });
    const result = await mgr.getOrCreateContainer('user-admin', 'admin');
    expect(result.containerId).toBe('container-1');
    expect(result.port).toBeDefined();
    expect(docker.createContainer).toHaveBeenCalled();
    const createOpts = docker.createContainer.mock.calls[0][0];
    expect(createOpts.Labels['crowe-ide.user-id']).toBe('user-admin');
    expect(createOpts.Labels['crowe-ide.role']).toBe('admin');
  });

  test('creates subscriber container with resource limits', async () => {
    const docker = createMockDocker();
    const mgr = createContainerManager({ docker, imageName: 'crowe-ide-codeserver' });
    const result = await mgr.getOrCreateContainer('user-sub', 'subscriber');
    expect(result.containerId).toBe('container-1');
    const createOpts = docker.createContainer.mock.calls[0][0];
    expect(createOpts.HostConfig.NanoCpus).toBe(500000000); // 0.5 CPU
    expect(createOpts.HostConfig.Memory).toBe(512 * 1024 * 1024); // 512 MB
    expect(createOpts.Labels['crowe-ide.role']).toBe('subscriber');
  });

  test('returns existing container if already running', async () => {
    const docker = createMockDocker();
    const mgr = createContainerManager({ docker, imageName: 'crowe-ide-codeserver' });
    const first = await mgr.getOrCreateContainer('user-1', 'admin');
    // Simulate the container showing up in list
    docker.listContainers.mockResolvedValueOnce([{
      Id: first.containerId,
      State: 'running',
      Labels: { 'crowe-ide.user-id': 'user-1', 'crowe-ide.role': 'admin' },
      Ports: [{ PublicPort: first.port }],
    }]);
    const second = await mgr.getOrCreateContainer('user-1', 'admin');
    expect(second.containerId).toBe(first.containerId);
    expect(docker.createContainer).toHaveBeenCalledTimes(1); // Not called again
  });
});

describe('stopContainer', () => {
  test('stops a running container', async () => {
    const docker = createMockDocker();
    const mgr = createContainerManager({ docker, imageName: 'crowe-ide-codeserver' });
    const { containerId } = await mgr.getOrCreateContainer('user-1', 'subscriber');
    await mgr.stopContainer(containerId);
    const mock = docker.getContainer(containerId);
    expect(mock.stop).toHaveBeenCalled();
  });
});

describe('removeContainer', () => {
  test('removes a stopped container', async () => {
    const docker = createMockDocker();
    const mgr = createContainerManager({ docker, imageName: 'crowe-ide-codeserver' });
    const { containerId } = await mgr.getOrCreateContainer('user-1', 'subscriber');
    await mgr.removeContainer(containerId);
    const mock = docker.getContainer(containerId);
    expect(mock.remove).toHaveBeenCalled();
  });
});

describe('port allocation', () => {
  test('assigns unique ports to different users', async () => {
    const docker = createMockDocker();
    const mgr = createContainerManager({ docker, imageName: 'crowe-ide-codeserver' });
    const a = await mgr.getOrCreateContainer('user-a', 'subscriber');
    const b = await mgr.getOrCreateContainer('user-b', 'subscriber');
    expect(a.port).not.toBe(b.port);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd deploy/ide/session-router
npx jest __tests__/containers.test.js --verbose
```

Expected: FAIL — `Cannot find module '../containers'`

- [ ] **Step 3: Implement the container lifecycle module**

```javascript
// deploy/ide/session-router/containers.js

const LABEL_PREFIX = 'crowe-ide';
const BASE_PORT = 10001; // 10000 reserved for admin in docker-compose
const MAX_PORT = 10100;

const PROFILES = {
  admin: {
    nanoCpus: 2 * 1e9,      // 2 CPU
    memory: 2 * 1024 * 1024 * 1024, // 2 GB
    network: 'ide-admin',
  },
  subscriber: {
    nanoCpus: 0.5 * 1e9,    // 0.5 CPU
    memory: 512 * 1024 * 1024,      // 512 MB
    network: 'ide-subscribers',
  },
};

function createContainerManager({ docker, imageName }) {
  const allocatedPorts = new Set();

  function nextPort() {
    for (let p = BASE_PORT; p <= MAX_PORT; p++) {
      if (!allocatedPorts.has(p)) {
        allocatedPorts.add(p);
        return p;
      }
    }
    throw new Error('No available ports in range');
  }

  async function findExisting(userId) {
    const containers = await docker.listContainers({
      all: true,
      filters: JSON.stringify({
        label: [`${LABEL_PREFIX}.user-id=${userId}`],
      }),
    });
    if (containers.length === 0) return null;
    const c = containers[0];
    const port = c.Ports?.[0]?.PublicPort;
    if (port) allocatedPorts.add(port);
    return { containerId: c.Id, port, state: c.State };
  }

  async function getOrCreateContainer(userId, role) {
    const existing = await findExisting(userId);
    if (existing && existing.state === 'running') {
      return { containerId: existing.containerId, port: existing.port };
    }
    if (existing && existing.state !== 'running') {
      const container = docker.getContainer(existing.containerId);
      await container.start();
      return { containerId: existing.containerId, port: existing.port };
    }

    const profile = PROFILES[role] || PROFILES.subscriber;
    const port = nextPort();

    const container = await docker.createContainer({
      Image: imageName,
      Labels: {
        [`${LABEL_PREFIX}.user-id`]: userId,
        [`${LABEL_PREFIX}.role`]: role,
      },
      Cmd: [
        '--bind-addr=0.0.0.0:8080',
        '--auth=none',
        '--disable-telemetry',
      ],
      HostConfig: {
        PortBindings: {
          '8080/tcp': [{ HostPort: String(port) }],
        },
        NanoCpus: profile.nanoCpus,
        Memory: profile.memory,
        NetworkMode: profile.network,
      },
      ExposedPorts: {
        '8080/tcp': {},
      },
    });

    await container.start();
    return { containerId: container.id, port };
  }

  async function stopContainer(containerId) {
    const container = docker.getContainer(containerId);
    await container.stop();
  }

  async function removeContainer(containerId) {
    const container = docker.getContainer(containerId);
    await container.remove();
  }

  async function getContainerPort(containerId) {
    const container = docker.getContainer(containerId);
    const info = await container.inspect();
    const bindings = info.NetworkSettings.Ports['8080/tcp'];
    if (bindings && bindings.length > 0) {
      return parseInt(bindings[0].HostPort, 10);
    }
    return null;
  }

  return {
    getOrCreateContainer,
    stopContainer,
    removeContainer,
    getContainerPort,
    findExisting,
  };
}

module.exports = { createContainerManager, PROFILES, LABEL_PREFIX };
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd deploy/ide/session-router
npx jest __tests__/containers.test.js --verbose
```

Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add deploy/ide/session-router/containers.js deploy/ide/session-router/__tests__/containers.test.js
git commit -m "feat: add container lifecycle module with per-user isolation and resource limits"
```

---

## Task 7: Session Router — Cleanup Module

**Files:**
- Create: `deploy/ide/session-router/cleanup.js`
- Create: `deploy/ide/session-router/__tests__/cleanup.test.js`

- [ ] **Step 1: Write the failing tests**

```javascript
// deploy/ide/session-router/__tests__/cleanup.test.js
const { createCleanupJob } = require('../cleanup');

function createMockContainerManager() {
  const containers = [];
  return {
    containers,
    addContainer(id, role, lastActivity) {
      containers.push({ id, role, lastActivity });
    },
    listAll: jest.fn(async () =>
      containers.map((c) => ({
        containerId: c.id,
        role: c.role,
        lastActivity: c.lastActivity,
        state: 'running',
      }))
    ),
    stopContainer: jest.fn(async () => {}),
    removeContainer: jest.fn(async () => {}),
  };
}

describe('cleanup', () => {
  test('stops subscriber containers idle for 30+ minutes', async () => {
    const mgr = createMockContainerManager();
    const now = Date.now();
    mgr.addContainer('c1', 'subscriber', now - 31 * 60 * 1000); // 31 min ago
    mgr.addContainer('c2', 'subscriber', now - 10 * 60 * 1000); // 10 min ago

    const cleanup = createCleanupJob({
      containerManager: mgr,
      idleStopMinutes: 30,
      idleRemoveHours: 24,
      getNow: () => now,
    });

    await cleanup.runOnce();
    expect(mgr.stopContainer).toHaveBeenCalledWith('c1');
    expect(mgr.stopContainer).not.toHaveBeenCalledWith('c2');
  });

  test('removes subscriber containers stopped for 24+ hours', async () => {
    const mgr = createMockContainerManager();
    const now = Date.now();
    mgr.addContainer('c1', 'subscriber', now - 25 * 60 * 60 * 1000); // 25 hours ago

    mgr.listAll.mockResolvedValueOnce([{
      containerId: 'c1',
      role: 'subscriber',
      lastActivity: now - 25 * 60 * 60 * 1000,
      state: 'exited',
    }]);

    const cleanup = createCleanupJob({
      containerManager: mgr,
      idleStopMinutes: 30,
      idleRemoveHours: 24,
      getNow: () => now,
    });

    await cleanup.runOnce();
    expect(mgr.removeContainer).toHaveBeenCalledWith('c1');
  });

  test('never stops admin containers', async () => {
    const mgr = createMockContainerManager();
    const now = Date.now();
    mgr.addContainer('admin-c', 'admin', now - 999 * 60 * 1000); // Very old

    const cleanup = createCleanupJob({
      containerManager: mgr,
      idleStopMinutes: 30,
      idleRemoveHours: 24,
      getNow: () => now,
    });

    await cleanup.runOnce();
    expect(mgr.stopContainer).not.toHaveBeenCalled();
    expect(mgr.removeContainer).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd deploy/ide/session-router
npx jest __tests__/cleanup.test.js --verbose
```

Expected: FAIL — `Cannot find module '../cleanup'`

- [ ] **Step 3: Implement the cleanup module**

```javascript
// deploy/ide/session-router/cleanup.js

function createCleanupJob({
  containerManager,
  idleStopMinutes = 30,
  idleRemoveHours = 24,
  intervalMinutes = 5,
  getNow = () => Date.now(),
}) {
  const idleStopMs = idleStopMinutes * 60 * 1000;
  const idleRemoveMs = idleRemoveHours * 60 * 60 * 1000;

  async function runOnce() {
    const containers = await containerManager.listAll();

    for (const c of containers) {
      // Never touch admin containers
      if (c.role === 'admin') continue;

      const idleMs = getNow() - c.lastActivity;

      if (c.state === 'running' && idleMs >= idleStopMs) {
        console.log(`[cleanup] Stopping idle container ${c.containerId} (idle ${Math.round(idleMs / 60000)}m)`);
        await containerManager.stopContainer(c.containerId);
      } else if (c.state === 'exited' && idleMs >= idleRemoveMs) {
        console.log(`[cleanup] Removing stale container ${c.containerId} (idle ${Math.round(idleMs / 3600000)}h)`);
        await containerManager.removeContainer(c.containerId);
      }
    }
  }

  let timer = null;

  function start() {
    timer = setInterval(runOnce, intervalMinutes * 60 * 1000);
    console.log(`[cleanup] Running every ${intervalMinutes}m (stop after ${idleStopMinutes}m, remove after ${idleRemoveHours}h)`);
  }

  function stop() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  return { runOnce, start, stop };
}

module.exports = { createCleanupJob };
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd deploy/ide/session-router
npx jest __tests__/cleanup.test.js --verbose
```

Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add deploy/ide/session-router/cleanup.js deploy/ide/session-router/__tests__/cleanup.test.js
git commit -m "feat: add idle container cleanup job (30m stop, 24h remove, skip admin)"
```

---

## Task 8: Session Router — Proxy and Main Entry Point

**Files:**
- Create: `deploy/ide/session-router/proxy.js`
- Create: `deploy/ide/session-router/index.js`
- Create: `deploy/ide/session-router/.env.example`

- [ ] **Step 1: Create the proxy module**

```javascript
// deploy/ide/session-router/proxy.js
const httpProxy = require('http-proxy');

function createProxyServer() {
  const proxy = httpProxy.createProxyServer({
    ws: true,
    changeOrigin: true,
    xfwd: true,
  });

  proxy.on('error', (err, req, res) => {
    console.error(`[proxy] Error: ${err.message}`);
    if (res && res.writeHead) {
      res.writeHead(502, { 'Content-Type': 'text/plain' });
      res.end('IDE container is starting up. Refresh in a few seconds.');
    }
  });

  function proxyRequest(req, res, port) {
    proxy.web(req, res, { target: `http://127.0.0.1:${port}` });
  }

  function proxyWebSocket(req, socket, head, port) {
    proxy.ws(req, socket, head, { target: `http://127.0.0.1:${port}` });
  }

  return { proxyRequest, proxyWebSocket };
}

module.exports = { createProxyServer };
```

- [ ] **Step 2: Create the .env.example**

```bash
# deploy/ide/session-router/.env.example
# Supabase project URL (e.g., https://abcdefgh.supabase.co)
SUPABASE_URL=

# Port for the Session Router to listen on
PORT=3001

# Docker image name for code-server containers
IMAGE_NAME=crowe-ide-codeserver

# Idle timeout settings
IDLE_STOP_MINUTES=30
IDLE_REMOVE_HOURS=24
```

- [ ] **Step 3: Create the main entry point**

```javascript
// deploy/ide/session-router/index.js
const express = require('express');
const http = require('http');
const cookie = require('cookie');
const { createAuthModule } = require('./auth');
const { createContainerManager } = require('./containers');
const { createCleanupJob } = require('./cleanup');
const { createProxyServer } = require('./proxy');

// Load env from .env file if present
try { require('dotenv').config(); } catch (_) { /* dotenv is optional */ }

const PORT = parseInt(process.env.PORT || '3001', 10);
const SUPABASE_URL = process.env.SUPABASE_URL;
const IMAGE_NAME = process.env.IMAGE_NAME || 'crowe-ide-codeserver';

if (!SUPABASE_URL) {
  console.error('SUPABASE_URL is required');
  process.exit(1);
}

// Initialize modules
const Docker = require('dockerode');
const docker = new Docker();

const auth = createAuthModule({ supabaseUrl: SUPABASE_URL });
const containerMgr = createContainerManager({ docker, imageName: IMAGE_NAME });
const proxy = createProxyServer();

// Cleanup job
const cleanup = createCleanupJob({
  containerManager: {
    async listAll() {
      const containers = await docker.listContainers({
        all: true,
        filters: JSON.stringify({ label: ['crowe-ide.user-id'] }),
      });
      return containers.map((c) => ({
        containerId: c.Id,
        role: c.Labels['crowe-ide.role'] || 'subscriber',
        lastActivity: c.Created * 1000,
        state: c.State,
      }));
    },
    stopContainer: (id) => containerMgr.stopContainer(id),
    removeContainer: (id) => containerMgr.removeContainer(id),
  },
  idleStopMinutes: parseInt(process.env.IDLE_STOP_MINUTES || '30', 10),
  idleRemoveHours: parseInt(process.env.IDLE_REMOVE_HOURS || '24', 10),
});

// Express app
const app = express();

// Parse cookies on every request
app.use((req, _res, next) => {
  req.cookies = cookie.parse(req.headers.cookie || '');
  next();
});

// Health check (no auth)
app.get('/health', (_req, res) => {
  res.json({ status: 'ok', timestamp: Date.now() });
});

// All other requests: authenticate then proxy
app.use(async (req, res) => {
  try {
    const token = auth.extractToken(req);
    if (!token) {
      res.status(401).json({ error: 'Authentication required' });
      return;
    }

    const user = await auth.validateToken(token);

    // If token came from query param, set cookie and redirect (strips token from URL)
    if (req.query.token) {
      res.setHeader('Set-Cookie', cookie.serialize(auth.COOKIE_NAME, token, {
        httpOnly: true,
        secure: true,
        sameSite: 'strict',
        domain: 'ide.southwestmushrooms.com',
        path: '/',
        maxAge: 60 * 60 * 24, // 24 hours
      }));
      res.redirect(302, '/');
      return;
    }

    // Get or create container
    const { port } = await containerMgr.getOrCreateContainer(user.userId, user.role);

    // Proxy the request
    proxy.proxyRequest(req, res, port);
  } catch (err) {
    console.error(`[router] Auth error: ${err.message}`);
    res.status(401).json({ error: 'Invalid or expired session' });
  }
});

// Create HTTP server for WebSocket support
const server = http.createServer(app);

// Handle WebSocket upgrades
server.on('upgrade', async (req, socket, head) => {
  try {
    const cookies = cookie.parse(req.headers.cookie || '');
    const token = cookies[auth.COOKIE_NAME];
    if (!token) {
      socket.destroy();
      return;
    }

    const user = await auth.validateToken(token);
    const { port } = await containerMgr.getOrCreateContainer(user.userId, user.role);
    proxy.proxyWebSocket(req, socket, head, port);
  } catch (err) {
    console.error(`[router] WebSocket auth error: ${err.message}`);
    socket.destroy();
  }
});

// Start
server.listen(PORT, () => {
  console.log(`[router] Crowe Logic IDE Session Router listening on port ${PORT}`);
  cleanup.start();
});

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('[router] Shutting down...');
  cleanup.stop();
  server.close(() => process.exit(0));
});
```

- [ ] **Step 4: Commit**

```bash
git add deploy/ide/session-router/proxy.js deploy/ide/session-router/index.js deploy/ide/session-router/.env.example
git commit -m "feat: add Session Router entry point with auth, proxy, and WebSocket support"
```

---

## Task 9: systemd Service Unit

**Files:**
- Create: `deploy/ide/systemd/crowe-ide-router.service`

- [ ] **Step 1: Create the systemd service**

```ini
# deploy/ide/systemd/crowe-ide-router.service
[Unit]
Description=Crowe Logic IDE Session Router
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=crowe-ide
WorkingDirectory=/opt/crowe-ide/session-router
ExecStart=/usr/bin/node /opt/crowe-ide/session-router/index.js
Restart=always
RestartSec=5
Environment=NODE_ENV=production

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/crowe-ide/session-router

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=crowe-ide-router

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Commit**

```bash
git add deploy/ide/systemd/crowe-ide-router.service
git commit -m "feat: add systemd service unit for Session Router"
```

---

## Task 10: Subscriber Sandbox Template

**Files:**
- Create: `deploy/ide/sandbox-template/README.md`
- Create: `deploy/ide/sandbox-template/examples/hello_foundry.py`
- Create: `deploy/ide/sandbox-template/examples/explore_tools.py`
- Create: `deploy/ide/sandbox-template/examples/crowelm_pipeline.py`

- [ ] **Step 1: Create the sandbox README**

```markdown
# Crowe Logic Foundry — Sandbox

Welcome to your Crowe Logic IDE workspace.

## What's here

- `examples/` — Working scripts that demonstrate foundry tools
- `scratch/` — Your personal scratch area (write anything here)

## Quick Start

Open a terminal (Ctrl+`) and run:

    python examples/hello_foundry.py

## Available Tools

The Crowe Logic Foundry provides:
- **CroweLM Pipeline** — Stage, evaluate, and curate training data
- **Search** — Web search and file grep
- **Shell** — Execute shell commands
- **Filesystem** — Read, write, and list files

Explore `examples/` to see each tool in action.
```

- [ ] **Step 2: Create the example scripts**

```python
# deploy/ide/sandbox-template/examples/hello_foundry.py
"""
Hello Foundry — your first script in the Crowe Logic IDE.

This demonstrates using the filesystem tools to read and write files.
"""


def main():
    print("Welcome to Crowe Logic Foundry!")
    print()
    print("This sandbox is your personal workspace.")
    print("Files you create in /workspace/sandbox/scratch/ are yours to keep.")
    print()
    print("Try editing this file and running it again!")


if __name__ == "__main__":
    main()
```

```python
# deploy/ide/sandbox-template/examples/explore_tools.py
"""
Explore Tools — list all available tools in the Crowe Logic Foundry.
"""
import importlib
import json


def main():
    try:
        tools_init = importlib.import_module("tools")
        user_functions = getattr(tools_init, "user_functions", set())
        print(f"Crowe Logic Foundry has {len(user_functions)} tools:\n")
        for fn in sorted(user_functions, key=lambda f: f.__name__):
            doc = (fn.__doc__ or "").strip().split("\n")[0]
            print(f"  {fn.__name__:40s} {doc}")
    except ImportError:
        print("Tools module not available in sandbox mode.")
        print("This is a read-only demo. Full access requires admin privileges.")


if __name__ == "__main__":
    main()
```

```python
# deploy/ide/sandbox-template/examples/crowelm_pipeline.py
"""
CroweLM Pipeline Demo — shows the staging pipeline flow.

This is a read-only demonstration. Sandbox users cannot write
to the production staging directory.
"""
import json


def main():
    print("=== CroweLM Staging Pipeline ===\n")
    print("The pipeline processes training data through 4 stages:\n")

    stages = [
        ("pending",  "New items awaiting evaluation"),
        ("approved", "Score >= 0.85 — auto-approved for training"),
        ("review",   "Score 0.50-0.84 — needs human review"),
        ("rejected", "Score < 0.50 — filtered out"),
    ]

    for stage, desc in stages:
        print(f"  {stage:12s} {desc}")

    print()
    print("Example item flowing through the pipeline:\n")

    example = {
        "instruction": "How do I grow shiitake mushrooms?",
        "response": "Use hardwood sawdust blocks supplemented with wheat bran...",
        "category": "mycology",
        "confidence": 0.92,
    }
    print(json.dumps(example, indent=2))
    print()
    print("This item would score >= 0.85 and be auto-approved.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create the scratch directory placeholder**

```bash
mkdir -p deploy/ide/sandbox-template/scratch
echo "# Your scratch area — experiment freely here" > deploy/ide/sandbox-template/scratch/.gitkeep
```

- [ ] **Step 4: Commit**

```bash
git add deploy/ide/sandbox-template/
git commit -m "feat: add subscriber sandbox template with example scripts and scratch area"
```

---

## Task 11: Launcher Page (crowe-logic-ai)

This task is in the **crowe-logic-ai** repo, not crowe-logic-foundry.

**Files:**
- Create: `/Users/crowelogic/Projects/crowe-logic-ai/app/ide/page.tsx`
- Create: `/Users/crowelogic/Projects/crowe-logic-ai/app/api/ide/launch/route.ts`
- Create: `/Users/crowelogic/Projects/crowe-logic-ai/lib/ide-client.ts`

- [ ] **Step 1: Create the IDE status helper**

```typescript
// lib/ide-client.ts

const IDE_HOST = process.env.NEXT_PUBLIC_IDE_URL || 'https://ide.southwestmushrooms.com';

export interface IdeStatus {
  online: boolean;
  timestamp?: number;
}

export async function checkIdeStatus(): Promise<IdeStatus> {
  try {
    const res = await fetch(`${IDE_HOST}/health`, {
      next: { revalidate: 30 },
      signal: AbortSignal.timeout(5000),
    });
    if (res.ok) {
      const data = await res.json();
      return { online: true, timestamp: data.timestamp };
    }
    return { online: false };
  } catch {
    return { online: false };
  }
}
```

- [ ] **Step 2: Create the launch API route**

```typescript
// app/api/ide/launch/route.ts
import { NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase/server';
import { SignJWT } from 'jose';

const IDE_URL = process.env.NEXT_PUBLIC_IDE_URL || 'https://ide.southwestmushrooms.com';
const IDE_JWT_SECRET = process.env.IDE_JWT_SECRET;

export async function POST() {
  try {
    const supabase = await createClient();
    const { data: { user }, error } = await supabase.auth.getUser();

    if (error || !user) {
      return NextResponse.json({ error: 'Not authenticated' }, { status: 401 });
    }

    if (!IDE_JWT_SECRET) {
      return NextResponse.json({ error: 'IDE not configured' }, { status: 503 });
    }

    // Determine role from user metadata or subscription
    const role = user.user_metadata?.role === 'admin' ? 'admin' : 'subscriber';

    // Generate short-lived JWT for IDE handoff
    const secret = new TextEncoder().encode(IDE_JWT_SECRET);
    const token = await new SignJWT({
      sub: user.id,
      role,
      email: user.email,
    })
      .setProtectedHeader({ alg: 'HS256' })
      .setIssuedAt()
      .setExpirationTime('60s')
      .sign(secret);

    return NextResponse.json({
      url: `${IDE_URL}?token=${token}`,
    });
  } catch (err) {
    console.error('[ide/launch]', err);
    return NextResponse.json({ error: 'Failed to generate launch token' }, { status: 500 });
  }
}
```

- [ ] **Step 3: Create the launcher page**

```tsx
// app/ide/page.tsx
import { checkIdeStatus } from '@/lib/ide-client';
import { createClient } from '@/lib/supabase/server';
import { redirect } from 'next/navigation';
import { IdeLauncher } from './ide-launcher';

export const metadata = {
  title: 'IDE | Crowe Logic AI',
  description: 'Browser-based VS Code IDE powered by Crowe Logic Foundry',
};

export default async function IdePage() {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();

  if (!user) {
    redirect('/auth?next=/ide');
  }

  const status = await checkIdeStatus();
  const role = user.user_metadata?.role === 'admin' ? 'admin' : 'subscriber';

  return (
    <div className="container mx-auto max-w-2xl py-12 px-4">
      <h1 className="text-3xl font-bold mb-2">Crowe Logic IDE</h1>
      <p className="text-muted-foreground mb-8">
        Browser-based VS Code environment powered by Crowe Logic Foundry
      </p>
      <IdeLauncher status={status} role={role} />
    </div>
  );
}
```

- [ ] **Step 4: Create the client component**

```tsx
// app/ide/ide-launcher.tsx
'use client';

import { useState } from 'react';
import type { IdeStatus } from '@/lib/ide-client';

interface IdeLauncherProps {
  status: IdeStatus;
  role: string;
}

export function IdeLauncher({ status, role }: IdeLauncherProps) {
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleLaunch() {
    setLaunching(true);
    setError(null);
    try {
      const res = await fetch('/api/ide/launch', { method: 'POST' });
      const data = await res.json();
      if (data.url) {
        window.open(data.url, '_blank');
      } else {
        setError(data.error || 'Failed to launch IDE');
      }
    } catch {
      setError('Network error. Please try again.');
    } finally {
      setLaunching(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* Status */}
      <div className="flex items-center gap-3 p-4 rounded-lg border bg-card">
        <div
          className={`w-3 h-3 rounded-full ${
            status.online ? 'bg-green-500' : 'bg-red-500'
          }`}
        />
        <span className="text-sm">
          IDE Server: {status.online ? 'Online' : 'Offline'}
        </span>
        <span className="ml-auto text-xs text-muted-foreground">
          {role === 'admin' ? 'Admin Access' : 'Subscriber Access'}
        </span>
      </div>

      {/* Launch Button */}
      <button
        onClick={handleLaunch}
        disabled={!status.online || launching}
        className="w-full py-3 px-6 rounded-lg bg-primary text-primary-foreground font-medium
                   disabled:opacity-50 disabled:cursor-not-allowed
                   hover:bg-primary/90 transition-colors"
      >
        {launching ? 'Launching...' : 'Launch IDE'}
      </button>

      {error && (
        <p className="text-sm text-red-500">{error}</p>
      )}

      {/* Info */}
      <div className="text-sm text-muted-foreground space-y-2">
        {role === 'admin' ? (
          <p>Full access to crowe-logic-foundry workspace with terminal and all extensions.</p>
        ) : (
          <>
            <p>Sandbox workspace with example scripts and documentation.</p>
            <p>Your container will automatically stop after 30 minutes of inactivity.</p>
          </>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Commit (in crowe-logic-ai repo)**

```bash
cd /Users/crowelogic/Projects/crowe-logic-ai
git add app/ide/page.tsx app/ide/ide-launcher.tsx app/api/ide/launch/route.ts lib/ide-client.ts
git commit -m "feat: add IDE launcher page and auth handoff API route"
```

---

## Task 12: Azure VM Provisioning and Go-Live

This is an infrastructure task — no TDD, just step-by-step deployment.

- [ ] **Step 1: Create the Azure VM**

```bash
# Create resource group (if not exists)
az group create --name crowe-logic-ide --location eastus2

# Create VM
az vm create \
  --resource-group crowe-logic-ide \
  --name crowe-ide-vm \
  --image Ubuntu2404 \
  --size Standard_B2s \
  --admin-username crowelogic \
  --generate-ssh-keys \
  --os-disk-size-gb 64 \
  --data-disk-sizes-gb 128 \
  --public-ip-sku Standard \
  --nsg-rule SSH

# Open ports 80 and 443
az vm open-port --resource-group crowe-logic-ide --name crowe-ide-vm --port 80 --priority 1001
az vm open-port --resource-group crowe-logic-ide --name crowe-ide-vm --port 443 --priority 1002
```

- [ ] **Step 2: Configure DNS**

Add an A record for `ide.southwestmushrooms.com` pointing to the VM's public IP.

Find the IP:
```bash
az vm show -d --resource-group crowe-logic-ide --name crowe-ide-vm --query publicIps -o tsv
```

- [ ] **Step 3: SSH in and run bootstrap**

```bash
# Get the IP
VM_IP=$(az vm show -d --resource-group crowe-logic-ide --name crowe-ide-vm --query publicIps -o tsv)

# SSH in
ssh crowelogic@$VM_IP

# Run bootstrap script
sudo bash /tmp/setup.sh
```

- [ ] **Step 4: Deploy application files**

From local machine:
```bash
# Copy all deploy files to the VM
scp -r deploy/ide/* crowelogic@$VM_IP:/opt/crowe-ide/

# Copy requirements.txt for Docker build
scp requirements.txt crowelogic@$VM_IP:/opt/crowe-ide/
```

- [ ] **Step 5: Configure and start services on VM**

```bash
# SSH into VM
ssh crowelogic@$VM_IP

# Install Session Router deps
cd /opt/crowe-ide/session-router
npm install --production

# Configure environment
cp .env.example .env
# Edit .env with real values:
#   SUPABASE_URL=https://your-project.supabase.co
#   IDE_JWT_SECRET=<generate with: openssl rand -hex 32>
nano .env

# Install Nginx config
sudo cp /opt/crowe-ide/nginx/ide.conf /etc/nginx/sites-available/ide
sudo ln -s /etc/nginx/sites-available/ide /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# Get TLS certificate
sudo certbot --nginx -d ide.southwestmushrooms.com

# Install and start Session Router service
sudo cp /opt/crowe-ide/systemd/crowe-ide-router.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now crowe-ide-router

# Build Docker image and start admin container
cd /opt/crowe-ide
sudo docker compose build
sudo docker compose up -d
```

- [ ] **Step 6: Verify end-to-end**

1. Check Session Router: `curl https://ide.southwestmushrooms.com/health`
   Expected: `{"status":"ok","timestamp":...}`

2. Check admin container: `sudo docker compose ps`
   Expected: admin container running

3. Log into ai.southwestmushrooms.com, navigate to /ide, click Launch IDE
   Expected: VS Code opens in new tab at ide.southwestmushrooms.com with Dracula theme

- [ ] **Step 7: Add environment variables to crowe-logic-ai**

In Railway dashboard for crowe-logic-ai, add:
```
NEXT_PUBLIC_IDE_URL=https://ide.southwestmushrooms.com
IDE_JWT_SECRET=<same secret as on the VM>
```

Redeploy the Railway app.
