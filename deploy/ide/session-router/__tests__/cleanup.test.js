const { createCleanupJob } = require('../cleanup');

function createMockContainerManager() {
  const containers = [];
  return {
    containers,
    addContainer(id, role, lastActivity, state = 'running') {
      containers.push({ id, role, lastActivity, state });
    },
    listAll: jest.fn(async () =>
      containers.map((c) => ({
        containerId: c.id,
        role: c.role,
        lastActivity: c.lastActivity,
        state: c.state,
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

  test('continues cleanup when one container fails', async () => {
    const mgr = createMockContainerManager();
    const now = Date.now();
    mgr.addContainer('c1', 'subscriber', now - 31 * 60 * 1000); // idle
    mgr.addContainer('c2', 'subscriber', now - 31 * 60 * 1000); // idle

    // First stop call throws, second should still succeed
    mgr.stopContainer
      .mockRejectedValueOnce(new Error('Docker API error'))
      .mockResolvedValueOnce();

    const cleanup = createCleanupJob({
      containerManager: mgr,
      idleStopMinutes: 30,
      idleRemoveHours: 24,
      getNow: () => now,
    });

    await cleanup.runOnce(); // Should not throw
    expect(mgr.stopContainer).toHaveBeenCalledTimes(2);
    expect(mgr.stopContainer).toHaveBeenCalledWith('c1');
    expect(mgr.stopContainer).toHaveBeenCalledWith('c2');
  });
});
