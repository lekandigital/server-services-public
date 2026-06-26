const { evaluatePreflightResult, buildPreflightResponse } = require('../lib/cast/preflight');

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const checks = [
  { name: 'server_lan_url', ok: true, detail: 'http://198.51.100.10:8004', suggestion: '' },
  { name: 'receiver_visible', ok: false, detail: 'not in scan', suggestion: 'warn' },
  { name: 'receiver_reachable', ok: true, detail: 'State: IDLE', suggestion: '' },
  { name: 'host_can_fetch_stream', ok: false, detail: 'timeout', suggestion: 'warn only' },
];

const evaluated = evaluatePreflightResult(checks);
assert(evaluated.ok === true, 'non-blocking failures should not block');
assert(evaluated.warnings.length === 2, 'expected 2 warnings');
assert(evaluated.blockingFailures.length === 0, 'no blocking failures');

const blocked = evaluatePreflightResult([
  { name: 'server_lan_url', ok: false, detail: 'http://127.0.0.1:8004', suggestion: 'set LAN URL' },
  { name: 'adb_connected', ok: false, detail: 'none', suggestion: 'plug usb' },
]);
assert(blocked.ok === false, 'localhost base should block');
assert(blocked.blockingFailures[0].stage === 'server-url', 'stage mapping');

const resp = buildPreflightResponse(blocked);
assert(resp.success === false && resp.blocking === true, 'structured blocking response');
assert(resp.suggestedFix === 'set LAN URL', 'suggestedFix propagated');

console.log('preflight tests passed');
