const path = require('path');

function isLocalhostHost(host) {
  const h = String(host || '').toLowerCase();
  return h.startsWith('localhost') || h.startsWith('127.0.0.1') || h.startsWith('[::1]');
}

function normalizeBaseUrl(value) {
  return String(value || '').trim().replace(/\/+$/, '');
}

function getReceiverBaseUrl(req, cfg = {}, env = process.env) {
  const explicit = normalizeBaseUrl(env.CAST_PUBLIC_BASE_URL || env.PUBLIC_BASE_URL || env.SERVER_PUBLIC_URL);
  if (explicit) return explicit;

  const proto = req?.headers?.['x-forwarded-proto'] || req?.protocol || 'http';
  const host = req?.headers?.['x-forwarded-host'] || req?.headers?.host || '';
  if (host && !isLocalhostHost(host)) return normalizeBaseUrl(`${proto}://${host}`);

  const lanHost = cfg.publicHost || cfg.sshHost || '127.0.0.1';
  const port = cfg.port || 8004;
  return normalizeBaseUrl(`http://${lanHost}:${port}`);
}

function assertReceiverReachableUrl(url) {
  const parsed = new URL(url);
  if (isLocalhostHost(parsed.host)) {
    const err = new Error(`Receiver URL uses localhost (${parsed.host}). Set CAST_PUBLIC_BASE_URL to the Cast Manager LAN URL.`);
    err.code = 'LOCALHOST_RECEIVER_URL';
    throw err;
  }
  return url;
}

function buildJobUrl(req, cfg, jobId, fileName = 'master.m3u8') {
  const base = getReceiverBaseUrl(req, cfg);
  return assertReceiverReachableUrl(`${base}/api/cast/jobs/${encodeURIComponent(jobId)}/${encodeURIComponent(fileName)}`);
}

function buildSegmentBaseUrl(req, cfg, jobId) {
  const base = getReceiverBaseUrl(req, cfg);
  return assertReceiverReachableUrl(`${base}/api/cast/jobs/${encodeURIComponent(jobId)}/segment/`);
}

function buildTokenStreamUrl(req, cfg, token, filename, query = 'raw=1') {
  const base = getReceiverBaseUrl(req, cfg);
  const safeName = encodeURIComponent(path.basename(filename || 'media'));
  const suffix = query ? `?${query.replace(/^\?/, '')}` : '';
  return assertReceiverReachableUrl(`${base}/stream/${encodeURIComponent(token)}/${safeName}${suffix}`);
}

function sanitizeUrlForLog(url) {
  try {
    const u = new URL(url);
    const parts = u.pathname.split('/');
    const streamIdx = parts.indexOf('stream');
    if (streamIdx >= 0 && parts[streamIdx + 1]) parts[streamIdx + 1] = '***';
    u.pathname = parts.join('/');
    return u.toString();
  } catch (_) {
    return String(url || '').replace(/\/stream\/[^/]+/g, '/stream/***');
  }
}

module.exports = {
  assertReceiverReachableUrl,
  buildJobUrl,
  buildSegmentBaseUrl,
  buildTokenStreamUrl,
  getReceiverBaseUrl,
  isLocalhostHost,
  sanitizeUrlForLog,
};
