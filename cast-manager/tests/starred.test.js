const assert = require('assert');
const { inferKindFromPath, normalizeStarredRow, parentPathOf } = require('../lib/folders/starred');

assert.equal(inferKindFromPath('/media/Shows', true), 'folder');
assert.equal(inferKindFromPath('/media/a.mkv', false), 'video');
assert.equal(inferKindFromPath('/media/a.flac', false), 'audio');
assert.equal(inferKindFromPath('/media/a.srt', false), 'subtitle');
assert.equal(parentPathOf('/home/REDACTED_USER/watch_list/Shows'), '/home/REDACTED_USER/watch_list');

const folder = normalizeStarredRow({
  file_path: '/home/REDACTED_USER/watch_list/Shows',
  name: 'Shows',
  kind: 'folder',
  pinned_to_sidebar: 1,
  exists: 1,
});
assert.equal(folder.type, 'folder');
assert.equal(folder.kind, 'folder');
assert.equal(folder.parentPath, '/home/REDACTED_USER/watch_list');
assert.equal(folder.pinned_to_sidebar, 1);

const video = normalizeStarredRow({ file_path: '/home/REDACTED_USER/watch_list/a.mkv', kind: 'video' });
assert.equal(video.item_type, 'file');
assert.equal(video.type, 'video');

console.log('starred tests passed');
