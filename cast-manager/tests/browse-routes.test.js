'use strict';

const assert = require('assert');

// Mirrors the logic in frontend/src/utils/pathRoutes.ts
// These tests verify the expected conversions without requiring TypeScript compilation.

function encodePathSegment(segment) {
  return encodeURIComponent(segment);
}

function normalizeServerPath(input) {
  const value = String(input || '').replace(/\0/g, '').trim();
  if (!value.startsWith('/')) throw new Error('Server paths must be absolute');
  const parts = value.split('/').filter(Boolean);
  if (parts.some((p) => p === '.' || p === '..')) throw new Error('Path traversal segments are not allowed');
  return parts.length ? `/${parts.join('/')}` : '/';
}

function browseRouteToServerPath(routePath) {
  const clean = String(routePath || '').split('?')[0].split('#')[0];
  let rest;
  if (clean === '/browse' || clean === '/browse/') {
    rest = '';
  } else if (clean.startsWith('/browse/')) {
    rest = clean.slice('/browse'.length);
  } else {
    throw new Error('Not a browse route');
  }
  const segments = rest.split('/').filter(Boolean);
  const decoded = segments.map((seg) => {
    const d = decodeURIComponent(seg);
    if (!d || d === '.' || d === '..' || d.includes('\0') || d.includes('/') || d.includes('\\')) {
      throw new Error(`Invalid path segment in browse URL: "${seg}"`);
    }
    return d;
  });
  return decoded.length ? `/${decoded.join('/')}` : '/';
}

function serverPathToBrowseRoute(serverPath) {
  const normalized = normalizeServerPath(serverPath);
  if (normalized === '/') return '/browse/';
  const segments = normalized.split('/').filter(Boolean);
  return `/browse/${segments.map(encodePathSegment).join('/')}`;
}

function assertThrows(fn, messageHint) {
  try {
    fn();
    assert.fail(`Expected to throw (${messageHint}) but did not`);
  } catch (err) {
    if (err.message.startsWith('Expected to throw')) throw err;
    // Successfully threw — good
  }
}

// --- browseRouteToServerPath ---

assert.equal(browseRouteToServerPath('/browse'), '/');
assert.equal(browseRouteToServerPath('/browse/'), '/');
assert.equal(browseRouteToServerPath('/browse/home'), '/home');
assert.equal(browseRouteToServerPath('/browse/home/REDACTED_USER'), '/home/REDACTED_USER');
assert.equal(browseRouteToServerPath('/browse/etc'), '/etc');
assert.equal(browseRouteToServerPath('/browse/home/REDACTED_USER/My%20Folder'), '/home/REDACTED_USER/My Folder');
assert.equal(browseRouteToServerPath('/browse/home/REDACTED_USER/file-manager/drive'), '/home/REDACTED_USER/file-manager/drive');

// Query strings and fragments should be stripped
assert.equal(browseRouteToServerPath('/browse/home?foo=bar'), '/home');
assert.equal(browseRouteToServerPath('/browse/#hash'), '/');

// Traversal rejection
assertThrows(() => browseRouteToServerPath('/browse/..'), 'dot-dot');
assertThrows(() => browseRouteToServerPath('/browse/home/../etc'), 'traversal in middle');
assertThrows(() => browseRouteToServerPath('/browse/.'), 'single dot');

// Null byte rejection
assertThrows(() => browseRouteToServerPath('/browse/home/\0evil'), 'null byte');

// Non-browse route rejection
assertThrows(() => browseRouteToServerPath('/file-manager/drive'), 'non-browse route');

// --- serverPathToBrowseRoute ---

assert.equal(serverPathToBrowseRoute('/'), '/browse/');
assert.equal(serverPathToBrowseRoute('/home'), '/browse/home');
assert.equal(serverPathToBrowseRoute('/home/REDACTED_USER'), '/browse/home/REDACTED_USER');
assert.equal(serverPathToBrowseRoute('/etc'), '/browse/etc');
assert.equal(serverPathToBrowseRoute('/home/REDACTED_USER/My Folder'), '/browse/home/REDACTED_USER/My%20Folder');
assert.equal(serverPathToBrowseRoute('/home/REDACTED_USER/file-manager/drive'), '/browse/home/REDACTED_USER/file-manager/drive');

// Relative paths must be rejected
assertThrows(() => serverPathToBrowseRoute('home/o'), 'relative path');
assertThrows(() => serverPathToBrowseRoute(''), 'empty path');

// Traversal in server paths must be rejected
assertThrows(() => serverPathToBrowseRoute('/home/../etc'), 'traversal in server path');

// Null bytes are stripped and the path normalizes safely
assert.equal(serverPathToBrowseRoute('/home/\0'), '/browse/home');

// --- Round-trip fidelity ---

const roundTrips = ['/', '/home', '/home/REDACTED_USER', '/etc', '/usr/local/bin', '/home/REDACTED_USER/My Folder'];
for (const p of roundTrips) {
  assert.equal(browseRouteToServerPath(serverPathToBrowseRoute(p)), p, `Round-trip failed for ${p}`);
}

console.log('browse-routes tests passed');
