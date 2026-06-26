const path = require('path');
const { normalizeBackend, rankBackendsForAuto, explainAutoChoice } = require('./backend-scoring');
const { classifyFailure } = require('./failure-classifier');
const { runPreflight, buildPreflightResponse } = require('./preflight');
const { collectAdbSnapshot, parseMediaSessionPlaying, resolveAdbUsbSerial } = require('./adb');

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function preflightIsBlocking(preflight) {
  if (!preflight) return false;
  return preflight.blocking === true || (Array.isArray(preflight.blockingFailures) && preflight.blockingFailures.length > 0);
}

function formatPreflightFailure(preflight) {
  const structured = buildPreflightResponse(preflight);
  const message = structured.message || 'Preflight blocked casting';
  return {
    error: message,
    stage: structured.stage,
    blocking: true,
    message,
    details: structured.details,
    suggestedFix: structured.suggestedFix,
    warnings: structured.warnings || preflight.warnings || [],
    preflight,
  };
}

function normalizeSubtitleInput(body = {}) {
  const subtitle = body.subtitle || {};
  if (typeof subtitle === 'string') return { mode: subtitle };
  const mode = String(subtitle.mode || body.subtitleMode || 'off').toLowerCase();
  return {
    mode,
    id: subtitle.id || body.subtitleId || null,
    path: subtitle.path || subtitle.url || subtitle.source || body.subtitlePath || body.customSubtitlePath || body.subtitleUrl || null,
    language: subtitle.language || null,
    burnIn: mode === 'burn-in' || body.burnInSubtitles === true,
  };
}

function createCastOrchestrator(deps) {
  const {
    cfg,
    castConfig,
    diagnostics,
    deviceProfiles,
    castSessions,
    getProvider,
    prepareMediaForCast,
    summarizePrepared,
    cleanupPreparedMedia,
    resolveSubtitleForCast,
    sshExec,
    getReceiverBaseUrl,
    probeVlcAvailable,
    createLiveCastJob,
    logger = () => {},
  } = deps;

  async function probeBackends() {
    const vlc = await probeVlcAvailable?.().catch(() => ({ ok: false }));
    let ffmpeg = true;
    try {
      const { stdout } = await sshExec('command -v ffmpeg >/dev/null 2>&1 && echo ok', 5000);
      ffmpeg = String(stdout).includes('ok');
    } catch (_) { ffmpeg = false; }
    return { vlc: !!vlc?.ok, ffmpeg };
  }

  async function prepareForBackend(req, backend, ctx) {
    const modeMap = {
      'simple-direct': 'direct',
      direct: 'direct',
      hls: 'hls',
      vlc: 'vlc',
      pretranscode: 'full-transcode',
      'ffmpeg-live': 'ffmpeg-live',
    };
    const mode = modeMap[backend] || backend;
    if (backend === 'simple-direct') {
      const prepared = await prepareMediaForCast({
        ...ctx.prepareArgs,
        mode: 'direct',
        skipAnalysis: true,
      });
      prepared.backend = 'simple-direct';
      prepared.pipelineMode = 'simple-direct';
      return prepared;
    }
    if (backend === 'ffmpeg-live') {
      const job = await createLiveCastJob({
        req,
        filePath: ctx.filePath,
        analysis: ctx.analysis,
        startSeconds: ctx.startSeconds,
        title: path.basename(ctx.filePath),
        burnInSubtitlePath: ctx.subtitleSelection?.burnInVttPath || ctx.subtitleSelection?.burnInPath || null,
        burnInSubtitleVttPath: ctx.subtitleSelection?.burnInVttPath || null,
      });
      return {
        backend: 'ffmpeg-live',
        pipelineMode: 'ffmpeg-live-fmp4',
        playbackMode: ctx.analysis?.playbackMode,
        streamUrl: job.streamUrl,
        jobId: job.jobId,
        mimeType: 'video/mp4',
        receiverSeek: false,
        startSeconds: ctx.startSeconds,
        duration: ctx.analysis?.duration || 0,
        title: path.basename(ctx.filePath),
        mediaKind: 'video',
        analysis: ctx.analysis,
        reasons: ctx.analysis?.reasons || [],
        liveJob: job,
      };
    }
    return prepareMediaForCast({ ...ctx.prepareArgs, mode });
  }

  async function waitForStreamRequest(sessionId, streamUrl, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const summary = diagnostics.summarizeSession(sessionId);
      if (summary?.session?.tvRequestedStream) return true;
      await sleep(500);
    }
    return false;
  }

  async function verifyPlayback(sessionId, provider, deviceId, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    let lastTime = -1;
    let stagnant = 0;
    while (Date.now() < deadline) {
      const status = await provider.status(deviceId).catch(() => ({ state: 'unknown' }));
      const state = String(status.state || '').toLowerCase();
      if (['playing', 'buffering'].includes(state)) {
        const t = Number(status.currentTime || 0);
        if (t > 0 && t > lastTime) return { ok: true, state, currentTime: t };
        if (t === lastTime) stagnant += 1;
        else stagnant = 0;
        lastTime = t;
        if (stagnant >= 6) break;
      }
      await sleep(1000);
    }
    return { ok: false, state: 'unknown' };
  }

  async function startCast(req, res) {
    let prepared = null;
    let sessionId = null;
    const attemptedBackends = [];

    try {
      const body = req.body || {};
      let { filePath } = body;
      filePath = await deps.assertRemotePathInsideRoot(filePath);
      deps.ensureNotProtectedFile(filePath, 'cast');

      const providerName = deps.normalizeProviderName(body.provider || 'chromecast');
      const provider = getProvider(providerName);
      const selected = body.deviceId
        ? { device_id: body.deviceId, name: body.deviceName || '' }
        : await deps.getSelectedDeviceForProvider(providerName);
      if (!selected?.device_id) {
        return res.status(409).json({ success: false, error: `No ${providerName} device selected. Scan and select a device first.` });
      }
      if (body.deviceId) await provider.selectDevice(body.deviceId);

      sessionId = `cast_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
      diagnostics.setActiveSession(sessionId);
      diagnostics.transitionState(sessionId, 'preflighting', 'Starting cast');

      const seconds = body.seekTo ? deps.parseTimeToSeconds(String(body.seekTo)) : Number(body.startSeconds || 0) || 0;
      const requestedBackend = normalizeBackend(body.backend || body.mode || castConfig.backendDefault);
      const subtitleSelection = await resolveSubtitleForCast(req, filePath, normalizeSubtitleInput(body));
      const autoTranscode = String(body.autoTranscode || 'auto').toLowerCase();

      const analysis = await deps.analyzeMediaCompatibility(filePath, providerName, { autoTranscode }, { sshExec });
      if (analysis.playbackMode === 'unsupported') {
        const failure = classifyFailure({ receiverCodecRejected: true });
        diagnostics.transitionState(sessionId, 'failed', failure.userMessage, { primaryFailureCode: failure.primaryFailureCode });
        return res.status(415).json({ success: false, error: failure.userMessage, reasons: analysis.reasons || [], primaryFailureCode: failure.primaryFailureCode });
      }

      const deviceName = selected.name || deps.stripProviderPrefix(providerName, selected.device_id);
      const profile = deviceProfiles.get(deviceName);
      const probes = await probeBackends();

      const prepareArgs = {
        req,
        cfg,
        filePath,
        target: providerName,
        autoTranscode,
        startSeconds: seconds,
        hlsJobs: deps.hlsJobs,
        generateStreamToken: deps.generateStreamToken,
        getMimeType: deps.getMimeType,
        sshExec,
        createVlcJob: deps.createVlcJob,
        createLiveFfmpegJob: deps.createLiveCastJob,
        logger,
      };

      const backendsToTry = (() => {
        if (requestedBackend !== 'auto') {
          if (subtitleSelection.burnIn && requestedBackend !== 'ffmpeg-live') {
            const err = new Error('Burn-in subtitles require FFmpeg Live. Select ffmpeg-live or use Auto.');
            err.status = 400;
            throw err;
          }
          return [requestedBackend];
        }
        const ranked = rankBackendsForAuto({
          analysis,
          deviceProfile: profile,
          castConfig,
          probes,
          subtitleMode: subtitleSelection.burnIn ? 'burn-in' : subtitleSelection.mode,
        }).slice(0, castConfig.maxAutoFallbackAttempts);
        if (subtitleSelection.burnIn) {
          return ['ffmpeg-live'];
        }
        return ranked.map((r) => r.backend);
      })();

      const autoExplanation = requestedBackend === 'auto'
        ? explainAutoChoice(rankBackendsForAuto({
          analysis,
          deviceProfile: profile,
          castConfig,
          probes,
          subtitleMode: subtitleSelection.burnIn ? 'burn-in' : subtitleSelection.mode,
        }), backendsToTry[0])
        : null;

      let lastError = null;
      let successResult = null;

      for (let i = 0; i < backendsToTry.length; i++) {
        const backend = backendsToTry[i];
        if (prepared) await cleanupPreparedMedia(prepared, 'fallback');
        prepared = null;

        diagnostics.transitionState(sessionId, 'preparing', `Preparing ${backend}`, { backend });
        try {
          prepared = await prepareForBackend(req, backend, {
            filePath,
            startSeconds: seconds,
            analysis,
            subtitleSelection,
            prepareArgs,
          });
        } catch (err) {
          lastError = err;
          attemptedBackends.push({ backend, ok: false, error: err.message, score: null });
          diagnostics.recordBackendAttempt(sessionId, { backend, ok: false, error: err.message });
          continue;
        }

        const preflight = await runPreflight({
          req,
          cfg,
          castConfig,
          sshExec,
          getReceiverBaseUrl,
          streamUrl: prepared.streamUrl,
          subtitleUrl: subtitleSelection.url,
          backend,
          deviceName,
          diagnostics,
        });

        if (preflightIsBlocking(preflight)) {
          const failure = classifyFailure({ preflightFailed: true });
          const formatted = formatPreflightFailure(preflight);
          diagnostics.transitionState(sessionId, 'failed', formatted.message, {
            primaryFailureCode: failure.primaryFailureCode,
            preflightStage: formatted.stage,
          });
          if (preflight.warnings?.length) {
            logger(`preflight warnings (non-blocking): ${preflight.warnings.map((w) => w.name).join(', ')}`);
          }
          return res.status(409).json({
            success: false,
            ...formatted,
            primaryFailureCode: failure.primaryFailureCode,
            diagnosticsUrl: `/api/cast/diagnostics/${sessionId}`,
          });
        }
        if (preflight.warnings?.length) {
          logger(`preflight warn-but-continue: ${preflight.warnings.map((w) => w.name).join(', ')}`);
        }

        diagnostics.transitionState(sessionId, 'starting', `Casting via ${backend}`, { backend });
        const playStarted = Date.now();
        let playResult;
        try {
          playResult = await provider.play({
            deviceId: selected.device_id,
            filePath,
            streamUrl: prepared.streamUrl,
            title: prepared.title,
            mimeType: prepared.mimeType,
            mediaKind: prepared.mediaKind,
            startSeconds: prepared.startSeconds,
            subtitlesUrl: subtitleSelection.mode !== 'off' && !subtitleSelection.burnIn ? subtitleSelection.url : null,
            preparedMedia: prepared,
          });
        } catch (err) {
          lastError = err;
          diagnostics.recordCattCommand(sessionId, { command: `cast ${prepared.streamUrl}`, stderr: err.message, code: 1 });
          attemptedBackends.push({ backend, ok: false, error: err.message, preflight });
          diagnostics.recordBackendAttempt(sessionId, { backend, ok: false, error: err.message, streamUrl: prepared.streamUrl });
          if (requestedBackend !== 'auto') break;
          continue;
        }

        diagnostics.transitionState(sessionId, 'waiting_for_receiver_request', 'Waiting for TV to request stream', { backend });
        const tvRequested = await waitForStreamRequest(sessionId, prepared.streamUrl, castConfig.streamRequestTimeoutMs);

        let verify = { ok: false };
        if (tvRequested) {
          diagnostics.transitionState(sessionId, 'buffering', 'Stream requested; verifying playback', { backend });
          verify = await verifyPlayback(sessionId, provider, selected.device_id, castConfig.verifyPlaybackTimeoutMs);
        }

        const attempt = {
          backend,
          ok: tvRequested && verify.ok,
          streamUrl: prepared.streamUrl,
          tvRequestedStream: tvRequested,
          playbackVerified: verify.ok,
          preflight,
          autoExplanation: i === 0 ? autoExplanation : null,
          durationMs: Date.now() - playStarted,
        };
        attemptedBackends.push(attempt);
        diagnostics.recordBackendAttempt(sessionId, attempt);

        if (tvRequested && verify.ok) {
          successResult = { playResult, backend, prepared, attempt };
          break;
        }

        if (!tvRequested) lastError = new Error('Cast command accepted, but receiver never requested stream. Likely bad URL, receiver incompatibility, or receiver-side rejection.');
        else lastError = new Error('Playback did not start or time did not advance');

        if (requestedBackend !== 'auto') break;
        logger(`auto fallback: ${backend} failed (${lastError.message}); trying next backend`);
        await provider.stop(selected.device_id).catch(() => {});
      }

      if (!successResult) {
        const failure = classifyFailure({
          cattFailed: attemptedBackends.every((a) => a.error && /catt/i.test(a.error)),
          tvRequestedStream: attemptedBackends.some((a) => a.tvRequestedStream),
          preflightFailed: attemptedBackends.some((a) => a.preflight && !a.preflight.ok),
        });
        if (!attemptedBackends.some((a) => a.tvRequestedStream)) failure.primaryFailureCode = 'TV_DID_NOT_REQUEST_STREAM';

        diagnostics.transitionState(sessionId, 'failed', failure.userMessage, {
          primaryFailureCode: failure.primaryFailureCode,
          error: lastError?.message,
        });
        deviceProfiles.recordFailure(deviceName, { backend: backendsToTry[0], reason: failure.userMessage });
        await cleanupPreparedMedia(prepared, 'failed');

        return res.status(502).json({
          success: false,
          sessionId,
          error: failure.userMessage,
          primaryFailureCode: failure.primaryFailureCode,
          secondaryFailureCodes: failure.secondaryFailureCodes,
          attemptedBackends,
          diagnosticsUrl: `/api/cast/diagnostics/${sessionId}`,
        });
      }

      const { prepared: okPrepared, backend, playResult } = successResult;
      diagnostics.transitionState(sessionId, 'playing', 'Playback verified', { backend });

      if (castConfig.adbEnabled) {
        const adbSerial = await resolveAdbUsbSerial(sshExec, castConfig.adbSerial);
        const snap = await collectAdbSnapshot({
          sshExec,
          serial: adbSerial,
          serverIp: (() => { try { return new URL(getReceiverBaseUrl(req, cfg)).hostname; } catch (_) { return null; } })(),
        }).catch(() => null);
        if (snap) diagnostics.recordAdbObservation(snap, sessionId);
      }

      const session = castSessions.create({
        sessionId,
        provider: providerName,
        deviceId: selected.device_id,
        deviceName,
        filePath,
        title: okPrepared.title,
        mediaKind: okPrepared.mediaKind,
        preparedMedia: summarizePrepared(okPrepared),
        streamUrl: okPrepared.streamUrl,
        jobId: okPrepared.jobId,
        pipelineMode: okPrepared.pipelineMode,
        backend,
        startSeconds: okPrepared.startSeconds,
        duration: okPrepared.duration,
        state: 'playing',
        subtitleSelection,
      });
      castSessions.update({ sessionId, diagnosticsUrl: `/api/cast/diagnostics/${sessionId}` });

      deps.syncActiveCastSession?.({
        filePath,
        streamUrl: okPrepared.streamUrl,
        backend,
        jobId: okPrepared.jobId,
        prepared: okPrepared,
        subtitleUrl: subtitleSelection.url,
        seconds,
      });

      deviceProfiles.recordSuccess(deviceName, { backend, analysis: okPrepared.analysis });

      return res.json({
        success: true,
        sessionId,
        provider: providerName,
        deviceId: selected.device_id,
        backend,
        attemptedBackends,
        streamUrl: okPrepared.streamUrl,
        playbackMode: okPrepared.playbackMode,
        analysis: okPrepared.analysis,
        subtitle: subtitleSelection,
        receiverObserved: playResult,
        tvRequestedStream: true,
        autoExplanation,
        diagnosticsUrl: `/api/cast/diagnostics/${sessionId}`,
        session,
        preparedMedia: summarizePrepared(okPrepared),
        jobId: okPrepared.jobId || null,
        live: ['hls', 'vlc', 'ffmpeg-live'].includes(backend),
        receiver: playResult,
      });
    } catch (err) {
      if (sessionId) diagnostics.transitionState(sessionId, 'failed', err.message, { error: err.message });
      await cleanupPreparedMedia(prepared, 'start-failed');
      const status = err.status || (err.code === 'LOCALHOST_RECEIVER_URL' ? 409 : 500);
      return res.status(status).json({
        success: false,
        sessionId,
        error: err.message,
        reasons: err.reasons || undefined,
        diagnosticsUrl: sessionId ? `/api/cast/diagnostics/${sessionId}` : null,
      });
    }
  }

  return { startCast };
}

module.exports = {
  createCastOrchestrator,
  normalizeSubtitleInput,
};
