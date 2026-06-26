const BACKEND_ALIASES = {
  auto: 'auto',
  'simple-direct': 'simple-direct',
  simple: 'simple-direct',
  direct: 'direct',
  'direct-only': 'direct',
  url: 'direct',
  'ffmpeg-live': 'ffmpeg-live',
  ffmpeg: 'ffmpeg-live',
  live: 'ffmpeg-live',
  hls: 'hls',
  compat: 'hls',
  compatibility: 'hls',
  vlc: 'vlc',
  pretranscode: 'pretranscode',
  'full-transcode': 'pretranscode',
};

function normalizeBackend(value) {
  const raw = String(value || 'auto').toLowerCase().trim();
  return BACKEND_ALIASES[raw] || raw;
}

function backendEnabled(name, castConfig, probes = {}) {
  if (name === 'simple-direct' || name === 'direct') return true;
  if (name === 'ffmpeg-live') return probes.ffmpeg !== false;
  if (name === 'hls') return castConfig.enableHlsBackend;
  if (name === 'vlc') return castConfig.enableVlcBackend && probes.vlc;
  if (name === 'pretranscode') return probes.ffmpeg !== false;
  return true;
}

function scoreBackend(backend, {
  analysis = {},
  deviceProfile = {},
  castConfig = {},
  probes = {},
  subtitleMode = 'off',
  userSelected = false,
} = {}) {
  if (!backendEnabled(backend, castConfig, probes)) return -1000;

  let score = 50;
  const reasons = [];

  const directSafe = analysis.playbackMode === 'direct' && !analysis.timestampRisk;
  const videoCodec = String(analysis.videoCodec || '').toLowerCase();
  const audioCodec = String(analysis.audioCodec || '').toLowerCase();
  const container = String(analysis.container || '').toLowerCase();

  if (backend === 'simple-direct') {
    score = 30;
    reasons.push('Baseline HTTP cast without analysis');
  }

  if (backend === 'direct') {
    if (directSafe) { score += 40; reasons.push('Media is H.264/AAC-friendly for direct HTTP'); }
    else { score -= 20; reasons.push('Media may need remux/transcode for direct'); }
    if (deviceProfile.knownWorkingBackends?.includes('direct')) { score += 25; reasons.push('Direct worked on this receiver before'); }
    if (deviceProfile.knownFailingBackends?.includes('direct')) { score -= 35; reasons.push('Direct failed on this receiver before'); }
    if (subtitleMode === 'burn-in') { score -= 30; reasons.push('Burn-in not supported on direct'); }
  }

  if (backend === 'ffmpeg-live') {
    if (analysis.playbackMode === 'remux' || analysis.playbackMode === 'audio-transcode') { score += 25; reasons.push('Good for remux/audio-transcode'); }
    if (analysis.playbackMode === 'full-transcode') { score += 15; reasons.push('Supports full transcode'); }
    if (subtitleMode === 'burn-in') { score += 20; reasons.push('Burn-in subtitles supported'); }
    if (deviceProfile.knownWorkingBackends?.includes('ffmpeg-live')) score += 15;
    score -= 10; reasons.push('Higher CPU than direct');
  }

  if (backend === 'hls') {
    if (container === 'matroska' || analysis.timestampRisk) { score += 20; reasons.push('HLS stabilizes MKV/timestamp issues'); }
    if (deviceProfile.knownWorkingBackends?.includes('hls')) { score += 20; reasons.push('HLS worked on this receiver before'); }
    if (deviceProfile.knownFailingBackends?.includes('hls')) { score -= 25; }
    score -= 5; reasons.push('HLS startup slower than direct');
  }

  if (backend === 'vlc') {
    if (probes.vlc) { score += 5; reasons.push('VLC relay available'); }
    else return -1000;
    if (videoCodec === 'hevc' && analysis.playbackMode === 'full-transcode') { score += 15; reasons.push('HEVC full-transcode fallback'); }
    score -= 15; reasons.push('VLC relay is heavier');
  }

  if (backend === 'pretranscode') {
    score -= 40; reasons.push('Slow disk transcode — last resort');
    if (userSelected) score += 30;
  }

  if (deviceProfile.lastKnownGood?.backend === backend) score += 10;

  if (videoCodec === 'h264' && ['aac', 'mp3'].includes(audioCodec) && container === 'mp4' && backend === 'direct') {
    score += 15;
    reasons.push('Known-good MP4 profile');
  }

  return { backend, score, reasons };
}

function rankBackendsForAuto(ctx = {}) {
  const order = ctx.castConfig?.backendOrder || ['direct', 'hls', 'ffmpeg-live', 'vlc', 'pretranscode'];
  const candidates = ['direct', 'hls', 'ffmpeg-live', 'vlc', 'pretranscode'];
  const ranked = candidates
    .map((backend) => scoreBackend(backend, ctx))
    .filter((row) => row.score > -500)
    .sort((a, b) => b.score - a.score || order.indexOf(a.backend) - order.indexOf(b.backend));
  return ranked;
}

function explainAutoChoice(ranked, chosen) {
  const row = ranked.find((r) => r.backend === chosen) || { reasons: [] };
  return `Auto chose ${chosen} because ${row.reasons.slice(0, 2).join('; ') || 'it had the highest score for this media and receiver'}.`;
}

module.exports = {
  BACKEND_ALIASES,
  backendEnabled,
  explainAutoChoice,
  normalizeBackend,
  rankBackendsForAuto,
  scoreBackend,
};
