const { makeProviderDeviceId, stripProviderPrefix } = require('./provider-interface');

function maskCredentials(value) {
  if (!value) return null;
  return '***';
}

function createAirPlayProvider({
  sidecarUrl = process.env.AIRPLAY_SIDECAR_URL || 'http://127.0.0.1:8765',
  upsertDevice,
  setSelectedDevice,
  getSelectedDevice,
  getDevice,
  saveCredentials,
}) {
  const baseUrl = String(sidecarUrl || '').replace(/\/+$/, '');

  async function sidecar(path, { method = 'GET', body, timeoutMs = 12000 } = {}) {
    if (!baseUrl) {
      const err = new Error('AirPlay sidecar URL is not configured. Set AIRPLAY_SIDECAR_URL.');
      err.code = 'AIRPLAY_SIDECAR_MISSING';
      throw err;
    }
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(`${baseUrl}${path}`, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
        signal: ctrl.signal,
      });
      const text = await res.text();
      const data = text ? JSON.parse(text) : {};
      if (!res.ok) {
        const err = new Error(data.error || data.detail || `AirPlay sidecar returned ${res.status}`);
        err.status = res.status;
        err.details = data;
        throw err;
      }
      return data;
    } catch (err) {
      if (err.name === 'AbortError') throw new Error('AirPlay sidecar timed out');
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  async function credentialsFor(deviceId) {
    const row = await getDevice?.('airplay', deviceId);
    return row?.credentials || null;
  }

  return {
    name: 'airplay',

    async discover() {
      const data = await sidecar('/scan', { method: 'POST', timeoutMs: 20000 });
      const selected = await getSelectedDevice?.('airplay');
      const devices = (data.devices || []).map((d) => {
        const fullId = makeProviderDeviceId('airplay', d.id || d.host || d.name);
        return {
          id: fullId,
          provider: 'airplay',
          name: d.name || d.host || 'AirPlay Device',
          host: d.host,
          port: d.port,
          model: d.model,
          paired: !!d.paired,
          selected: selected?.device_id === fullId,
          capabilities: { video: true, audio: true, subtitles: false, seek: true, volume: false },
        };
      });
      for (const device of devices) await upsertDevice?.(device);
      return devices;
    },

    async selectDevice(deviceId) {
      const fullId = deviceId.startsWith('airplay:') ? deviceId : makeProviderDeviceId('airplay', deviceId);
      await setSelectedDevice?.('airplay', fullId);
      return { success: true, provider: 'airplay', deviceId: fullId };
    },

    async pairStart(input = {}) {
      const deviceId = input.deviceId ? stripProviderPrefix('airplay', input.deviceId) : '';
      return sidecar('/pair/start', { method: 'POST', body: { ...input, deviceId }, timeoutMs: 20000 });
    },

    async pairFinish(input = {}) {
      const fullId = input.deviceId?.startsWith('airplay:') ? input.deviceId : makeProviderDeviceId('airplay', input.deviceId);
      const data = await sidecar('/pair/finish', {
        method: 'POST',
        body: { ...input, deviceId: stripProviderPrefix('airplay', fullId) },
        timeoutMs: 20000,
      });
      if (data.credentials) await saveCredentials?.('airplay', fullId, data.credentials);
      return { ...data, credentials: maskCredentials(data.credentials) };
    },

    async play(input) {
      const fullId = input.deviceId?.startsWith('airplay:') ? input.deviceId : makeProviderDeviceId('airplay', input.deviceId);
      const row = await getDevice?.('airplay', fullId);
      const data = await sidecar('/play', {
        method: 'POST',
        body: {
          deviceId: stripProviderPrefix('airplay', fullId),
          host: input.host || row?.host,
          url: input.streamUrl,
          title: input.title,
          credentials: await credentialsFor(fullId),
        },
        timeoutMs: 30000,
      });
      return {
        success: true,
        provider: 'airplay',
        deviceId: fullId,
        state: 'playing',
        backend: input.preparedMedia?.backend,
        pipelineMode: input.preparedMedia?.pipelineMode,
        sidecar: data,
      };
    },

    async pause(deviceId) {
      const fullId = deviceId?.startsWith('airplay:') ? deviceId : makeProviderDeviceId('airplay', deviceId);
      return sidecar('/pause', { method: 'POST', body: { deviceId: stripProviderPrefix('airplay', fullId), credentials: await credentialsFor(fullId) } });
    },

    async resume(deviceId) {
      const fullId = deviceId?.startsWith('airplay:') ? deviceId : makeProviderDeviceId('airplay', deviceId);
      return sidecar('/resume', { method: 'POST', body: { deviceId: stripProviderPrefix('airplay', fullId), credentials: await credentialsFor(fullId) } });
    },

    async stop(deviceId) {
      const fullId = deviceId?.startsWith('airplay:') ? deviceId : makeProviderDeviceId('airplay', deviceId);
      return sidecar('/stop', { method: 'POST', body: { deviceId: stripProviderPrefix('airplay', fullId), credentials: await credentialsFor(fullId) } });
    },

    async seek(deviceId, seconds) {
      const fullId = deviceId?.startsWith('airplay:') ? deviceId : makeProviderDeviceId('airplay', deviceId);
      return sidecar('/seek', { method: 'POST', body: { deviceId: stripProviderPrefix('airplay', fullId), seconds, credentials: await credentialsFor(fullId) } });
    },

    async volume(_deviceId, _level) {
      return { success: false, state: 'unknown', error: 'Volume control varies by AirPlay receiver and is not exposed here yet' };
    },

    async status(deviceId) {
      try {
        const fullId = deviceId?.startsWith('airplay:') ? deviceId : makeProviderDeviceId('airplay', deviceId);
        const data = await sidecar('/status', { method: 'POST', body: { deviceId: stripProviderPrefix('airplay', fullId), credentials: await credentialsFor(fullId) }, timeoutMs: 10000 });
        return { success: true, receiverReachable: true, ...data };
      } catch (err) {
        return { success: false, receiverReachable: false, state: 'unknown', error: err.message };
      }
    },

    async health() {
      return sidecar('/health', { timeoutMs: 5000 });
    },
  };
}

module.exports = {
  createAirPlayProvider,
  maskCredentials,
};
