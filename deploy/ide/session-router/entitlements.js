// entitlements.js — Check workspace entitlements via the Control Plane API
// before launching IDE containers. Caches plan data briefly to avoid
// hammering the API on every request.

const http = require('http');
const https = require('https');

// Plan → runtime class mapping
const PLAN_RUNTIME_CLASS = {
  developer: null,           // no IDE access
  studio: 'dev-small',       // 1 vCPU, 2 GB
  lab: 'research-medium',    // 2 vCPU, 8 GB
  enterprise: 'vision-large', // 4 vCPU, 16 GB
};

// Runtime class resource profiles (Docker HostConfig values)
const RUNTIME_PROFILES = {
  'dev-small': {
    nanoCpus: 1 * 1e9,                   // 1 vCPU
    memory: 2 * 1024 * 1024 * 1024,      // 2 GB
    network: 'ide-dev',
  },
  'research-medium': {
    nanoCpus: 2 * 1e9,                   // 2 vCPU
    memory: 8 * 1024 * 1024 * 1024,      // 8 GB
    network: 'ide-research',
  },
  'vision-large': {
    nanoCpus: 4 * 1e9,                   // 4 vCPU
    memory: 16 * 1024 * 1024 * 1024,     // 16 GB
    network: 'ide-vision',
  },
};

const CACHE_TTL_MS = 60_000; // 1 minute

function createEntitlementsChecker({ controlPlaneUrl, controlPlaneApiKey }) {
  const cache = new Map();

  function _httpGet(url) {
    return new Promise((resolve, reject) => {
      const mod = url.startsWith('https') ? https : http;
      const req = mod.get(url, {
        headers: {
          'Authorization': `Bearer ${controlPlaneApiKey}`,
          'Accept': 'application/json',
        },
        timeout: 5000,
      }, (res) => {
        let body = '';
        res.on('data', (d) => body += d);
        res.on('end', () => {
          try {
            resolve({ status: res.statusCode, data: JSON.parse(body) });
          } catch (e) {
            reject(new Error(`Invalid JSON from control plane: ${body.slice(0, 200)}`));
          }
        });
      });
      req.on('error', reject);
      req.on('timeout', () => { req.destroy(); reject(new Error('Control plane timeout')); });
    });
  }

  async function checkEntitlements(userId, email) {
    // Check cache first
    const cached = cache.get(userId);
    if (cached && Date.now() - cached.ts < CACHE_TTL_MS) {
      return cached.result;
    }

    // If no control plane URL configured, allow access with default profile
    // This lets the router run standalone during development
    if (!controlPlaneUrl) {
      const result = {
        allowed: true,
        plan: 'studio',
        runtimeClass: 'dev-small',
        maxConcurrentSessions: 2,
        maxIdeHoursMonth: 100,
        reason: null,
      };
      cache.set(userId, { ts: Date.now(), result });
      return result;
    }

    try {
      const { status, data } = await _httpGet(
        `${controlPlaneUrl}/api/workspaces?user_id=${encodeURIComponent(userId)}`
      );

      if (status !== 200 || !data || !Array.isArray(data.workspaces)) {
        return { allowed: false, reason: 'Unable to verify entitlements' };
      }

      // Find the first active workspace for this user
      const ws = data.workspaces.find(w => w.status === 'active');
      if (!ws) {
        return { allowed: false, reason: 'No active workspace found' };
      }

      const plan = ws.plan_id || 'developer';
      const runtimeClass = PLAN_RUNTIME_CLASS[plan];

      if (!runtimeClass) {
        return {
          allowed: false,
          plan,
          reason: `The ${plan} plan does not include IDE access. Upgrade to Studio or higher.`,
        };
      }

      // Check IDE entitlement
      const entResp = await _httpGet(
        `${controlPlaneUrl}/api/workspaces/${ws.id}/entitlements/ide_hours`
      );

      let maxIdeHoursMonth = 100;
      if (entResp.status === 200 && entResp.data) {
        if (entResp.data.remaining !== undefined && entResp.data.remaining <= 0) {
          return {
            allowed: false,
            plan,
            reason: 'Monthly IDE hours exhausted. Upgrade your plan or wait for the next billing cycle.',
          };
        }
        maxIdeHoursMonth = entResp.data.budget || 100;
      }

      const result = {
        allowed: true,
        plan,
        workspaceId: ws.id,
        runtimeClass,
        maxConcurrentSessions: ws.max_concurrent_sessions || 2,
        maxIdeHoursMonth,
        reason: null,
      };

      cache.set(userId, { ts: Date.now(), result });
      return result;
    } catch (err) {
      console.error(`[entitlements] Error checking entitlements for ${userId}:`, err.message);
      // Fail open in development, fail closed in production
      if (process.env.NODE_ENV === 'production') {
        return { allowed: false, reason: 'Entitlement check failed' };
      }
      return {
        allowed: true,
        plan: 'studio',
        runtimeClass: 'dev-small',
        maxConcurrentSessions: 2,
        maxIdeHoursMonth: 100,
        reason: null,
      };
    }
  }

  function clearCache(userId) {
    if (userId) cache.delete(userId);
    else cache.clear();
  }

  return {
    checkEntitlements,
    clearCache,
    PLAN_RUNTIME_CLASS,
    RUNTIME_PROFILES,
  };
}

module.exports = { createEntitlementsChecker, PLAN_RUNTIME_CLASS, RUNTIME_PROFILES };
