// cleanup.js — Idle container cleanup job for Crowe Logic IDE
// Periodically stops idle subscriber containers and removes stale stopped ones.
// Admin containers are never touched.

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
      try {
        if (c.role === 'admin') continue;

        const idleMs = getNow() - c.lastActivity;

        if (c.state === 'running' && idleMs >= idleStopMs) {
          console.log(`[cleanup] Stopping idle container ${c.containerId} (idle ${Math.round(idleMs / 60000)}m)`);
          await containerManager.stopContainer(c.containerId);
        } else if (c.state === 'exited' && idleMs >= idleRemoveMs) {
          console.log(`[cleanup] Removing stale container ${c.containerId} (idle ${Math.round(idleMs / 3600000)}h)`);
          await containerManager.removeContainer(c.containerId);
        }
      } catch (err) {
        console.error(`[cleanup] Error processing ${c.containerId}:`, err.message);
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
