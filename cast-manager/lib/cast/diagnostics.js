const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const CAST_PATH_PREFIXES = [
  '/api/cast/live/',
  '/api/cast/vlc/',
  '/api/cast/jobs/',
  '/api/cast/direct/',
  '/api/subtitles/',
  '/stream/',
];

function isCastStreamPath(urlPath) {
  const p = String(urlPath || '');
  return CAST_PATH_PREFIXES.some((prefix) => p.startsWith(prefix));
}

function createDiagnosticsStore({ diagnosticsDir, maxEntries = 500, logger = () => {} } = {}) {
  const sessions = new Map();
  let activeSessionId = null;
  let globalCattLog = [];
  let globalFfmpegErrors = [];
  let globalVlcErrors = [];
  let globalAdbObservations = [];
  let latestDoctorReport = null;

  function ensureDir() {
    try { fs.mkdirSync(diagnosticsDir, { recursive: true }); } catch (_) {}
  }

  function newCorrelationId() {
    return crypto.randomBytes(6).toString('hex');
  }

  function getOrCreateSession(sessionId) {
    const id = sessionId || activeSessionId;
    if (!id) return null;
    if (!sessions.has(id)) {
      sessions.set(id, {
        sessionId: id,
        createdAt: new Date().toISOString(),
        state: 'idle',
        stateHistory: [],
        backend: null,
        attemptedBackends: [],
        streamUrl: null,
        subtitleUrl: null,
        mediaAnalysis: null,
        subtitleSelection: null,
        streamRequests: [],
        subtitleRequests: [],
        hlsRequests: [],
        cattCommands: [],
        ffmpegErrors: [],
        vlcErrors: [],
        adbObservations: [],
        autoFallbackAttempts: [],
        tvRequestedStream: false,
        tvRequestedSubtitles: false,
        firstStreamRequestAt: null,
        firstSubtitleRequestAt: null,
        primaryFailureCode: null,
        secondaryFailureCodes: [],
        finalFailureReason: null,
        deviceName: null,
        receiverObserved: null,
        preflight: null,
        config: null,
      });
    }
    return sessions.get(id);
  }

  function setActiveSession(sessionId) {
    activeSessionId = sessionId;
    return getOrCreateSession(sessionId);
  }

  function transitionState(sessionId, state, reason = '', extra = {}) {
    const session = getOrCreateSession(sessionId);
    if (!session) return null;
    session.state = state;
    session.stateHistory.push({
      state,
      reason,
      at: new Date().toISOString(),
      backend: extra.backend || session.backend,
      error: extra.error || null,
      primaryFailureCode: extra.primaryFailureCode || null,
    });
    if (extra.primaryFailureCode) session.primaryFailureCode = extra.primaryFailureCode;
    persistLatest(session);
    return session;
  }

  function recordStreamRequest(sessionId, entry) {
    const session = getOrCreateSession(sessionId) || getOrCreateSession(activeSessionId);
    const row = { ...entry, at: new Date().toISOString() };
    if (session) {
      session.streamRequests.push(row);
      if (session.streamRequests.length > maxEntries) session.streamRequests.shift();
      if (!session.tvRequestedStream) {
        session.tvRequestedStream = true;
        session.firstStreamRequestAt = row.at;
      }
      if (entry.path && /\.m3u8/i.test(entry.path)) {
        session.hlsRequests.push(row);
      }
    }
    return row;
  }

  function recordSubtitleRequest(sessionId, entry) {
    const session = getOrCreateSession(sessionId) || getOrCreateSession(activeSessionId);
    const row = { ...entry, at: new Date().toISOString() };
    if (session) {
      session.subtitleRequests.push(row);
      if (!session.tvRequestedSubtitles) {
        session.tvRequestedSubtitles = true;
        session.firstSubtitleRequestAt = row.at;
      }
    }
    return row;
  }

  function recordCattCommand(sessionId, { command, stdout = '', stderr = '', code = 0, durationMs = 0 } = {}) {
    const row = {
      command,
      stdout: String(stdout).slice(0, 4000),
      stderr: String(stderr).slice(0, 4000),
      code,
      durationMs,
      at: new Date().toISOString(),
    };
    globalCattLog.push(row);
    if (globalCattLog.length > maxEntries) globalCattLog.shift();
    const session = getOrCreateSession(sessionId) || getOrCreateSession(activeSessionId);
    if (session) {
      session.cattCommands.push(row);
      if (session.cattCommands.length > 100) session.cattCommands.shift();
    }
    return row;
  }

  function recordBackendAttempt(sessionId, attempt) {
    const session = getOrCreateSession(sessionId);
    if (!session) return;
    session.attemptedBackends.push({ ...attempt, at: new Date().toISOString() });
    session.backend = attempt.backend || session.backend;
    if (attempt.streamUrl) session.streamUrl = attempt.streamUrl;
  }

  function recordAdbObservation(observation, sessionId = activeSessionId) {
    const row = { ...observation, at: new Date().toISOString() };
    globalAdbObservations.push(row);
    if (globalAdbObservations.length > maxEntries) globalAdbObservations.shift();
    const session = getOrCreateSession(sessionId);
    if (session) session.adbObservations.push(row);
    return row;
  }

  function persistLatest(session) {
    ensureDir();
    try {
      const latestPath = path.join(diagnosticsDir, 'latest.json');
      fs.writeFileSync(latestPath, JSON.stringify(summarizeSession(session.sessionId), null, 2));
    } catch (err) {
      logger(`diagnostics persist failed: ${err.message}`);
    }
  }

  function summarizeSession(sessionId) {
    const session = getOrCreateSession(sessionId);
    if (!session) return { activeSessionId, sessions: [] };
    return {
      activeSessionId,
      session: {
        ...session,
        streamRequests: session.streamRequests.slice(-50),
        subtitleRequests: session.subtitleRequests.slice(-20),
        hlsRequests: session.hlsRequests.slice(-50),
        cattCommands: session.cattCommands.slice(-20),
        stateHistory: session.stateHistory.slice(-30),
        attemptedBackends: session.attemptedBackends.slice(-10),
      },
      recentCatt: globalCattLog.slice(-20),
      recentAdb: globalAdbObservations.slice(-10),
      latestDoctor: latestDoctorReport,
    };
  }

  function getAllDiagnostics() {
    const all = [...sessions.values()].map((s) => summarizeSession(s.sessionId));
    return {
      activeSessionId,
      sessions: all.map((x) => x.session),
      recentCatt: globalCattLog.slice(-30),
      recentFfmpegErrors: globalFfmpegErrors.slice(-20),
      recentVlcErrors: globalVlcErrors.slice(-20),
      recentAdb: globalAdbObservations.slice(-20),
      latestDoctor: latestDoctorReport,
    };
  }

  function reset() {
    sessions.clear();
    activeSessionId = null;
    globalCattLog = [];
    globalFfmpegErrors = [];
    globalVlcErrors = [];
    globalAdbObservations = [];
    ensureDir();
    try { fs.writeFileSync(path.join(diagnosticsDir, 'latest.json'), '{}'); } catch (_) {}
  }

  function middleware() {
    return (req, res, next) => {
      if (!isCastStreamPath(req.path)) return next();
      const correlationId = req.headers['x-correlation-id'] || newCorrelationId();
      req.castCorrelationId = correlationId;
      const started = Date.now();
      let bytesSent = 0;
      let recordedRow = null;
      const originalWrite = res.write.bind(res);
      const originalEnd = res.end.bind(res);

      const requestEntry = () => ({
        correlationId,
        sessionId: activeSessionId,
        remoteIp: req.ip || req.connection?.remoteAddress,
        method: req.method,
        path: req.path,
        range: req.headers.range || null,
        userAgent: req.headers['user-agent'] || null,
        status: res.statusCode,
        bytesSent,
        timeToFirstByteMs: res.headersSent ? (Date.now() - started) : null,
        durationMs: Date.now() - started,
      });

      const recordRequest = () => {
        if (recordedRow) return recordedRow;
        const entry = requestEntry();
        recordedRow = req.path.includes('/api/subtitles/') || req.path.endsWith('.vtt')
          ? recordSubtitleRequest(activeSessionId, entry)
          : recordStreamRequest(activeSessionId, entry);
        return recordedRow;
      };

      res.write = (chunk, ...args) => {
        if (chunk) bytesSent += Buffer.byteLength(chunk);
        recordRequest();
        return originalWrite(chunk, ...args);
      };
      res.end = (chunk, ...args) => {
        if (chunk) bytesSent += Buffer.byteLength(chunk);
        const row = recordRequest();
        Object.assign(row, requestEntry());
        return originalEnd(chunk, ...args);
      };
      next();
    };
  }

  function setDoctorReport(report) {
    latestDoctorReport = { ...report, at: new Date().toISOString() };
    ensureDir();
    try {
      fs.writeFileSync(path.join(diagnosticsDir, 'doctor-latest.json'), JSON.stringify(latestDoctorReport, null, 2));
    } catch (_) {}
  }

  return {
    getAllDiagnostics,
    getOrCreateSession,
    middleware,
    newCorrelationId,
    recordAdbObservation,
    recordBackendAttempt,
    recordCattCommand,
    recordStreamRequest,
    recordSubtitleRequest,
    reset,
    setActiveSession,
    setDoctorReport,
    summarizeSession,
    transitionState,
    get activeSessionId() { return activeSessionId; },
  };
}

module.exports = {
  CAST_PATH_PREFIXES,
  createDiagnosticsStore,
  isCastStreamPath,
};
