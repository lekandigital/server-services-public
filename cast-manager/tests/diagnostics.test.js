const assert = require('assert');
const { createDiagnosticsStore } = require('../lib/cast/diagnostics');

const diagnostics = createDiagnosticsStore({ diagnosticsDir: '/tmp/cast-manager-diagnostics-test' });
diagnostics.setActiveSession('live-stream-test');

const req = {
  path: '/api/cast/live/test-job',
  method: 'GET',
  headers: { 'user-agent': 'receiver-smoke' },
  ip: '198.51.100.50',
  connection: {},
};
const writes = [];
const res = {
  statusCode: 200,
  headersSent: true,
  write(chunk) { writes.push(chunk); return true; },
  end(chunk) { if (chunk) writes.push(chunk); return true; },
};

let nextCalled = false;
diagnostics.middleware()(req, res, () => { nextCalled = true; });
assert(nextCalled, 'middleware should continue');

res.write(Buffer.from('first'));
let summary = diagnostics.summarizeSession('live-stream-test');
assert(summary.session.tvRequestedStream === true, 'first streamed bytes should mark the TV request');
assert(summary.session.streamRequests.length === 1, 'stream request should be recorded once');
assert(summary.session.streamRequests[0].bytesSent === 5, 'first chunk bytes should be recorded');

res.write(Buffer.from('more'));
res.end(Buffer.from('done'));
summary = diagnostics.summarizeSession('live-stream-test');
assert(summary.session.streamRequests.length === 1, 'stream completion must not duplicate the request');
assert(summary.session.streamRequests[0].bytesSent === 13, 'final byte count should update the existing row');

console.log('diagnostics tests passed');
