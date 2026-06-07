/** Playback modes drive Chromecast `/api/cast`: server prefers live fMP4 for non-direct modes; disk pre-transcode is opt-in (`allowPretranscode`). Live full-transcode H.264 encoder is chosen on the cast-manager host (`CAST_LIVE_TRANSCODE_ENCODER`). */
const path = require('path');
const crypto = require('crypto');
 
function normalizeCodec(value) {
  return String(value || '').trim().toLowerCase();
}
 
function normalizeContainer(formatName) {
  const first = String(formatName || '').split(',').map(s => s.trim().toLowerCase()).filter(Boolean)[0];
  return first || 'unknown';
}
 
function extOf(filePath) {
  return path.extname(String(filePath || '')).toLowerCase();
}
 
function stableHash(input) {
  return crypto.createHash('sha1').update(String(input)).digest('hex');
}

function shEsc(str) {
  return `'${String(str || '').replace(/'/g, "'\\''")}'`;
}
 
function isAudioCodecSafe(codec, profile) {
  const c = normalizeCodec(codec);
  if (!c) return false;
  if ((profile.safeAudio || []).includes(c)) return true;
  return !(profile.incompatibleAudio || []).some(x => c.includes(x));
}
 
function isVideoCodecSafe(codec, profile) {
  const c = normalizeCodec(codec);
  if (!c) return false;
  if ((profile.safeVideo || []).includes(c)) return true;
  if ((profile.riskyVideo || []).includes(c)) return false;
  // Unknown video codec -> treat as risky for chromecast; browser depends on container anyway.
  return profile.unknownVideoIsRisky !== false;
}
 
function isContainerDirectFriendly(analysis, profile) {
  const container = normalizeContainer(analysis.formatName);
  if ((profile.directContainers || []).includes(container)) return true;
  const ext = analysis.ext;
  if ((profile.directExts || []).includes(ext)) return true;
  return false;
}
 
function pickPrimaryVideoStream(videoStreams) {
  if (!Array.isArray(videoStreams) || videoStreams.length === 0) return null;
  // Prefer the first video stream (simple, deterministic)
  return videoStreams[0];
}
 
function pickBestAudioStream(audioStreams, profile) {
  const streams = Array.isArray(audioStreams) ? audioStreams : [];
  if (streams.length === 0) return { stream: null, switched: false, foundCompatible: false };
 
  // Prefer first compatible AAC/MP3, then any compatible
  const prefer = (profile.preferredAudio || []).map(normalizeCodec);
  const compatible = streams.filter(s => isAudioCodecSafe(s.codec, profile));
  let chosen = null;
 
  for (const pc of prefer) {
    const m = compatible.find(s => normalizeCodec(s.codec) === pc);
    if (m) { chosen = m; break; }
  }
  if (!chosen) chosen = compatible[0] || null;
 
  if (chosen) {
    return {
      stream: chosen,
      switched: chosen.index !== streams[0].index,
      foundCompatible: true,
    };
  }
 
  // None compatible: keep a:0 as the default "selected" for planning
  return { stream: streams[0], switched: false, foundCompatible: false };
}
 
function buildProfiles() {
  return {
    browser: {
      name: 'browser',
      directContainers: ['mp4', 'mov', 'webm', 'ogg', 'wav', 'mp3', 'matroska'],
      directExts: ['.mp4', '.m4v', '.webm', '.mp3', '.m4a', '.wav', '.ogg', '.opus', '.aac'],
      safeVideo: ['h264', 'av1', 'vp8', 'vp9'],
      riskyVideo: ['hevc', 'h265', 'x265', 'mpeg2video', 'vc1'],
      unknownVideoIsRisky: false,
      preferredAudio: ['aac', 'mp3', 'opus', 'vorbis'],
      safeAudio: ['aac', 'mp3', 'opus', 'vorbis'],
      incompatibleAudio: ['ac3', 'eac3', 'dts', 'truehd', 'dts_hd_ma', 'dts_hd', 'mlp', 'flac', 'pcm_s16le', 'pcm_s24le'],
      preferMp4ForRemux: true,
    },
    chromecast: {
      name: 'chromecast',
      directContainers: ['mp4', 'mov', 'webm'],
      directExts: ['.mp4', '.m4v', '.webm'],
      safeVideo: ['h264'],
      riskyVideo: ['hevc', 'h265', 'x265', 'mpeg2video', 'vc1', 'av1'],
      unknownVideoIsRisky: true,
      preferredAudio: ['aac', 'mp3'],
      safeAudio: ['aac', 'mp3'],
      incompatibleAudio: ['ac3', 'eac3', 'dts', 'truehd', 'dts_hd_ma', 'dts_hd', 'mlp', 'flac', 'pcm_s16le', 'pcm_s24le'],
      preferMp4ForRemux: true,
    },
    airplay: {
      name: 'airplay',
      directContainers: ['mp4', 'mov', 'mpegts', 'hls'],
      directExts: ['.mp4', '.m4v', '.mov', '.m3u8'],
      safeVideo: ['h264'],
      riskyVideo: ['hevc', 'h265', 'x265', 'mpeg2video', 'vc1', 'av1', 'vp8', 'vp9'],
      unknownVideoIsRisky: true,
      preferredAudio: ['aac', 'alac', 'mp3'],
      safeAudio: ['aac', 'mp3', 'alac'],
      incompatibleAudio: ['ac3', 'eac3', 'dts', 'truehd', 'dts_hd_ma', 'dts_hd', 'mlp', 'flac', 'opus', 'vorbis', 'pcm_s16le', 'pcm_s24le'],
      preferMp4ForRemux: true,
    },
  };
}
 
async function probeMedia(filePath, { sshExec } = {}) {
  if (!sshExec) throw new Error('probeMedia requires sshExec');
  const p = String(filePath || '');
  const { stdout } = await sshExec(
    `ffprobe -v error -print_format json -show_format -show_streams -show_chapters ${shEsc(p)} 2>/dev/null`
  );
  const raw = JSON.parse(stdout || '{}');
  const streams = Array.isArray(raw.streams) ? raw.streams : [];
  const formatName = String(raw.format?.format_name || '');
  const duration = parseFloat(raw.format?.duration) || 0;
 
  const videoStreams = streams
    .filter(s => s.codec_type === 'video')
    .map(s => ({
      index: Number(s.index),
      codec: normalizeCodec(s.codec_name),
      profile: s.profile ? String(s.profile) : undefined,
      width: Number(s.width) || 0,
      height: Number(s.height) || 0,
      pixFmt: s.pix_fmt ? String(s.pix_fmt) : undefined,
      level: s.level != null ? Number(s.level) : undefined,
      startTime: s.start_time != null ? parseFloat(s.start_time) : null,
      duration: s.duration != null ? parseFloat(s.duration) : null,
      timeBase: s.time_base ? String(s.time_base) : undefined,
      avgFrameRate: s.avg_frame_rate ? String(s.avg_frame_rate) : undefined,
      rFrameRate: s.r_frame_rate ? String(s.r_frame_rate) : undefined,
    }))
    .filter(s => Number.isFinite(s.index));
 
  const audioStreams = streams
    .filter(s => s.codec_type === 'audio')
    .map(s => ({
      index: Number(s.index),
      codec: normalizeCodec(s.codec_name),
      channels: Number(s.channels) || 0,
      startTime: s.start_time != null ? parseFloat(s.start_time) : null,
      duration: s.duration != null ? parseFloat(s.duration) : null,
      timeBase: s.time_base ? String(s.time_base) : undefined,
      language: s.tags?.language ? String(s.tags.language).toLowerCase() : undefined,
    }))
    .filter(s => Number.isFinite(s.index));
 
  const subtitleStreams = streams
    .filter(s => s.codec_type === 'subtitle')
    .map(s => ({
      index: Number(s.index),
      codec: normalizeCodec(s.codec_name),
      language: s.tags?.language ? String(s.tags.language).toLowerCase() : undefined,
    }))
    .filter(s => Number.isFinite(s.index));
 
  return {
    container: normalizeContainer(formatName),
    formatName,
    duration,
    ext: extOf(p),
    videoStreams,
    audioStreams,
    subtitleStreams,
    chapters: Array.isArray(raw.chapters) ? raw.chapters.length : 0,
  };
}
 
function buildFfmpegPlan({ playbackMode, analysis, targetProfile, selectedVideo, selectedAudio } = {}) {
  const outContainer = targetProfile?.preferMp4ForRemux ? 'mp4' : 'matroska';
 
  if (playbackMode === 'direct' || playbackMode === 'unsupported') return null;
 
  if (playbackMode === 'remux') {
    return {
      container: outContainer,
      video: 'copy',
      audio: 'copy',
      selectedVideoStream: selectedVideo?.index ?? null,
      selectedAudioStream: selectedAudio?.index ?? null,
    };
  }
 
  if (playbackMode === 'audio-transcode') {
    return {
      container: outContainer,
      video: 'copy',
      audio: 'aac',
      selectedVideoStream: selectedVideo?.index ?? null,
      selectedAudioStream: selectedAudio?.index ?? null,
    };
  }
 
  if (playbackMode === 'full-transcode') {
    return {
      container: outContainer,
      video: 'h264',
      audio: 'aac',
      selectedVideoStream: selectedVideo?.index ?? null,
      selectedAudioStream: selectedAudio?.index ?? null,
    };
  }
 
  return null;
}
 
function computePlaybackDecision({ analysis, targetProfile, autoTranscode } = {}) {
  const reasons = [];
  const selectedVideo = pickPrimaryVideoStream(analysis.videoStreams);
  const audioPick = pickBestAudioStream(analysis.audioStreams, targetProfile);
  const selectedAudio = audioPick.stream;
 
  const videoCodec = selectedVideo?.codec || null;
  const audioCodec = selectedAudio?.codec || null;
 
  const hasVideo = !!selectedVideo;
  const hasAudio = !!selectedAudio;
 
  const videoSafe = hasVideo ? isVideoCodecSafe(videoCodec, targetProfile) : true;
  const audioSafe = hasAudio ? isAudioCodecSafe(audioCodec, targetProfile) : true;
  const containerDirectFriendly = isContainerDirectFriendly(analysis, targetProfile);
  const isMkv = analysis.container === 'matroska' || analysis.ext === '.mkv';
  const vStart = selectedVideo?.startTime;
  const aStart = selectedAudio?.startTime;
  const startDelta = Number.isFinite(vStart) && Number.isFinite(aStart) ? Math.abs(vStart - aStart) : 0;
  const hasTimestampRisk = isMkv || startDelta > 0.05 || (Number.isFinite(vStart) && vStart < 0) || (Number.isFinite(aStart) && aStart < 0);
 
  // Track switching intent: if we need a non-a:0 stream, we must remux/transcode (cannot "select" in direct path).
  const needsAudioSwitch = !!audioPick.switched;
 
  if (!hasVideo && !hasAudio) {
    return { playbackMode: 'unsupported', reasons: ['No playable audio/video streams found'], selectedVideo, selectedAudio, audioPick };
  }
 
  if (hasVideo && !videoSafe) {
    reasons.push(`Video codec ${String(videoCodec || 'unknown')} is risky/unsupported for ${targetProfile.name}`);
    if (autoTranscode === 'never') {
      return { playbackMode: 'unsupported', reasons: [...reasons, 'autoTranscode=never'], selectedVideo, selectedAudio, audioPick };
    }
    return { playbackMode: 'full-transcode', reasons, selectedVideo, selectedAudio, audioPick };
  }
 
  if (hasAudio && !audioSafe) {
    reasons.push(`Audio codec ${String(audioCodec || 'unknown')} is incompatible for ${targetProfile.name}`);
    if (autoTranscode === 'never') {
      return { playbackMode: 'unsupported', reasons: [...reasons, 'autoTranscode=never'], selectedVideo, selectedAudio, audioPick };
    }
    // If any compatible audio exists, choose that and remux rather than transcode.
    if (audioPick.foundCompatible) {
      reasons.push('Found compatible alternate audio stream; will remux to select it');
      return { playbackMode: 'remux', reasons, selectedVideo, selectedAudio, audioPick };
    }
    return { playbackMode: 'audio-transcode', reasons, selectedVideo, selectedAudio, audioPick };
  }
 
  if (needsAudioSwitch) {
    reasons.push('Will remux to select compatible alternate audio stream');
    return { playbackMode: 'remux', reasons, selectedVideo, selectedAudio, audioPick };
  }
 
  if (isMkv && targetProfile.name !== 'browser') {
    reasons.push('MKV container is not sent directly to receivers; timestamps/audio are normalized first');
    return { playbackMode: hasVideo && !videoSafe ? 'full-transcode' : 'audio-transcode', reasons, selectedVideo, selectedAudio, audioPick, timestampRisk: hasTimestampRisk };
  }

  if (!containerDirectFriendly && (analysis.container === 'matroska' || analysis.ext === '.mkv')) {
    reasons.push(`Container ${analysis.container} not preferred; remux to ${targetProfile.preferMp4ForRemux ? 'mp4' : 'matroska'}`);
    if (autoTranscode === 'never') {
      return { playbackMode: 'remux', reasons, selectedVideo, selectedAudio, audioPick };
    }
    return { playbackMode: 'remux', reasons, selectedVideo, selectedAudio, audioPick };
  }

  if (hasTimestampRisk && targetProfile.name !== 'browser') {
    reasons.push(`Timestamp start delta ${startDelta.toFixed(3)}s; normalize before casting`);
    return { playbackMode: hasAudio ? 'audio-transcode' : 'remux', reasons, selectedVideo, selectedAudio, audioPick, timestampRisk: hasTimestampRisk };
  }
 
  return { playbackMode: 'direct', reasons, selectedVideo, selectedAudio, audioPick };
}
 
async function analyzeMediaCompatibility(filePath, target = 'chromecast', options = {}, { sshExec } = {}) {
  const autoTranscode = String(options.autoTranscode || 'auto').toLowerCase();
  const profiles = buildProfiles();
  const targetProfile = profiles[target] || profiles.chromecast;
 
  const analysis = await probeMedia(filePath, { sshExec });
  const decision = computePlaybackDecision({ analysis, targetProfile, autoTranscode });
  const selectedVideo = decision.selectedVideo;
  const selectedAudio = decision.selectedAudio;
 
  const ffmpegPlan = buildFfmpegPlan({
    playbackMode: decision.playbackMode,
    analysis,
    targetProfile,
    selectedVideo,
    selectedAudio,
  });
 
  const subtitleCodecs = (analysis.subtitleStreams || []).map(s => s.codec).filter(Boolean);
 
  const planKey = stableHash(JSON.stringify({
    v: 2,
    filePath: String(filePath || ''),
    target: targetProfile.name,
    autoTranscode,
    playbackMode: decision.playbackMode,
    ffmpegPlan,
    selectedVideoStreamIndex: selectedVideo?.index ?? null,
    selectedAudioStreamIndex: selectedAudio?.index ?? null,
    container: analysis.container,
    ext: analysis.ext,
  }));
 
  return {
    success: true,
    target: targetProfile.name,
    filePath: String(filePath || ''),
    ext: analysis.ext,
    container: analysis.container,
    formatName: analysis.formatName,
    duration: analysis.duration,
    videoCodec: selectedVideo?.codec || null,
    videoStreamIndex: selectedVideo?.index ?? null,
    videoWidth: selectedVideo?.width || 0,
    videoHeight: selectedVideo?.height || 0,
    audioCodec: selectedAudio?.codec || null,
    audioStreamIndex: selectedAudio?.index ?? null,
    audioStreamWasSwitched: !!decision.audioPick?.switched,
    videoStartTime: selectedVideo?.startTime ?? null,
    audioStartTime: selectedAudio?.startTime ?? null,
    startTimeDelta: Number.isFinite(selectedVideo?.startTime) && Number.isFinite(selectedAudio?.startTime)
      ? Math.abs(selectedVideo.startTime - selectedAudio.startTime)
      : 0,
    timestampRisk: !!decision.timestampRisk,
    subtitleCodecs,
    playbackMode: decision.playbackMode,
    reasons: decision.reasons || [],
    ffmpegPlan,
    planKey,
  };
}
 
module.exports = {
  probeMedia,
  analyzeMediaCompatibility,
  buildProfiles,
  stableHash,
};
