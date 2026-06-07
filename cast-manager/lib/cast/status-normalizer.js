function parseTimeToSeconds(timeStr) {
  if (!timeStr) return 0;
  const parts = String(timeStr).split(':').map(Number);
  if (parts.length === 3) return (parts[0] || 0) * 3600 + (parts[1] || 0) * 60 + (parts[2] || 0);
  if (parts.length === 2) return (parts[0] || 0) * 60 + (parts[1] || 0);
  return parts[0] || 0;
}

function normalizeCastState(raw) {
  const value = String(raw || '').toLowerCase();
  if (!value) return 'unknown';
  if (value.includes('play')) return 'playing';
  if (value.includes('pause')) return 'paused';
  if (value.includes('buffer') || value.includes('load') || value.includes('connect')) return 'buffering';
  if (value.includes('idle') || value.includes('stop')) return 'idle';
  return 'unknown';
}

function sanitizeCastTitle(title) {
  const t = String(title || '').trim();
  if (!t) return '';
  try {
    if (/^https?:\/\//i.test(t)) {
      const u = new URL(t);
      const base = (u.pathname || '').split('/').filter(Boolean).pop() || '';
      return base ? `${u.hostname}/${base}` : u.hostname;
    }
  } catch (_) {}
  return t;
}

function parseCattStatus(output) {
  const text = String(output || '');
  if (text.includes('Nothing is currently playing')) {
    return { state: 'idle', currentTime: 0, duration: 0, volumeLevel: 100, title: '' };
  }
  const timeMatch = text.match(/Time:\s*([\d:.]+)\s*\/\s*([\d:.]+)/i);
  const stateMatch = text.match(/State:\s*(.+)/i);
  const titleMatch = text.match(/Title:\s*(.+)/i);
  const volMatch = text.match(/Volume:\s*(\d+)/i);
  return {
    state: normalizeCastState(stateMatch?.[1] || ''),
    currentTime: timeMatch ? parseTimeToSeconds(timeMatch[1]) : 0,
    duration: timeMatch ? parseTimeToSeconds(timeMatch[2]) : 0,
    title: sanitizeCastTitle(titleMatch?.[1] || ''),
    volumeLevel: volMatch ? parseInt(volMatch[1], 10) : 100,
  };
}

module.exports = {
  normalizeCastState,
  parseCattStatus,
  parseTimeToSeconds,
  sanitizeCastTitle,
};
