const path = require('path');

function parseBool(value, defaultValue = false) {
  if (value == null || value === '') return defaultValue;
  const raw = String(value).toLowerCase().trim();
  if (['1', 'true', 'yes', 'on'].includes(raw)) return true;
  if (['0', 'false', 'no', 'off'].includes(raw)) return false;
  return defaultValue;
}

function parseList(value, fallback = []) {
  if (!value) return [...fallback];
  return String(value).split(',').map((s) => s.trim()).filter(Boolean);
}

function loadCastConfig(cfg = {}, env = process.env) {
  const diagnosticsDir = path.resolve(env.CAST_DIAGNOSTICS_DIR || path.join(process.cwd(), 'diagnostics'));
  const tmpDir = env.CAST_TMP_DIR || '/tmp/cast-manager';
  const deviceProfilePath = path.resolve(env.CAST_DEVICE_PROFILE_PATH || path.join(process.cwd(), 'data', 'cast-device-profiles.json'));

  return {
    port: cfg.port || parseInt(env.PORT || '8004', 10),
    publicHost: cfg.publicHost || env.CAST_PUBLIC_HOST || env.SSH_HOST || '127.0.0.1',
    castPublicBaseUrl: String(env.CAST_PUBLIC_BASE_URL || env.PUBLIC_BASE_URL || env.SERVER_PUBLIC_URL || '').trim(),
    chromecastName: cfg.chromecastName || env.CHROMECAST_NAME || 'REDACTED_DEVICE',
    cattPath: cfg.cattPath || env.CATT_PATH || '/home/REDACTED_USER/.local/bin/catt',
    backendDefault: String(env.CAST_BACKEND_DEFAULT || 'auto').toLowerCase(),
    backendOrder: parseList(env.CAST_BACKEND_ORDER, ['direct', 'hls', 'ffmpeg-live', 'vlc', 'pretranscode']),
    enableVlcBackend: parseBool(env.CAST_ENABLE_VLC_BACKEND, true),
    enableHlsBackend: !parseBool(env.CAST_DISABLE_HLS_BACKEND, false) && parseBool(env.CAST_ENABLE_HLS_BACKEND, true),
    liveTranscodeEncoder: String(env.CAST_LIVE_TRANSCODE_ENCODER || 'auto').toLowerCase(),
    subtitleDefault: String(env.CAST_SUBTITLE_DEFAULT || 'auto').toLowerCase(),
    subtitleBurnInFallback: parseBool(env.CAST_SUBTITLE_BURN_IN_FALLBACK, false),
    adbEnabled: parseBool(env.CAST_ADB_ENABLED, true),
    adbSerial: String(env.CAST_ADB_SERIAL || '').trim(),
    diagnosticsDir,
    tmpDir,
    deviceProfilePath,
    hlsSegmentSeconds: Number(env.CAST_HLS_SEGMENT_SECONDS || 2) || 2,
    hlsListSize: Number(env.CAST_HLS_LIST_SIZE || 6) || 6,
    pretranscodeMaxPixels: env.CAST_PRETRANSCODE_MAX_PIXELS ? Number(env.CAST_PRETRANSCODE_MAX_PIXELS) : null,
    streamRequestTimeoutMs: Number(env.CAST_STREAM_REQUEST_TIMEOUT_MS || 20000) || 20000,
    verifyPlaybackTimeoutMs: Number(env.CAST_VERIFY_PLAYBACK_TIMEOUT_MS || 30000) || 30000,
    maxAutoFallbackAttempts: Number(env.CAST_MAX_AUTO_FALLBACK || 4) || 4,
    watchdogIntervalMs: Number(env.CAST_WATCHDOG_INTERVAL_MS || 5000) || 5000,
  };
}

function redactConfigForDiagnostics(castConfig, cfg = {}) {
  return {
    ...castConfig,
    sshHost: cfg.sshHost ? '[redacted]' : undefined,
    sshUser: cfg.sshUser ? '[redacted]' : undefined,
  };
}

module.exports = {
  loadCastConfig,
  parseBool,
  parseList,
  redactConfigForDiagnostics,
};
