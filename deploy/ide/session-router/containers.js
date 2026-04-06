// deploy/ide/session-router/containers.js
// Container lifecycle manager — creates, starts, stops, and removes
// per-user Docker containers with role-based resource limits.

const LABEL_PREFIX = 'crowe-ide';
const BASE_PORT = 10001; // 10000 reserved for admin in docker-compose
const MAX_PORT = 10100;

const PROFILES = {
  admin: {
    nanoCpus: 2 * 1e9,                  // 2 CPU
    memory: 2 * 1024 * 1024 * 1024,     // 2 GB
    network: 'ide-admin',
  },
  subscriber: {
    nanoCpus: 0.5 * 1e9,                // 0.5 CPU
    memory: 512 * 1024 * 1024,          // 512 MB
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
    if (typeof userId !== 'string' || userId.length === 0 || !/^[\w-]+$/.test(userId)) {
      throw new Error('Invalid userId');
    }
    if (role && !PROFILES[role]) {
      throw new Error(`Unknown role: ${role}`);
    }

    const existing = await findExisting(userId);
    if (existing && existing.state === 'running') {
      return { containerId: existing.containerId, port: existing.port };
    }
    if (existing && existing.state !== 'running') {
      const container = docker.getContainer(existing.containerId);
      await container.start();
      const info = await container.inspect();
      const bindings = info.NetworkSettings?.Ports?.['8080/tcp'];
      const port = bindings?.[0]?.HostPort ? parseInt(bindings[0].HostPort, 10) : existing.port;
      if (port) allocatedPorts.add(port);
      return { containerId: existing.containerId, port };
    }

    const profile = PROFILES[role];
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
          '8080/tcp': [{ HostIp: '127.0.0.1', HostPort: String(port) }],
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
    const bindings = info.NetworkSettings?.Ports?.['8080/tcp'];
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
