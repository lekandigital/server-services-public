const { makeProviderDeviceId, stripProviderPrefix } = require('./provider-interface');
const { parseCattStatus } = require('./status-normalizer');

function shEsc(str) {
  return `'${String(str || '').replace(/'/g, "'\\''")}'`;
}

function secondsToHMS(totalSeconds) {
  const s = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
}

function createChromecastProvider({ cfg, sshExec, upsertDevice, setSelectedDevice, getSelectedDevice }) {
  async function catt(args, timeout = 15000, deviceId = null) {
    const selectedName = stripProviderPrefix('chromecast', deviceId || cfg.chromecastName);
    return sshExec(`${cfg.cattPath} -d ${shEsc(selectedName)} ${args}`, timeout);
  }

  return {
    name: 'chromecast',

    async discover() {
      let { stdout, stderr, code } = await sshExec(`${cfg.cattPath} scan -t 5`, 15000);
      if (code !== 0 && /No such option|Usage:/i.test(String(stderr || stdout))) {
        ({ stdout, stderr, code } = await sshExec(`timeout 8s ${cfg.cattPath} scan`, 12000));
      }
      if (code !== 0) throw new Error(stderr || stdout || 'catt scan failed');
      const selected = await getSelectedDevice?.('chromecast');
      const devices = [];
      for (const line of String(stdout || '').split('\n')) {
        if (!line.trim()) continue;
        const ipFirst = line.match(/^([0-9a-fA-F:.]+)(?::(\d+))?\s+-\s+(.+?)(?:\s+-\s+(.+))?$/);
        const nameFirst = line.match(/^(.+?)\s+-\s+([0-9a-fA-F:.]+)(?::(\d+))?/);
        const match = ipFirst || nameFirst;
        if (!match) continue;
        const name = ipFirst ? match[3].trim() : match[1].trim();
        const host = ipFirst ? match[1] : match[2];
        const port = ipFirst ? (match[2] ? parseInt(match[2], 10) : undefined) : (match[3] ? parseInt(match[3], 10) : undefined);
        const model = ipFirst ? match[4] : undefined;
        const device = {
          id: makeProviderDeviceId('chromecast', name),
          provider: 'chromecast',
          name,
          host,
          port,
          model,
          selected: selected ? selected.device_id === makeProviderDeviceId('chromecast', name) : name === cfg.chromecastName,
          capabilities: { video: true, audio: true, subtitles: true, seek: true, volume: true },
        };
        devices.push(device);
        await upsertDevice?.(device);
      }
      return devices;
    },

    async selectDevice(deviceId) {
      const name = stripProviderPrefix('chromecast', deviceId);
      if (!name) throw new Error('Chromecast device id is required');
      cfg.chromecastName = name;
      const fullId = makeProviderDeviceId('chromecast', name);
      await setSelectedDevice?.('chromecast', fullId);
      return { success: true, provider: 'chromecast', deviceId: fullId, name };
    },

    async play(input) {
      const start = Math.max(0, Math.floor(Number(input.startSeconds) || 0));
      const seekArg = input.preparedMedia?.receiverSeek && start > 0 ? `-t ${secondsToHMS(start)}` : '';
      const subtitleArg = input.subtitlesUrl ? `--subtitles ${shEsc(input.subtitlesUrl)}` : '';
      await catt(`cast ${seekArg} ${subtitleArg} ${shEsc(input.streamUrl)}`.replace(/\s+/g, ' ').trim(), 30000, input.deviceId);
      return {
        success: true,
        provider: 'chromecast',
        deviceId: input.deviceId,
        state: 'playing',
        streamUrl: input.streamUrl,
        backend: input.preparedMedia?.backend,
        pipelineMode: input.preparedMedia?.pipelineMode,
      };
    },

    async pause(deviceId) {
      const result = await catt('pause', 12000, deviceId);
      return { success: true, state: 'paused', output: result.stdout };
    },

    async resume(deviceId) {
      const result = await catt('play', 12000, deviceId);
      return { success: true, state: 'playing', output: result.stdout };
    },

    async stop(deviceId) {
      const result = await catt('stop', 15000, deviceId);
      return { success: true, state: 'idle', output: result.stdout };
    },

    async seek(deviceId, seconds) {
      const target = Math.max(0, Math.floor(Number(seconds) || 0));
      const result = await catt(`seek ${target}`, 15000, deviceId);
      await catt('play', 12000, deviceId).catch(() => {});
      return { success: true, state: 'playing', currentTime: target, output: result.stdout };
    },

    async volume(deviceId, level) {
      const vol = Math.max(0, Math.min(100, parseInt(level, 10)));
      const result = await catt(`volume ${vol}`, 12000, deviceId);
      return { success: true, volumeLevel: vol, output: result.stdout };
    },

    async status(deviceId) {
      try {
        const { stdout, stderr } = await catt('status', 8000, deviceId);
        return { success: true, receiverReachable: true, ...parseCattStatus(stdout || stderr || '') };
      } catch (err) {
        return { success: false, receiverReachable: false, state: 'unknown', error: err.message };
      }
    },
  };
}

module.exports = {
  createChromecastProvider,
};
