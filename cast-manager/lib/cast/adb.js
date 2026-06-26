let cachedUsbSerial = null;
let cachedUsbSerialAt = 0;
const USB_SERIAL_CACHE_MS = 60_000;

function adbPrefix(serial) {
  if (!serial) return '';
  const safe = String(serial).replace(/[^a-zA-Z0-9._:-]/g, '');
  return safe ? `adb -s ${safe} ` : '';
}

async function resolveAdbUsbSerial(sshExec, explicitSerial = '', { refresh = false } = {}) {
  const explicit = String(explicitSerial || '').trim();
  if (explicit) return explicit;

  const now = Date.now();
  if (!refresh && cachedUsbSerial && (now - cachedUsbSerialAt) < USB_SERIAL_CACHE_MS) {
    return cachedUsbSerial;
  }

  const { stdout } = await sshExec('adb devices -l 2>&1', 8000);
  const lines = String(stdout || '').split('\n');

  // Prefer USB-attached device (plugged into Ubuntu host).
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('List of')) continue;
    if (/\bdevice\b/.test(trimmed) && /\busb:/.test(trimmed)) {
      const serial = trimmed.split(/\s+/)[0];
      if (serial) {
        cachedUsbSerial = serial;
        cachedUsbSerialAt = now;
        return serial;
      }
    }
  }

  // Fallback: first non-TCP device.
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('List of')) continue;
    if (!/\bdevice\b/.test(trimmed)) continue;
    const serial = trimmed.split(/\s+/)[0];
    if (serial && !serial.includes(':') && !serial.includes('_adb-tls-connect')) {
      cachedUsbSerial = serial;
      cachedUsbSerialAt = now;
      return serial;
    }
  }

  cachedUsbSerial = null;
  cachedUsbSerialAt = 0;
  return '';
}

async function runAdbCommand(sshExec, serial, command, timeout = 12000) {
  const prefix = adbPrefix(serial);
  const adbCmd = command.startsWith('adb ') ? command.replace(/^adb /, prefix || 'adb ') : `${prefix || 'adb '}${command}`;
  return sshExec(adbCmd, timeout);
}

async function collectAdbSnapshot({ sshExec, serial, serverIp = null, resolveSerial = true } = {}) {
  const resolvedSerial = resolveSerial
    ? await resolveAdbUsbSerial(sshExec, serial)
    : serial;
  const snapshot = { serial: resolvedSerial || null, transport: 'usb', commands: {} };
  const cmds = {
    devices: 'adb devices -l 2>&1',
    model: 'shell getprop ro.product.model 2>&1',
    androidVersion: 'shell getprop ro.build.version.release 2>&1',
    wmSize: 'shell wm size 2>&1',
    mediaSession: 'shell dumpsys media_session 2>&1 | head -80',
    activityTop: 'shell dumpsys activity top 2>&1 | head -40',
    castPackages: 'shell pm list packages 2>/dev/null | grep -Ei \'cast|chromecast|google|mediashell|receiver|media\' | head -30',
  };
  if (serverIp) {
    cmds.pingServer = `shell ping -c 1 -W 2 ${serverIp} 2>&1 | tail -2`;
  }

  for (const [key, cmd] of Object.entries(cmds)) {
    try {
      const { stdout, stderr, code } = await runAdbCommand(sshExec, resolvedSerial, cmd);
      snapshot.commands[key] = { stdout: String(stdout || stderr || '').trim(), code };
    } catch (err) {
      snapshot.commands[key] = { error: err.message };
    }
  }
  snapshot.at = new Date().toISOString();
  return snapshot;
}

async function captureLogcatTail(sshExec, serial, lines = 200, filter = 'cast|chromecast|media|player|exoplayer|codec|ffmpeg|http|subtitle|vtt|error|fail') {
  const cmd = `logcat -d -v time 2>&1 | grep -Ei ${JSON.stringify(filter).slice(1, -1)} | tail -n ${lines}`;
  const resolved = await resolveAdbUsbSerial(sshExec, serial);
  const { stdout } = await runAdbCommand(sshExec, resolved, cmd, 20000);
  return stdout || '';
}

async function captureScreenshot(sshExec, serial, outPath) {
  const resolved = await resolveAdbUsbSerial(sshExec, serial);
  const cmd = `exec-out screencap -p > ${JSON.stringify(outPath)} 2>/dev/null && test -s ${JSON.stringify(outPath)} && echo ok`;
  const { stdout } = await runAdbCommand(sshExec, resolved, cmd, 20000);
  return String(stdout || '').includes('ok');
}

function parseMediaSessionPlaying(snapshot) {
  const text = snapshot?.commands?.mediaSession?.stdout || '';
  if (!text) return { playing: null, confidence: 'low' };
  const playing = /state=PLAYING|playbackState=3/i.test(text);
  const paused = /state=PAUSED|playbackState=2/i.test(text);
  if (playing) return { playing: true, confidence: 'medium' };
  if (paused) return { playing: false, confidence: 'medium' };
  return { playing: null, confidence: 'low' };
}

module.exports = {
  captureLogcatTail,
  captureScreenshot,
  collectAdbSnapshot,
  parseMediaSessionPlaying,
  resolveAdbUsbSerial,
  runAdbCommand,
};
