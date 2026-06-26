const path = require('path');
const { analyzeMediaCompatibility } = require('./compatibility');
const { buildTokenStreamUrl, sanitizeUrlForLog } = require('./urls');

function normalizeMode(value) {
  const raw = String(value || 'auto').toLowerCase().trim();
  if (!raw || raw === 'automatic') return 'auto';
  if (['compat', 'compatibility', 'hls', 'safe'].includes(raw)) return 'hls';
  if (['force-full', 'full', 'full-transcode', 'pretranscode'].includes(raw)) return 'full-transcode';
  if (['direct', 'direct-only', 'simple-direct', 'simple'].includes(raw)) return 'direct';
  if (['ffmpeg-live', 'ffmpeg', 'live'].includes(raw)) return 'ffmpeg-live';
  if (['vlc', 'vlc-like'].includes(raw)) return 'vlc';
  return 'auto';
}

function mediaKindFromPath(filePath, fallback = 'video') {
  const ext = path.extname(String(filePath || '')).toLowerCase();
  if (['.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wav', '.opus', '.wma'].includes(ext)) return 'audio';
  if (['.mkv', '.mp4', '.avi', '.mov', '.webm', '.m4v', '.ts', '.flv', '.wmv'].includes(ext)) return 'video';
  return fallback;
}

function isDirectSafe(analysis) {
  return analysis?.playbackMode === 'direct' && !analysis?.timestampRisk;
}

function choosePipelineMode({ analysis, target, requestedMode }) {
  const mode = normalizeMode(requestedMode);
  if (mode === 'vlc') return 'vlc';
  if (mode === 'ffmpeg-live') return 'ffmpeg-live';
  if (mode === 'full-transcode') return 'hls-full-transcode';
  if (mode === 'hls') {
    return analysis?.playbackMode === 'full-transcode' ? 'hls-full-transcode' : 'hls-audio-transcode';
  }
  if (mode === 'direct') return 'direct';

  if (isDirectSafe(analysis)) return 'direct';
  if (analysis?.playbackMode === 'full-transcode') return 'hls-full-transcode';
  if (target === 'airplay') return 'hls-audio-transcode';
  return 'hls-audio-transcode';
}

function summarizePrepared(prepared) {
  return {
    pipelineMode: prepared.pipelineMode,
    playbackMode: prepared.playbackMode,
    backend: prepared.backend,
    target: prepared.target,
    jobId: prepared.jobId || null,
    streamUrl: prepared.streamUrl ? sanitizeUrlForLog(prepared.streamUrl) : null,
    mimeType: prepared.mimeType,
    receiverSeek: prepared.receiverSeek,
    startSeconds: prepared.startSeconds,
    analysis: prepared.analysis,
    reasons: prepared.reasons || [],
  };
}

async function prepareMediaForCast({
  req,
  cfg,
  filePath,
  target = 'chromecast',
  mode = 'auto',
  autoTranscode = 'auto',
  startSeconds = 0,
  hlsJobs,
  generateStreamToken,
  getMimeType = () => 'application/octet-stream',
  sshExec,
  createVlcJob,
  createLiveFfmpegJob,
  skipAnalysis = false,
  logger = () => {},
} = {}) {
  if (!filePath) throw new Error('filePath required');
  const targetName = target === 'airplay' ? 'airplay' : 'chromecast';
  const requestedMode = normalizeMode(mode);
  const analysis = skipAnalysis
    ? { container: path.extname(filePath).slice(1), playbackMode: 'direct', reasons: ['simple-direct skip analysis'] }
    : await analyzeMediaCompatibility(filePath, targetName, { autoTranscode }, { sshExec });
  if (analysis.playbackMode === 'unsupported') {
    const err = new Error('Unsupported media for selected receiver');
    err.status = 415;
    err.reasons = analysis.reasons || [];
    throw err;
  }

  let pipelineMode = choosePipelineMode({ analysis, target: targetName, requestedMode });
  if (requestedMode === 'direct' && !isDirectSafe(analysis)) {
    logger(`direct mode requested for non-direct-safe media; receiver may fail path=${path.basename(filePath)} mode=${analysis.playbackMode}`);
  }

  const mediaKind = mediaKindFromPath(filePath);
  const title = path.basename(filePath);
  const basePrepared = {
    target: targetName,
    filePath,
    title,
    mediaKind,
    analysis: {
      container: analysis.container,
      duration: analysis.duration,
      videoCodec: analysis.videoCodec,
      videoStreamIndex: analysis.videoStreamIndex,
      videoWidth: analysis.videoWidth,
      videoHeight: analysis.videoHeight,
      audioCodec: analysis.audioCodec,
      audioStreamIndex: analysis.audioStreamIndex,
      timestampRisk: analysis.timestampRisk,
      startTimeDelta: analysis.startTimeDelta,
      reasons: analysis.reasons || [],
    },
    reasons: analysis.reasons || [],
    playbackMode: analysis.playbackMode,
    startSeconds: Math.max(0, Math.floor(Number(startSeconds) || 0)),
    duration: analysis.duration || 0,
  };

  if (pipelineMode === 'direct') {
    const { token } = generateStreamToken(filePath, title, 24);
    const streamUrl = buildTokenStreamUrl(req, cfg, token, title, 'raw=1');
    return {
      ...basePrepared,
      backend: 'direct',
      pipelineMode: 'direct',
      streamUrl,
      mimeType: getMimeType(filePath),
      receiverSeek: true,
    };
  }

  if (pipelineMode === 'vlc') {
    if (typeof createVlcJob !== 'function') throw new Error('VLC backend is unavailable in this process');
    const job = await createVlcJob({ req, filePath, startSeconds, title });
    return {
      ...basePrepared,
      backend: 'vlc',
      pipelineMode: 'vlc-compatibility',
      streamUrl: job.streamUrl,
      jobId: job.jobId,
      mimeType: 'video/mp2t',
      receiverSeek: false,
      vlcJob: job,
    };
  }

  if (pipelineMode === 'ffmpeg-live') {
    if (typeof createLiveFfmpegJob !== 'function') throw new Error('FFmpeg live backend is unavailable in this process');
    const job = await createLiveFfmpegJob({ req, filePath, analysis, startSeconds, title });
    return {
      ...basePrepared,
      backend: 'ffmpeg-live',
      pipelineMode: 'ffmpeg-live-fmp4',
      streamUrl: job.streamUrl,
      jobId: job.jobId,
      mimeType: 'video/mp4',
      receiverSeek: false,
      liveJob: job,
    };
  }

  if (['1', 'true', 'yes', 'on'].includes(String(process.env.CAST_DISABLE_HLS_BACKEND || '').toLowerCase())) {
    const err = new Error('HLS backend is disabled by CAST_DISABLE_HLS_BACKEND=1');
    err.status = 409;
    throw err;
  }

  const hlsAnalysis = pipelineMode === 'hls-full-transcode'
    ? { ...analysis, playbackMode: 'full-transcode' }
    : { ...analysis, playbackMode: analysis.playbackMode === 'full-transcode' ? 'full-transcode' : 'audio-transcode' };
  const job = await hlsJobs.startHlsJob({
    req,
    filePath,
    analysis: hlsAnalysis,
    startSeconds,
    title,
    mode: hlsAnalysis.playbackMode,
    provider: targetName,
  });
  return {
    ...basePrepared,
    backend: 'hls',
    pipelineMode: job.pipelineMode,
    playbackMode: hlsAnalysis.playbackMode,
    streamUrl: job.streamUrl,
    jobId: job.jobId,
    mimeType: 'application/vnd.apple.mpegurl',
    receiverSeek: false,
  };
}

module.exports = {
  choosePipelineMode,
  normalizeMode,
  prepareMediaForCast,
  summarizePrepared,
};
