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
const COOKIE_DOMAIN = process.env.COOKIE_DOMAIN || undefined;

if (!SUPABASE_URL) {
  console.error('SUPABASE_URL is required');
  process.exit(1);
}

// Top-level error handlers — keep the process alive on unhandled rejections,
// but exit on uncaught exceptions (systemd will restart us).
process.on('unhandledRejection', (reason) => {
  console.error('[router] Unhandled rejection:', reason);
});
process.on('uncaughtException', (err) => {
  console.error('[router] Uncaught exception:', err);
  process.exit(1);
});

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
        // KNOWN LIMITATION: Created time is used as a proxy for activity until
        // proper tracking is added. Set IDLE_STOP_MINUTES generously (recommend 240+).
        lastActivity: c.Created * 1000,
        state: c.State,
      }));
    },
    stopContainer: (id) => containerMgr.stopContainer(id),
    removeContainer: (id) => containerMgr.removeContainer(id),
  },
  idleStopMinutes: parseInt(process.env.IDLE_STOP_MINUTES || '240', 10),
  idleRemoveHours: parseInt(process.env.IDLE_REMOVE_HOURS || '24', 10),
  intervalMinutes: parseInt(process.env.IDLE_CHECK_INTERVAL_MINUTES || '5', 10),
});

// Express app
const app = express();

// Strip token from req.url BEFORE anything else can log it.
// req.query is still available for downstream handlers via Express parsing of the ORIGINAL URL,
// so we also clear it defensively.
app.use((req, _res, next) => {
  if (req.query && req.query.token) {
    req._queryToken = req.query.token;
    delete req.query.token;
    try {
      const u = new URL(req.url, 'http://placeholder');
      u.searchParams.delete('token');
      req.url = u.pathname + (u.search || '');
    } catch (_) { /* ignore malformed URLs */ }
  }
  next();
});

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
    // Prefer the query token if present (this is the handoff flow)
    const token = req._queryToken || auth.extractToken(req);
    if (!token) {
      res.status(401).json({ error: 'Authentication required' });
      return;
    }

    let user;
    try {
      user = await auth.validateToken(token);
    } catch (err) {
      console.error(`[router] Auth error: ${err.message}`);
      res.status(401).json({ error: 'Invalid or expired session' });
      return;
    }

    // If token came from query param, set cookie and redirect (strips token from URL)
    if (req._queryToken) {
      res.setHeader('Set-Cookie', cookie.serialize(auth.COOKIE_NAME, token, {
        httpOnly: true,
        secure: true,
        sameSite: 'strict',
        ...(COOKIE_DOMAIN ? { domain: COOKIE_DOMAIN } : {}),
        path: '/',
        maxAge: 60 * 60 * 24, // 24 hours
      }));
      res.redirect(302, '/');
      return;
    }

    // Get or create container
    try {
      const { port } = await containerMgr.getOrCreateContainer(user.userId, user.role);
      proxy.proxyRequest(req, res, port);
    } catch (err) {
      console.error(`[router] Container error for user ${user.userId}: ${err.message}`);
      res.status(503).json({ error: 'IDE container unavailable. Please retry in a moment.' });
    }
  } catch (err) {
    console.error(`[router] Unexpected error: ${err.message}`);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// Create HTTP server for WebSocket support
const server = http.createServer(app);

// Handle WebSocket upgrades
server.on('upgrade', async (req, socket, head) => {
  let user;
  try {
    const cookies = cookie.parse(req.headers.cookie || '');
    const token = cookies[auth.COOKIE_NAME];
    if (!token) {
      socket.destroy();
      return;
    }
    user = await auth.validateToken(token);
  } catch (err) {
    console.error(`[router] WebSocket auth error: ${err.message}`);
    socket.destroy();
    return;
  }

  try {
    const { port } = await containerMgr.getOrCreateContainer(user.userId, user.role);
    proxy.proxyWebSocket(req, socket, head, port);
  } catch (err) {
    console.error(`[router] WebSocket container error for user ${user.userId}: ${err.message}`);
    socket.destroy();
  }
});

// Start
server.listen(PORT, () => {
  console.log(`[router] Crowe Logic IDE Session Router listening on port ${PORT}`);
  cleanup.start();
});

// Graceful shutdown — handle both SIGTERM (systemd) and SIGINT (Ctrl-C in dev)
function shutdown(signal) {
  console.log(`[router] Received ${signal}, shutting down...`);
  cleanup.stop();
  server.closeIdleConnections?.();
  const force = setTimeout(() => {
    console.warn('[router] Force-closing remaining connections');
    server.closeAllConnections?.();
  }, 5000);
  server.close((err) => {
    clearTimeout(force);
    if (err) {
      console.error(`[router] Error during close: ${err.message}`);
      process.exit(1);
    }
    process.exit(0);
  });
}
process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
