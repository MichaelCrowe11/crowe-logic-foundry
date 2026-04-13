// deploy/ide/session-router/index.js
const express = require('express');
const http = require('http');
const cookie = require('cookie');
const { createAuthModule } = require('./auth');
const { createContainerManager } = require('./containers');
const { createCleanupJob } = require('./cleanup');
const { createProxyServer } = require('./proxy');
const { createEntitlementsChecker } = require('./entitlements');
const { createMeteringClient } = require('./metering');

// Load env from .env file if present
try { require('dotenv').config(); } catch (_) { /* dotenv is optional */ }

const PORT = parseInt(process.env.PORT || '3001', 10);
const IDE_JWT_SECRET = process.env.IDE_JWT_SECRET;
const IMAGE_NAME = process.env.IMAGE_NAME || 'crowe-ide-codeserver';
const COOKIE_DOMAIN = process.env.COOKIE_DOMAIN || undefined;
const CONTROL_PLANE_URL = process.env.CONTROL_PLANE_URL || undefined;
const CONTROL_PLANE_API_KEY = process.env.CONTROL_PLANE_API_KEY || undefined;

if (!IDE_JWT_SECRET) {
  console.error('IDE_JWT_SECRET is required (must match the launcher in crowe-logic-ai)');
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

const auth = createAuthModule({ ideJwtSecret: IDE_JWT_SECRET });
const containerMgr = createContainerManager({ docker, imageName: IMAGE_NAME });
const proxy = createProxyServer();
const entitlements = createEntitlementsChecker({
  controlPlaneUrl: CONTROL_PLANE_URL,
  controlPlaneApiKey: CONTROL_PLANE_API_KEY,
});
const metering = createMeteringClient({
  controlPlaneUrl: CONTROL_PLANE_URL,
  controlPlaneApiKey: CONTROL_PLANE_API_KEY,
});

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
        userId: c.Labels['crowe-ide.user-id'] || null,
        role: c.Labels['crowe-ide.role'] || 'subscriber',
        runtimeClass: c.Labels['crowe-ide.runtime-class'] || null,
        // KNOWN LIMITATION: Created time is used as a proxy for activity until
        // proper tracking is added. Set IDLE_STOP_MINUTES generously (recommend 240+).
        lastActivity: c.Created * 1000,
        state: c.State,
      }));
    },
    stopContainer: (id) => containerMgr.stopContainer(id),
    removeContainer: (id) => containerMgr.removeContainer(id),
  },
  metering,
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
    // Handoff flow: query parameter token from the launcher.
    // Validate as a handoff JWT, then mint a fresh long-lived session token,
    // store it as an httpOnly cookie, and 302 to strip the URL.
    if (req._queryToken) {
      let user;
      try {
        user = await auth.validateHandoffToken(req._queryToken);
      } catch (err) {
        console.error(`[router] Handoff auth error: ${err.message}`);
        res.status(401).json({ error: 'Invalid or expired handoff token' });
        return;
      }
      const sessionToken = await auth.mintSessionToken({
        userId: user.userId,
        role: user.role,
        email: user.email,
      });
      res.setHeader('Set-Cookie', cookie.serialize(auth.COOKIE_NAME, sessionToken, {
        httpOnly: true,
        secure: true,
        sameSite: 'lax', // 'lax' lets the cookie ride along on top-level navigations from the launcher
        ...(COOKIE_DOMAIN ? { domain: COOKIE_DOMAIN } : {}),
        path: '/',
        maxAge: auth.SESSION_TTL_SECONDS,
      }));
      res.redirect(302, '/');
      return;
    }

    // Cookie/Bearer flow: validate the session token.
    const token = auth.extractToken(req);
    if (!token) {
      res.status(401).json({ error: 'Authentication required' });
      return;
    }

    let user;
    try {
      user = await auth.validateSessionToken(token);
    } catch (err) {
      console.error(`[router] Session auth error: ${err.message}`);
      res.status(401).json({ error: 'Invalid or expired session' });
      return;
    }

    // Get or create container
    try {
      // Check entitlements before container launch
      const ent = await entitlements.checkEntitlements(user.userId, user.email);
      if (!ent.allowed) {
        res.status(403).json({ error: ent.reason || 'IDE access denied by plan' });
        return;
      }

      const { port, containerId } = await containerMgr.getOrCreateContainer(
        user.userId, user.role, ent.runtimeClass
      );

      // Record session start (idempotent — skips if already tracked)
      if (!metering.getActiveSession(user.userId)) {
        await metering.recordSessionStart({
          userId: user.userId,
          containerId,
          runtimeClass: ent.runtimeClass,
          workspaceId: ent.workspaceId,
        });
      }

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
    user = await auth.validateSessionToken(token);
  } catch (err) {
    console.error(`[router] WebSocket auth error: ${err.message}`);
    socket.destroy();
    return;
  }

  try {
    // Check entitlements for WebSocket connections too
    const ent = await entitlements.checkEntitlements(user.userId, user.email);
    if (!ent.allowed) {
      socket.destroy();
      return;
    }

    const { port } = await containerMgr.getOrCreateContainer(
      user.userId, user.role, ent.runtimeClass
    );
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
