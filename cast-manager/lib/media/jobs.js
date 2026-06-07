const path = require('path');
const crypto = require('crypto');
const { Client } = require('ssh2');
const { assertReceiverReachableUrl, buildSegmentBaseUrl, getReceiverBaseUrl, sanitizeUrlForLog } = require('./urls');

function shEsc(str) {
  return `'${String(str || '').replace(/'/g, "'\\''")}'`;
}

function makeJobId(prefix = 'job') {
  return `${prefix}_${crypto.randomBytes(12).toString('hex')}`;
}

function clampSeconds(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

function safeRemoteName(value) {
  return String(value || '').replace(/[^a-zA-Z0-9._-]/g, '');
}

function buildHlsFfmpegCommand({ filePath, analysis, jobDir, startSeconds = 0, mode = 'auto', encoder = 'libx264' }) {
  const playbackMode = analysis?.playbackMode || mode || 'audio-transcode';
  const videoCodec = String(analysis?.videoCodec || '').toLowerCase();
  const shouldFullTranscode = playbackMode === 'full-transcode' || mode === 'full-transcode' || (videoCodec && videoCodec !== 'h264');
  const hasAudio = analysis?.audioStreamIndex != null;
  const hasVideo = analysis?.videoStreamIndex != null;
  const ss = clampSeconds(startSeconds);

  const args = [
    'ffmpeg -hide_banner -nostdin -loglevel info',
    '-fflags +genpts',
    ss > 0 ? `-ss ${ss}` : '',
    `-i ${shEsc(filePath)}`,
    hasVideo ? `-map 0:${analysis.videoStreamIndex}` : '-vn',
    hasAudio ? `-map 0:${analysis.audioStreamIndex}` : '-an',
    '-sn',
    '-dn',
    '-start_at_zero',
    '-avoid_negative_ts make_zero',
    '-max_interleave_delta 0',
    '-muxdelay 0',
    '-muxpreload 0',
  ].filter(Boolean);

  if (hasVideo) {
    if (shouldFullTranscode) {
      if (encoder === 'h264_nvenc') {
        args.push('-c:v h264_nvenc -preset p4 -rc vbr -cq 23 -b:v 0 -pix_fmt yuv420p');
      } else {
        args.push('-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p');
      }
    } else {
      args.push('-c:v copy');
      if (videoCodec === 'h264') args.push('-bsf:v h264_mp4toannexb');
    }
  }

  if (hasAudio) {
    args.push('-c:a aac -b:a 192k -ac 2');
    args.push('-af aresample=async=1:first_pts=0');
  }

  args.push('-f hls');
  args.push(`-hls_time ${Number(process.env.CAST_HLS_SEGMENT_SECONDS || 4) || 4}`);
  args.push(`-hls_list_size ${Number(process.env.CAST_HLS_LIST_SIZE || 0) || 0}`);
  args.push('-hls_flags independent_segments+temp_file');
  args.push('-hls_allow_cache 0');
  args.push('-hls_segment_type mpegts');
  args.push(`-hls_segment_filename ${shEsc(path.posix.join(jobDir, 'segment_%05d.ts'))}`);
  args.push(shEsc(path.posix.join(jobDir, 'master.m3u8')));

  return args.join(' ').replace(/\s+/g, ' ').trim();
}

function summarizeAnalysis(analysis) {
  if (!analysis) return null;
  return {
    target: analysis.target,
    container: analysis.container,
    duration: analysis.duration,
    videoCodec: analysis.videoCodec,
    videoStreamIndex: analysis.videoStreamIndex,
    videoWidth: analysis.videoWidth,
    videoHeight: analysis.videoHeight,
    audioCodec: analysis.audioCodec,
    audioStreamIndex: analysis.audioStreamIndex,
    playbackMode: analysis.playbackMode,
    timestampRisk: analysis.timestampRisk,
    startTimeDelta: analysis.startTimeDelta,
    reasons: analysis.reasons || [],
  };
}

function createHlsJobManager({ sshExec, sshConfig, cfg = {}, resolveEncoder = async () => ({ encoder: 'libx264' }), logger = () => {} }) {
  const jobs = new Map();
  const cacheRoot = String(process.env.TRANSCODE_CACHE_DIR || '/tmp/cast_manager_cache').replace(/\/+$/, '');
  const jobRoot = `${cacheRoot}/jobs`;
  const ttlMs = Number(process.env.CAST_JOB_TTL_MS || 2 * 60 * 60 * 1000);

  function getJob(jobId) {
    const id = safeRemoteName(jobId);
    return id ? jobs.get(id) || null : null;
  }

  async function waitUntilReady(job, timeoutMs = 12000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const playlist = path.posix.join(job.jobDir, 'master.m3u8');
      const firstSeg = path.posix.join(job.jobDir, 'segment_00000.ts');
      const { stdout } = await sshExec(`if test -s ${shEsc(playlist)} && ls ${shEsc(job.jobDir)}/segment_*.ts >/dev/null 2>&1; then echo ready; elif ! kill -0 ${Number(job.pid)} >/dev/null 2>&1; then echo dead; else echo wait; fi`, 5000);
      if (String(stdout).includes('ready')) {
        job.state = 'ready';
        return true;
      }
      if (String(stdout).includes('dead')) {
        const log = await readJobLog(job.jobId, 80).catch(() => '');
        job.state = 'failed';
        job.lastError = log.split('\n').find(Boolean) || 'ffmpeg exited before HLS became ready';
        return false;
      }
      await new Promise((r) => setTimeout(r, 500));
      await sshExec(`test -e ${shEsc(firstSeg)} && true || true`, 2000).catch(() => {});
    }
    job.state = 'starting';
    return false;
  }

  async function startHlsJob({ req, filePath, analysis, startSeconds = 0, title, mode = 'auto', provider = 'chromecast' }) {
    const jobId = makeJobId('hls');
    const jobDir = `${jobRoot}/${jobId}`;
    const logPath = `${jobDir}/ffmpeg.log`;
    await sshExec(`mkdir -p ${shEsc(jobDir)}`);

    const encoderChoice = analysis?.playbackMode === 'full-transcode'
      ? await resolveEncoder()
      : { encoder: 'libx264' };
    const encoder = encoderChoice?.encoder === 'h264_nvenc' ? 'h264_nvenc' : 'libx264';
    const ffmpegCmd = buildHlsFfmpegCommand({ filePath, analysis, jobDir, startSeconds, mode, encoder });
    const wrapped = `cd ${shEsc(jobDir)} && ${ffmpegCmd} > ${shEsc(logPath)} 2>&1`;
    const { stdout } = await sshExec(`nohup setsid bash -lc ${shEsc(wrapped)} </dev/null >/dev/null 2>&1 & echo $!`, 5000);
    const pid = parseInt(String(stdout || '').trim(), 10);
    if (!Number.isFinite(pid) || pid <= 0) throw new Error('Failed to start HLS ffmpeg job');

    const streamUrl = assertReceiverReachableUrl(`${getReceiverBaseUrl(req, cfg)}/api/cast/jobs/${encodeURIComponent(jobId)}/master.m3u8`);
    const job = {
      jobId,
      backend: 'hls',
      provider,
      state: 'starting',
      filePath,
      title,
      analysis: summarizeAnalysis(analysis),
      playbackMode: analysis?.playbackMode || mode,
      pipelineMode: analysis?.playbackMode === 'full-transcode' ? 'hls-full-transcode' : 'hls-audio-transcode',
      startSeconds: clampSeconds(startSeconds),
      streamUrl,
      jobDir,
      logPath,
      pid,
      encoder,
      createdAt: Date.now(),
      expiresAt: Date.now() + ttlMs,
      command: ffmpegCmd.replace(filePath, '<input>'),
    };
    jobs.set(jobId, job);
    logger(`hls job started id=${jobId} pid=${pid} provider=${provider} mode=${job.pipelineMode} url=${sanitizeUrlForLog(streamUrl)}`);
    await waitUntilReady(job);
    return job;
  }

  async function cancelJob(jobId, reason = 'cancelled') {
    const job = getJob(jobId);
    if (!job) return false;
    job.state = 'cancelled';
    job.cancelReason = reason;
    job.cancelledAt = Date.now();
    if (job.pid) {
      const pid = Number(job.pid);
      await sshExec(`kill -TERM -${pid} >/dev/null 2>&1 || kill -TERM ${pid} >/dev/null 2>&1 || true; sleep 0.2; kill -KILL -${pid} >/dev/null 2>&1 || kill -KILL ${pid} >/dev/null 2>&1 || true`, 5000).catch(() => {});
    }
    await sshExec(`rm -rf ${shEsc(job.jobDir)}`, 10000).catch(() => {});
    jobs.delete(job.jobId);
    return true;
  }

  async function readJobLog(jobId, lines = 200) {
    const job = getJob(jobId);
    if (!job) {
      const err = new Error('Unknown job');
      err.status = 404;
      throw err;
    }
    const { stdout } = await sshExec(`test -f ${shEsc(job.logPath)} && tail -n ${Number(lines) || 200} ${shEsc(job.logPath)} || true`, 8000);
    return stdout || '';
  }

  async function getJobInfo(jobId) {
    const job = getJob(jobId);
    if (!job) return null;
    if (job.pid && !['cancelled', 'failed'].includes(job.state)) {
      const { stdout } = await sshExec(`kill -0 ${Number(job.pid)} >/dev/null 2>&1 && echo running || echo stopped`, 3000).catch(() => ({ stdout: 'unknown' }));
      if (String(stdout).includes('stopped') && job.state !== 'ready') job.state = 'ended';
    }
    return {
      ...job,
      filePath: undefined,
      logPath: undefined,
      jobDir: undefined,
    };
  }

  async function servePlaylist(req, res, jobId) {
    const job = getJob(jobId);
    if (!job) return res.status(404).json({ success: false, error: 'Unknown cast job' });
    const playlistPath = path.posix.join(job.jobDir, 'master.m3u8');
    const { stdout } = await sshExec(`test -f ${shEsc(playlistPath)} && cat ${shEsc(playlistPath)} || true`, 8000);
    if (!stdout) return res.status(404).json({ success: false, error: 'Playlist is not ready yet' });

    const segmentBase = buildSegmentBaseUrl(req, cfg, job.jobId);
    const rewritten = String(stdout).split('\n').map((line) => {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) return line;
      if (/^https?:\/\//i.test(trimmed)) return trimmed;
      return `${segmentBase}${encodeURIComponent(path.posix.basename(trimmed))}`;
    }).join('\n');

    res.setHeader('Content-Type', 'application/vnd.apple.mpegurl');
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Access-Control-Allow-Origin', '*');
    return res.send(`${rewritten}\n`);
  }

  async function serveSegment(res, jobId, segmentName) {
    const job = getJob(jobId);
    if (!job) return res.status(404).json({ success: false, error: 'Unknown cast job' });
    const safeName = safeRemoteName(segmentName);
    if (!/^segment_\d{5}\.ts$/.test(safeName)) return res.status(400).json({ success: false, error: 'Invalid segment' });
    const remotePath = path.posix.join(job.jobDir, safeName);

    const conn = new Client();
    conn.on('ready', () => {
      conn.sftp((err, sftp) => {
        if (err) { conn.end(); return res.status(500).end(); }
        const rs = sftp.createReadStream(remotePath);
        res.writeHead(200, {
          'Content-Type': 'video/mp2t',
          'Cache-Control': 'no-store',
          'Access-Control-Allow-Origin': '*',
        });
        rs.pipe(res);
        rs.on('end', () => conn.end());
        rs.on('error', () => { conn.end(); if (!res.headersSent) res.status(404).end(); else res.end(); });
      });
    });
    conn.on('error', () => { if (!res.headersSent) res.status(500).end(); });
    conn.connect(sshConfig);
  }

  async function cleanupExpired() {
    const now = Date.now();
    for (const job of [...jobs.values()]) {
      if (job.expiresAt && job.expiresAt < now) await cancelJob(job.jobId, 'expired');
    }
  }

  async function startupCleanup() {
    await sshExec(`mkdir -p ${shEsc(jobRoot)}; pkill -f ${shEsc(`${jobRoot}/hls_`)} >/dev/null 2>&1 || true; find ${shEsc(jobRoot)} -mindepth 1 -maxdepth 1 -type d -mmin +120 -exec rm -rf {} + 2>/dev/null || true`, 10000).catch(() => {});
  }

  return {
    cancelJob,
    cleanupExpired,
    getJob,
    getJobInfo,
    readJobLog,
    servePlaylist,
    serveSegment,
    startupCleanup,
    startHlsJob,
  };
}

module.exports = {
  buildHlsFfmpegCommand,
  createHlsJobManager,
};
