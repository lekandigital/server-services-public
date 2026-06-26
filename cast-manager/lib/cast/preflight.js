const { isLocalhostHost } = require('../media/urls');

const CHECK_META = {
  server_lan_url: { stage: 'server-url', blocking: true, label: 'Server LAN URL' },
  stream_url_not_localhost: { stage: 'stream-url', blocking: true, label: 'Stream URL reachable by TV' },
  stream_url_valid: { stage: 'stream-url', blocking: true, label: 'Stream URL format' },
  receiver_reachable: { stage: 'receiver', blocking: true, label: 'Receiver responds to catt' },
  catt_available: { stage: 'catt', blocking: true, label: 'catt installed' },
  receiver_visible: { stage: 'receiver', blocking: false, label: 'Receiver visible in scan' },
  host_can_fetch_stream: { stage: 'media', blocking: false, label: 'Server can fetch stream URL' },
  stream_range_support: { stage: 'media', blocking: false, label: 'Stream range support' },
  stream_content_type: { stage: 'media', blocking: false, label: 'Stream content type' },
  subtitle_reachable: { stage: 'media', blocking: false, label: 'Subtitle URL reachable' },
  ffmpeg_available: { stage: 'media', blocking: false, label: 'ffmpeg available' },
  vlc_available: { stage: 'media', blocking: false, label: 'VLC available' },
  adb_connected: { stage: 'adb', blocking: false, label: 'ADB connected' },
  adb_usb_serial: { stage: 'adb', blocking: false, label: 'ADB USB serial' },
  adb_device_model: { stage: 'adb', blocking: false, label: 'ADB device model' },
  tv_can_ping_server: { stage: 'device', blocking: false, label: 'TV can ping server' },
};

function redactEnv(env = process.env) {
  const keys = [
    'PORT', 'CAST_PUBLIC_BASE_URL', 'SERVER_PUBLIC_URL', 'PUBLIC_BASE_URL',
    'CHROMECAST_NAME', 'CATT_PATH', 'CAST_BACKEND_DEFAULT', 'CAST_BACKEND_ORDER',
    'CAST_ENABLE_VLC_BACKEND', 'CAST_ENABLE_HLS_BACKEND', 'CAST_LIVE_TRANSCODE_ENCODER',
    'CAST_SUBTITLE_DEFAULT', 'CAST_SUBTITLE_BURN_IN_FALLBACK', 'CAST_ADB_ENABLED', 'CAST_ADB_SERIAL',
  ];
  const out = {};
  for (const key of keys) out[key] = env[key] != null ? String(env[key]) : '';
  return out;
}

function classifyStreamUrl(streamUrl) {
  if (!streamUrl) return 'none';
  try {
    const u = new URL(streamUrl);
    const p = u.pathname || '';
    if (p.includes('/api/cast/live/')) return 'ffmpeg-live';
    if (p.includes('/api/cast/vlc/')) return 'vlc';
    if (p.includes('/api/cast/jobs/') && p.endsWith('.m3u8')) return 'hls';
    if (p.includes('/stream/')) return 'direct';
    return 'other';
  } catch (_) {
    return 'invalid';
  }
}

function enrichCheck(check) {
  const meta = CHECK_META[check.name] || { stage: 'media', blocking: false, label: check.name };
  return {
    ...check,
    stage: meta.stage,
    blocking: meta.blocking,
    label: meta.label,
  };
}

function evaluatePreflightResult(checks) {
  const enriched = checks.map(enrichCheck);
  const failed = enriched.filter((c) => !c.ok);
  const blockingFailures = failed.filter((c) => c.blocking);
  const warnings = failed.filter((c) => !c.blocking);
  const primary = blockingFailures[0] || null;
  return {
    ok: blockingFailures.length === 0,
    blocking: blockingFailures.length > 0,
    checks: enriched,
    failedChecks: failed,
    blockingFailures,
    warnings,
    stage: primary?.stage || null,
    message: primary
      ? `${primary.label}: ${primary.detail || 'check failed'}`
      : (warnings.length ? 'Preflight passed with warnings' : 'Preflight passed'),
    suggestedFix: primary?.suggestion || warnings[0]?.suggestion || '',
    details: primary
      ? { check: primary.name, detail: primary.detail, stage: primary.stage }
      : { warningCount: warnings.length },
  };
}

function buildPreflightResponse(evaluated, extra = {}) {
  if (evaluated.ok) {
    return {
      success: true,
      stage: evaluated.warnings.length ? 'warn' : 'ok',
      blocking: false,
      message: evaluated.message,
      details: evaluated.details,
      suggestedFix: evaluated.suggestedFix || undefined,
      warnings: evaluated.warnings,
      checks: evaluated.checks,
      ...extra,
    };
  }
  const primary = evaluated.blockingFailures[0];
  return {
    success: false,
    stage: primary?.stage || 'media',
    blocking: true,
    message: evaluated.message,
    details: evaluated.details,
    suggestedFix: evaluated.suggestedFix,
    warnings: evaluated.warnings,
    checks: evaluated.checks,
    failedChecks: evaluated.failedChecks,
    blockingFailures: evaluated.blockingFailures,
    ...extra,
  };
}

async function runPreflight({
  req,
  cfg,
  castConfig,
  sshExec,
  getReceiverBaseUrl,
  streamUrl = null,
  subtitleUrl = null,
  backend = 'auto',
  deviceName = null,
  diagnostics = null,
} = {}) {
  const checks = [];
  const add = (name, ok, detail = '', suggestion = '') => {
    checks.push({ name, ok, detail, suggestion });
  };

  const baseUrl = getReceiverBaseUrl(req, cfg);
  let baseHost = '';
  try { baseHost = new URL(baseUrl).hostname; } catch (_) {}
  const localhostBase = isLocalhostHost(baseHost);
  add(
    'server_lan_url',
    !localhostBase,
    baseUrl,
    localhostBase ? 'Set CAST_PUBLIC_BASE_URL to your LAN IP (e.g. http://192.168.x.x:8004)' : '',
  );

  if (streamUrl) {
    try {
      const u = new URL(streamUrl);
      add(
        'stream_url_not_localhost',
        !isLocalhostHost(u.hostname),
        streamUrl,
        isLocalhostHost(u.hostname) ? 'Stream URL must be LAN-reachable by the TV' : '',
      );
      add('stream_url_valid', true, streamUrl, '');
    } catch (err) {
      add('stream_url_valid', false, streamUrl, err.message);
    }
  }

  const target = deviceName || castConfig.chromecastName;
  const cattPath = castConfig.cattPath || 'catt';

  // catt binary + direct receiver probe (more reliable than scan alone)
  try {
    const which = await sshExec(`command -v ${JSON.stringify(cattPath)} 2>/dev/null || echo missing`, 5000);
    const cattOk = !String(which.stdout || '').includes('missing');
    add('catt_available', cattOk, cattOk ? cattPath : 'catt not found', cattOk ? '' : 'Install catt on the server and set CATT_PATH');

    if (cattOk && target) {
      const statusCmd = `${cattPath} -d ${JSON.stringify(target)} status 2>&1`;
      const { stdout, stderr, code } = await sshExec(statusCmd, 12000);
      const text = `${stdout}\n${stderr}`.trim();
      const reachable = code === 0 || /State:|Nothing is currently playing|Volume:/i.test(text);
      add(
        'receiver_reachable',
        reachable,
        text.slice(0, 500),
        reachable ? '' : `catt cannot talk to "${target}". Verify CHROMECAST_NAME and that the TV is on the LAN.`,
      );
    } else if (!target) {
      add('receiver_reachable', false, 'No device name configured', 'Select a Chromecast device first');
    }
  } catch (err) {
    add('catt_available', false, err.message, 'Install catt on the server');
    add('receiver_reachable', false, err.message, 'Verify catt and CHROMECAST_NAME');
  }

  // Receiver scan — informational only (Android TV names may not appear in scan)
  try {
    const scanCmd = `${cattPath} scan 2>&1`;
    const { stdout, stderr, code } = await sshExec(scanCmd, 15000);
    const text = `${stdout}\n${stderr}`;
    const found = text.toLowerCase().includes(String(target || '').toLowerCase());
    add(
      'receiver_visible',
      found,
      text.trim().slice(0, 500),
      found ? '' : `Device "${target}" not listed in catt scan (may still work if catt -d succeeds)`,
    );
  } catch (err) {
    add('receiver_visible', false, err.message, 'catt scan failed — not fatal if receiver_reachable passed');
  }

  // Stream URL probe — skip blocking checks for live/incremental backends
  const streamKind = classifyStreamUrl(streamUrl);
  const skipStreamProbe = ['ffmpeg-live', 'vlc'].includes(streamKind);
  if (streamUrl && !skipStreamProbe) {
    try {
      const probeUrl = streamKind === 'hls' ? streamUrl : streamUrl;
      const curlCmd = streamKind === 'hls'
        ? `curl -sS -o /dev/null -w '%{http_code} %{content_type}' --max-time 8 ${JSON.stringify(probeUrl)} 2>&1 || true`
        : `curl -sS -o /dev/null -w '%{http_code} %{content_type}' --max-time 8 -r 0-65535 ${JSON.stringify(probeUrl)} 2>&1 || true`;
      const { stdout } = await sshExec(curlCmd, 12000);
      const parts = String(stdout || '').trim().split(/\s+/);
      const status = parseInt(parts[0], 10);
      const contentType = parts[1] || '';
      const fetchOk = status === 200 || status === 206;
      add(
        'host_can_fetch_stream',
        fetchOk,
        stdout.trim(),
        !fetchOk ? (status >= 400 ? 'Server cannot fetch its own stream URL' : 'Stream probe timed out or returned unexpected status') : '',
      );
      add(
        'stream_range_support',
        status === 206 || status === 200,
        `HTTP ${status}`,
        status !== 206 && status !== 200 ? 'Range request should return 206 or 200 for direct files' : '',
      );
      if (contentType) add('stream_content_type', !!contentType, contentType, '');
    } catch (err) {
      add('host_can_fetch_stream', false, err.message, 'curl failed on server');
    }
  } else if (streamUrl && skipStreamProbe) {
    add(
      'host_can_fetch_stream',
      true,
      `Skipped probe for ${streamKind} stream (live endpoint; TV will request after cast)`,
      '',
    );
    add('stream_range_support', true, `Skipped for ${streamKind}`, '');
  }

  if (subtitleUrl) {
    try {
      const curlCmd = `curl -sS -o /dev/null -w '%{http_code} %{content_type}' --max-time 8 ${JSON.stringify(subtitleUrl)} 2>&1 || true`;
      const { stdout } = await sshExec(curlCmd, 12000);
      add('subtitle_reachable', /200|206/.test(stdout), stdout.trim(), 'Subtitle URL must be reachable from the LAN');
    } catch (err) {
      add('subtitle_reachable', false, err.message, '');
    }
  }

  if (['ffmpeg-live', 'hls', 'pretranscode', 'auto'].includes(backend)) {
    try {
      const { stdout } = await sshExec('command -v ffmpeg && ffmpeg -version | head -1', 8000);
      add('ffmpeg_available', /ffmpeg/.test(stdout), stdout.trim(), 'Install ffmpeg on the server');
    } catch (err) {
      add('ffmpeg_available', false, err.message, '');
    }
  }

  if (backend === 'vlc' || backend === 'auto') {
    try {
      const { stdout } = await sshExec('command -v cvlc >/dev/null 2>&1 && echo ok || echo missing', 5000);
      const ok = String(stdout).includes('ok');
      add('vlc_available', ok || backend !== 'vlc', ok ? 'cvlc found' : 'cvlc missing', backend === 'vlc' ? 'apt install vlc' : '');
    } catch (err) {
      add('vlc_available', backend !== 'vlc', err.message, '');
    }
  }

  if (castConfig.adbEnabled) {
    try {
      const { resolveAdbUsbSerial, runAdbCommand } = require('./adb');
      const serial = await resolveAdbUsbSerial(sshExec, castConfig.adbSerial);
      const { stdout } = await sshExec('adb devices -l 2>&1 | tail -n +2', 8000);
      const lines = String(stdout || '').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('*'));
      const connected = lines.some((l) => /\bdevice\b/.test(l));
      add('adb_connected', connected, stdout.trim(), connected ? '' : 'Plug Android TV into Ubuntu via USB and authorize debugging');
      if (serial) {
        add('adb_usb_serial', true, serial, castConfig.adbSerial ? '' : 'Auto-selected USB ADB device');
        const { stdout: model } = await runAdbCommand(sshExec, serial, 'shell getprop ro.product.model 2>&1', 5000).catch(() => ({ stdout: '' }));
        add('adb_device_model', !!String(model).trim(), String(model).trim(), '');
      } else if (connected) {
        add('adb_usb_serial', false, 'Multiple devices; set CAST_ADB_SERIAL', 'Use USB serial e.g. CAST_ADB_SERIAL=14291HFDD2RTE3');
      }
      if (connected && serial && baseHost && !localhostBase) {
        const { stdout: pingOut } = await runAdbCommand(sshExec, serial, `shell ping -c 1 -W 2 ${baseHost} 2>&1 | tail -1`, 10000).catch(() => ({ stdout: 'ping unavailable' }));
        add('tv_can_ping_server', /1 received|1 packets received|bytes from|^\d+ bytes from|rtt min/i.test(pingOut), pingOut.trim(), 'TV may not reach the stream host');
      }
    } catch (err) {
      add('adb_connected', false, err.message, 'Install adb on the Ubuntu server');
    }
  }

  const evaluated = evaluatePreflightResult(checks);
  const result = {
    ...evaluated,
    baseUrl,
    env: redactEnv(),
    at: new Date().toISOString(),
    streamKind,
  };

  if (diagnostics?.getOrCreateSession) {
    const session = diagnostics.getOrCreateSession(diagnostics.activeSessionId);
    if (session) session.preflight = result;
  }
  return result;
}

module.exports = {
  buildPreflightResponse,
  evaluatePreflightResult,
  redactEnv,
  runPreflight,
};
