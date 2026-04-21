const { createContainerManager } = require('../containers');

// No-op ready check for tests (no real HTTP server to poll)
const noopReady = async () => {};

function createMockDocker() {
  const containers = new Map();
  const containerHandles = new Map();
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
      if (containerHandles.has(id)) return containerHandles.get(id);
      const container = containers.get(id);
      const handle = {
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
      containerHandles.set(id, handle);
      return handle;
    }),
  };
}

describe('getOrCreateContainer', () => {
  test('creates a new admin container when none exists', async () => {
    const docker = createMockDocker();
    const mgr = createContainerManager({ docker, imageName: 'crowe-ide-codeserver', waitForReady: noopReady });
    const result = await mgr.getOrCreateContainer('user-admin', 'admin');
    expect(result.containerId).toBe('container-1');
    expect(result.port).toBeDefined();
    expect(docker.createContainer).toHaveBeenCalled();
    const createOpts = docker.createContainer.mock.calls[0][0];
    expect(createOpts.Labels['crowe-ide.user-id']).toBe('user-admin');
    expect(createOpts.Labels['crowe-ide.role']).toBe('admin');
    expect(createOpts.HostConfig.PortBindings['8080/tcp'][0].HostIp).toBe('127.0.0.1');
  });

  test('creates subscriber container with resource limits', async () => {
    const docker = createMockDocker();
    const mgr = createContainerManager({ docker, imageName: 'crowe-ide-codeserver', waitForReady: noopReady });
    const result = await mgr.getOrCreateContainer('user-sub', 'subscriber');
    expect(result.containerId).toBe('container-1');
    const createOpts = docker.createContainer.mock.calls[0][0];
    expect(createOpts.HostConfig.NanoCpus).toBe(500000000); // 0.5 CPU
    expect(createOpts.HostConfig.Memory).toBe(512 * 1024 * 1024); // 512 MB
    expect(createOpts.Labels['crowe-ide.role']).toBe('subscriber');
  });

  test('returns existing container if already running', async () => {
    const docker = createMockDocker();
    const mgr = createContainerManager({ docker, imageName: 'crowe-ide-codeserver', waitForReady: noopReady });
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
    const mgr = createContainerManager({ docker, imageName: 'crowe-ide-codeserver', waitForReady: noopReady });
    const { containerId } = await mgr.getOrCreateContainer('user-1', 'subscriber');
    await mgr.stopContainer(containerId);
    const mock = docker.getContainer(containerId);
    expect(mock.stop).toHaveBeenCalled();
  });
});

describe('removeContainer', () => {
  test('removes a stopped container', async () => {
    const docker = createMockDocker();
    const mgr = createContainerManager({ docker, imageName: 'crowe-ide-codeserver', waitForReady: noopReady });
    const { containerId } = await mgr.getOrCreateContainer('user-1', 'subscriber');
    await mgr.removeContainer(containerId);
    const mock = docker.getContainer(containerId);
    expect(mock.remove).toHaveBeenCalled();
  });
});

describe('port allocation', () => {
  test('assigns unique ports to different users', async () => {
    const docker = createMockDocker();
    const mgr = createContainerManager({ docker, imageName: 'crowe-ide-codeserver', waitForReady: noopReady });
    const a = await mgr.getOrCreateContainer('user-a', 'subscriber');
    const b = await mgr.getOrCreateContainer('user-b', 'subscriber');
    expect(a.port).not.toBe(b.port);
  });
});

describe('concurrent creation', () => {
  test('serializes simultaneous requests for the same user (no double-create)', async () => {
    const docker = createMockDocker();
    // Slow down createContainer so the second call definitely arrives before
    // the first one has a chance to finish — this is exactly the race
    // condition the in-flight map exists to prevent.
    const realCreate = docker.createContainer.getMockImplementation();
    docker.createContainer.mockImplementation(async (opts) => {
      await new Promise((r) => setTimeout(r, 25));
      return realCreate(opts);
    });

    const mgr = createContainerManager({ docker, imageName: 'crowe-ide-codeserver', waitForReady: noopReady });
    const [a, b] = await Promise.all([
      mgr.getOrCreateContainer('race-user', 'subscriber'),
      mgr.getOrCreateContainer('race-user', 'subscriber'),
    ]);
    expect(a.containerId).toBe(b.containerId);
    expect(a.port).toBe(b.port);
    expect(docker.createContainer).toHaveBeenCalledTimes(1);
  });

  test('different users still create separate containers in parallel', async () => {
    const docker = createMockDocker();
    const mgr = createContainerManager({ docker, imageName: 'crowe-ide-codeserver', waitForReady: noopReady });
    const [a, b] = await Promise.all([
      mgr.getOrCreateContainer('user-a', 'subscriber'),
      mgr.getOrCreateContainer('user-b', 'subscriber'),
    ]);
    expect(a.containerId).not.toBe(b.containerId);
    expect(a.port).not.toBe(b.port);
    expect(docker.createContainer).toHaveBeenCalledTimes(2);
  });
});

describe('input validation', () => {
  test('throws on invalid userId', async () => {
    const docker = createMockDocker();
    const mgr = createContainerManager({ docker, imageName: 'crowe-ide-codeserver', waitForReady: noopReady });
    await expect(mgr.getOrCreateContainer('', 'admin')).rejects.toThrow('Invalid userId');
    await expect(mgr.getOrCreateContainer(null, 'admin')).rejects.toThrow('Invalid userId');
    await expect(mgr.getOrCreateContainer('user with spaces', 'admin')).rejects.toThrow('Invalid userId');
  });

  test('throws on unknown role', async () => {
    const docker = createMockDocker();
    const mgr = createContainerManager({ docker, imageName: 'crowe-ide-codeserver', waitForReady: noopReady });
    await expect(mgr.getOrCreateContainer('user-1', 'unknown-role')).rejects.toThrow('Unknown profile');
  });
});
