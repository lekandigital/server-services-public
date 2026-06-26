const fs = require('fs');
const path = require('path');
const { runPreflight, redactEnv } = require('./preflight');

async function runCastDoctor(deps) {
  const {
    req,
    cfg,
    castConfig,
    sshExec,
    getReceiverBaseUrl,
    probeVlcAvailable,
    diagnostics,
    deviceName,
  } = deps;

  const checks = [];
  const add = (id, label, ok, detail = '', suggestion = '') => {
    checks.push({ id, label, ok, detail, suggestion });
  };

  const preflight = await runPreflight({
    req,
    cfg,
    castConfig,
    sshExec,
    getReceiverBaseUrl,
    backend: 'auto',
    deviceName,
    diagnostics,
  });
  for (const c of preflight.checks) {
    add(c.name, c.name.replace(/_/g, ' '), c.ok, c.detail, c.suggestion);
  }

  const vlc = await probeVlcAvailable?.().catch(() => ({ ok: false, reason: 'probe failed' }));
  add('vlc_relay', 'VLC relay works', !!vlc?.ok, vlc?.reason || vlc?.ok ? 'cvlc available' : 'unavailable', vlc?.ok ? '' : 'Optional fallback');

  let hlsOk = false;
  try {
    const { stdout } = await sshExec('ffmpeg -hide_banner -h muxer=hls 2>&1 | head -1', 8000);
    hlsOk = /hls/i.test(stdout);
  } catch (_) {}
  add('hls_backend', 'HLS backend available', hlsOk && castConfig.enableHlsBackend, hlsOk ? 'ffmpeg HLS muxer present' : 'ffmpeg HLS missing', '');

  const report = {
    ok: checks.every((c) => c.ok || ['vlc_relay'].includes(c.id)),
    checks,
    preflight,
    env: redactEnv(),
    at: new Date().toISOString(),
  };
  diagnostics?.setDoctorReport?.(report);
  return report;
}

module.exports = {
  runCastDoctor,
};
