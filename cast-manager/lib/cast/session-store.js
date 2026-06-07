const crypto = require('crypto');

function createSessionStore() {
  let active = null;

  function create(input) {
    active = {
      sessionId: `cast_${crypto.randomBytes(8).toString('hex')}`,
      provider: input.provider,
      deviceId: input.deviceId,
      deviceName: input.deviceName || '',
      filePath: input.filePath || null,
      title: input.title || '',
      mediaKind: input.mediaKind || 'video',
      preparedMedia: input.preparedMedia || null,
      streamUrl: input.streamUrl || null,
      jobId: input.jobId || null,
      pipelineMode: input.pipelineMode || null,
      backend: input.backend || null,
      startedAt: new Date().toISOString(),
      startSeconds: Number(input.startSeconds || 0),
      lastKnownTime: Number(input.startSeconds || 0),
      lastKnownAt: Date.now(),
      duration: Number(input.duration || 0),
      state: input.state || 'playing',
      statusSource: 'app',
      lastError: null,
    };
    return active;
  }

  function update(patch = {}) {
    if (!active) return null;
    const shouldTouchClock = Object.prototype.hasOwnProperty.call(patch, 'lastKnownTime')
      || Object.prototype.hasOwnProperty.call(patch, 'state')
      || Object.prototype.hasOwnProperty.call(patch, 'startSeconds');
    active = {
      ...active,
      ...patch,
      lastKnownAt: patch.lastKnownAt || (shouldTouchClock ? Date.now() : active.lastKnownAt),
    };
    return active;
  }

  function get() {
    return active;
  }

  function clear() {
    const old = active;
    active = null;
    return old;
  }

  return { clear, create, get, update };
}

module.exports = {
  createSessionStore,
};
