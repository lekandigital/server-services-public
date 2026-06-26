function createCastWatchdog({ castSessions, diagnostics, providerGetter, onRecover, logger = () => {}, intervalMs = 5000, maxRecoveries = 2 } = {}) {
  let timer = null;
  let recoveryCount = 0;

  async function tick() {
    const session = castSessions.get();
    if (!session) return;
    const diag = diagnostics.summarizeSession(session.sessionId)?.session;
    if (!diag) return;

    const provider = providerGetter(session.provider);
    const status = await provider.status(session.deviceId).catch(() => null);
    const staleStream = diag.tvRequestedStream && diag.streamRequests.length > 0;
    const lastReq = diag.streamRequests[diag.streamRequests.length - 1];
    const streamStale = staleStream && lastReq && (Date.now() - new Date(lastReq.at).getTime() > intervalMs * 6);

    if (session.state === 'playing' && status?.state === 'idle') {
      logger(`watchdog: receiver idle while session playing`);
      diagnostics.transitionState(session.sessionId, 'failed', 'Receiver went idle', { primaryFailureCode: 'STATUS_STUCK_IDLE' });
    }

    if (streamStale && recoveryCount < maxRecoveries && typeof onRecover === 'function') {
      recoveryCount += 1;
      logger(`watchdog: attempting recovery #${recoveryCount}`);
      try { await onRecover(session); } catch (err) { logger(`watchdog recovery failed: ${err.message}`); }
    }
  }

  return {
    start() {
      if (timer) return;
      timer = setInterval(() => { tick().catch(() => {}); }, intervalMs);
    },
    stop() {
      if (timer) clearInterval(timer);
      timer = null;
      recoveryCount = 0;
    },
  };
}

module.exports = {
  createCastWatchdog,
};
