'use strict';

const assert = require('assert');
const fs = require('fs');
const fsp = fs.promises;
const os = require('os');
const path = require('path');
const express = require('express');
const { createDriveRouter } = require('../lib/drive-routes');

async function main() {
  const temp = await fsp.mkdtemp(path.join(os.tmpdir(), 'cast-manager-drive-test-'));
  const library = path.join(temp, 'drive');
  await fsp.mkdir(library);
  await fsp.writeFile(path.join(library, '.env'), 'hidden');
  await fsp.writeFile(path.join(library, 'hello.txt'), 'hello drive');

  const app = express();
  app.use(express.json());
  app.use(createDriveRouter({ libraryPath: library, homeDir: temp, currentUser: os.userInfo().username }));
  const server = await new Promise((resolve) => {
    const listener = app.listen(0, '127.0.0.1', () => resolve(listener));
  });
  const base = `http://127.0.0.1:${server.address().port}`;

  async function json(url, options) {
    const response = await fetch(`${base}${url}`, options);
    const payload = await response.json();
    return { response, payload };
  }

  try {
    const config = await json('/api/files/config');
    assert.equal(config.payload.service, 'File Manager');
    assert.equal(config.payload.feature, 'Drive');
    assert.equal(config.payload.library_path, library);

    const listing = await json(`/api/files/list?path=${encodeURIComponent(library)}`);
    assert.equal(listing.response.status, 200);
    assert(listing.payload.entries.some((entry) => entry.name === '.env' && entry.is_hidden));
    assert(listing.payload.entries.every((entry) => entry.permissions.length === 10));

    const preview = await json(`/api/files/preview?path=${encodeURIComponent(path.join(library, 'hello.txt'))}`);
    assert.equal(preview.payload.kind, 'text');
    assert.equal(preview.payload.content, 'hello drive');
    const dotfilePreview = await json(`/api/files/preview?path=${encodeURIComponent(path.join(library, '.env'))}`);
    assert.equal(dotfilePreview.payload.kind, 'text');
    assert.equal(dotfilePreview.payload.content, 'hidden');

    const mkdir = await json('/api/files/mkdir', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: library, name: 'folder' }),
    });
    assert.equal(mkdir.payload.ok, true);

    for (const expectedName of ['upload.txt', 'upload (1).txt']) {
      const form = new FormData();
      form.append('path', path.join(library, 'folder'));
      form.append('files', new Blob(['uploaded']), 'upload.txt');
      const upload = await json('/api/files/upload', { method: 'POST', body: form });
      assert.equal(upload.payload.ok, true);
      assert.equal(upload.payload.uploaded[0].saved_name, expectedName);
    }

    const source = path.join(library, 'folder', 'upload.txt');
    const renamed = path.join(library, 'folder', 'renamed.txt');
    const rename = await json('/api/files/rename', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: source, new_name: 'renamed.txt' }),
    });
    assert.equal(rename.payload.path, renamed);

    const copy = await json('/api/files/copy', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: renamed, destination: path.join(library, 'copied.txt') }),
    });
    assert.equal(copy.payload.ok, true);

    const unconfirmedDelete = await json('/api/files/delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: path.join(library, 'copied.txt') }),
    });
    assert.equal(unconfirmedDelete.response.status, 400);
    assert.equal(unconfirmedDelete.payload.code, 'CONFIRMATION_REQUIRED');

    const remove = await json('/api/files/delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: path.join(library, 'copied.txt'), confirm: true }),
    });
    assert.equal(remove.payload.ok, true);
    assert.equal(fs.existsSync(path.join(library, 'copied.txt')), false);

    console.log('drive-routes tests passed');
  } finally {
    await new Promise((resolve) => server.close(resolve));
    await fsp.rm(temp, { recursive: true, force: true });
  }
}

main().catch((error) => { console.error(error); process.exit(1); });
