// metering.js — IDE session usage metering for Crowe Logic Foundry.
// Records session start/stop events and IDE hours to the Control Plane API.
// Falls back to local logging when the API is unavailable.

const http = require('http');
const https = require('https');

function createMeteringClient({ controlPlaneUrl, controlPlaneApiKey }) {
  // In-memory session tracker: userId → { startedAt, containerId, runtimeClass, workspaceId }
  const activeSessions = new Map();

  function _httpPost(url, body) {
    return new Promise((resolve, reject) => {
      const mod = url.startsWith('https') ? https : http;
      const payload = JSON.stringify(body);
      const parsed = new URL(url);
      const req = mod.request({
        hostname: parsed.hostname,
        port: parsed.port,
        path: parsed.pathname,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(payload),
          'Authorization': `Bearer ${controlPlaneApiKey}`,
        },
        timeout: 5000,
      }, (res) => {
        let data = '';
        res.on('data', (d) => data += d);
        res.on('end', () => resolve({ status: res.statusCode, data }));
      });
      req.on('error', reject);
      req.on('timeout', () => { req.destroy(); reject(new Error('Metering timeout')); });
      req.write(payload);
      req.end();
    });
  }

  async function recordSessionStart({ userId, containerId, runtimeClass, workspaceId }) {
    const entry = {
      startedAt: Date.now(),
      containerId,
      runtimeClass: runtimeClass || 'dev-small',
      workspaceId,
    };
    activeSessions.set(userId, entry);

    console.log(`[metering] Session started: user=${userId} class=${entry.runtimeClass} container=${containerId}`);

    if (controlPlaneUrl && workspaceId) {
      try {
        await _httpPost(`${controlPlaneUrl}/api/workspaces/${workspaceId}/usage`, {
          event_type: 'ide_session_start',
          quantity: 1,
          model: entry.runtimeClass,
          metadata: { containerId, userId },
        });
      } catch (err) {
        console.error(`[metering] Failed to report session start: ${err.message}`);
      }
    }
  }

  async function recordSessionStop({ userId, containerId }) {
    const entry = activeSessions.get(userId);
    if (!entry) {
      console.warn(`[metering] No active session found for user ${userId}`);
      return { durationHours: 0 };
    }

    const durationMs = Date.now() - entry.startedAt;
    const durationHours = Math.round((durationMs / 3_600_000) * 100) / 100;

    activeSessions.delete(userId);

    console.log(
      `[metering] Session stopped: user=${userId} duration=${durationHours}h class=${entry.runtimeClass}`
    );

    if (controlPlaneUrl && entry.workspaceId) {
      try {
        await _httpPost(`${controlPlaneUrl}/api/workspaces/${entry.workspaceId}/usage`, {
          event_type: 'ide_hours',
          quantity: Math.max(1, Math.ceil(durationHours * 60)), // bill in minutes
          model: entry.runtimeClass,
          metadata: { containerId: containerId || entry.containerId, userId, durationHours },
        });
      } catch (err) {
        console.error(`[metering] Failed to report session stop: ${err.message}`);
      }
    }

    return { durationMs, durationHours, runtimeClass: entry.runtimeClass };
  }

  function getActiveSession(userId) {
    return activeSessions.get(userId) || null;
  }

  function getActiveSessions() {
    return new Map(activeSessions);
  }

  return {
    recordSessionStart,
    recordSessionStop,
    getActiveSession,
    getActiveSessions,
  };
}

module.exports = { createMeteringClient };
