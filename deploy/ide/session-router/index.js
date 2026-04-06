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
