'use strict';
/**
 * UI test for the per-list "Follow again" reset on the dashboard home page.
 *
 * Drives the real page in Chromium: button states, the confirm dialog, the toast, the
 * row repaint afterwards, and that adding the button did not break drag-to-reorder. It
 * also parses the page's inline scripts, because all of this dashboard's JS is written
 * inside Python string literals where a stray escape silently breaks the whole page.
 *
 * Self-contained — writes its own fixture to a temp dir and starts its own dashboard:
 *
 *   NODE_PATH=../cast-manager/node_modules node test_list_reset_ui.js
 *
 * Set PLAYWRIGHT_CHANNEL=chrome to use the installed Google Chrome instead of
 * playwright's bundled build, and SHOTS=<dir> to keep the screenshots.
 */

const { chromium } = require('playwright');
const { spawn, execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const PORT = Number(process.env.PORT || 8899);
const BASE = `http://127.0.0.1:${PORT}`;
const SHOTS = process.env.SHOTS || fs.mkdtempSync(path.join(os.tmpdir(), 'xbot-ui-shots-'));
const DONE = 'XPorter_following_smalllist_exported_2026-09-06-at-12-43-14';
const MUTUALS = 'xExport_karpathy_following_count-1081_1775510';
const MID_FLIGHT = 'xExport_elonmusk_following_count-1291_1772836';
const AWAITING = 'XPorter_following_sequoia_exported_2026-09-06';
const JUST_DONE = 'xExport_lilianweng_following_count-174_1777300';

const passed = [];
function check(label, cond, detail) {
  if (!cond) throw new Error(`${label}${detail ? ': ' + detail : ''}`);
  passed.push(label);
  console.log(`  ok  ${label}`);
}

function writeFixture(dir) {
  const queue = [];
  const add = (username, source_list, status, extra = {}) =>
    queue.push({ username, source_list, status, timestamp: '2026-09-01T10:00:00', ...extra });

  // finished, nothing held back -> reset offered
  for (let i = 0; i < 9; i++) add(`alpha${i}`, DONE, 'unfollowed');
  add('alpha9', DONE, 'skipped', { skip_reason: 'not found/suspended' });
  // finished, but with mutuals and one handle we still count as followed
  for (let i = 0; i < 20; i++) add(`beta${i}`, MUTUALS, 'unfollowed');
  for (let i = 0; i < 3; i++)
    add(`betamutual${i}`, MUTUALS, 'followed', { follows_back: true, follows_back_at: '2026-09-10T10:00:00' });
  add('betaheld', MUTUALS, 'followed');
  // still being followed through -> reset refused
  for (let i = 0; i < 40; i++) add(`gamma${i}`, MID_FLIGHT, 'pending_follow', { timestamp: '' });
  for (let i = 0; i < 5; i++) add(`gammadone${i}`, MID_FLIGHT, 'unfollowed');
  // followed but not unfollowed yet -> reset refused for a different reason
  for (let i = 0; i < 12; i++) add(`delta${i}`, AWAITING, 'followed');
  add('delta99', AWAITING, 'unfollowed');
  // finished unfollowing minutes ago -> the button should already be live
  const justNow = new Date(Date.now() - 5 * 60 * 1000).toISOString();
  for (let i = 0; i < 6; i++) add(`eps${i}`, JUST_DONE, 'unfollowed', { timestamp: justNow });

  fs.writeFileSync(path.join(dir, 'queue.json'), JSON.stringify(queue, null, 1));
  const followed = ['username,follow_timestamp'];
  const unfollowed = ['username,unfollow_timestamp'];
  for (const e of queue) {
    if (['followed', 'unfollowed', 'skipped'].includes(e.status)) followed.push(`${e.username},2026-09-01 08:00:00`);
    if (e.status === 'unfollowed' || e.follows_back || e.username === 'betaheld')
      unfollowed.push(`${e.username},2026-09-01 10:00:00`);
  }
  fs.writeFileSync(path.join(dir, 'followed.csv'), followed.join('\n') + '\n');
  fs.writeFileSync(path.join(dir, 'unfollowed.csv'), unfollowed.join('\n') + '\n');
  fs.writeFileSync(path.join(dir, 'daily_counts.json'), JSON.stringify(
    { date: new Date().toISOString().slice(0, 10), follows: 56, unfollows: 53, likes: 110,
      last_updated: '2026-09-18 15:50:22' }));
}

async function startDashboard(dir) {
  const proc = spawn('python3', [path.join(__dirname, 'dashboard.py'),
    '--port', String(PORT), '--host', '127.0.0.1', '--data-dir', dir], { stdio: 'ignore' });
  for (let i = 0; i < 40; i++) {
    try {
      const r = await fetch(`${BASE}/api/status`);
      if (r.ok) return proc;
    } catch (_) { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error('dashboard did not start');
}

/** Every line of this page's JS is emitted from a Python string; make sure it parses. */
async function checkInlineScriptsParse() {
  const html = await (await fetch(BASE)).text();
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
  check('page has inline scripts to check', scripts.length >= 1, String(scripts.length));
  scripts.forEach((body, i) => {
    const f = path.join(SHOTS, `inline-script-${i}.js`);
    fs.writeFileSync(f, body);
    execFileSync(process.execPath, ['--check', f]);   // throws on a syntax error
  });
  check('every inline script parses as JavaScript', true);
}

const row = (page, name) => page.locator(`.src-row[data-name="${encodeURIComponent(name)}"]`);

(async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'xbot-ui-'));
  writeFixture(dataDir);
  const server = await startDashboard(dataDir);
  await checkInlineScriptsParse();

  // PLAYWRIGHT_CHANNEL=chrome falls back to the installed Google Chrome when the
  // bundled browser build for this playwright version isn't downloaded.
  const channel = process.env.PLAYWRIGHT_CHANNEL;
  const browser = await chromium.launch(channel ? { channel } : {});
  const page = await browser.newPage({ viewport: { width: 1280, height: 1100 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  // the dashboard ships no favicon; that 404 is pre-existing noise
  page.on('console', (m) => {
    const where = (m.location() && m.location().url) || '';
    if (m.type() === 'error' && !where.includes('favicon')) errors.push(`${m.text()} (${where})`);
  });

  try {
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForSelector('.src-row');
    await page.screenshot({ path: `${SHOTS}/reset-ui-before.png`, fullPage: true });

    check('list rows render', (await page.locator('.src-row').count()) === 5);

    const doneBtn = row(page, DONE).locator('.src-reset');
    check('finished list shows a reset button', (await doneBtn.count()) === 1);
    check('reset button is enabled there', await doneBtn.isEnabled());
    check('button label reads "Follow again"', (await doneBtn.textContent()).includes('Follow again'));
    check('enabled button explains itself on hover',
      (await doneBtn.getAttribute('title')).includes('10 already-unfollowed'),
      await doneBtn.getAttribute('title'));

    const midBtn = row(page, MID_FLIGHT).locator('.src-reset');
    check('list still visibly in progress shows no reset button at all',
      (await midBtn.count()) === 0);

    const awaitRow = row(page, AWAITING);
    check('list awaiting unfollows offers no button either',
      (await awaitRow.locator('.src-reset').count()) === 0);
    check('it says what it is waiting on',
      (await awaitRow.locator('.src-reset-wait').textContent()).includes('after 12 more unfollows'),
      await awaitRow.locator('.src-reset-wait').textContent());

    // Unfollowing is the last step, so a list that finished minutes ago is already open.
    const justDoneRow = row(page, JUST_DONE);
    check('list that just finished unfollowing offers the button right away',
      await justDoneRow.locator('.src-reset').isEnabled());
    check('no waiting note on it', (await justDoneRow.locator('.src-reset-wait').count()) === 0);

    const mutualRow = row(page, MUTUALS);
    check('mutuals are shown on the row',
      (await mutualRow.textContent()).includes('🤝3 follow you back'),
      await mutualRow.textContent());

    // A disabled button must not open anything
    let dialogs = 0;
    page.on('dialog', async (d) => { dialogs++; lastDialog = d.message(); await d.dismiss(); });
    let lastDialog = '';
    await awaitRow.locator('.src-reset-wait').click({ force: true });
    await page.waitForTimeout(400);
    check('the wait note is not clickable', dialogs === 0);

    // Cancelling the confirm must change nothing
    // cooldown_seconds ticks between calls, so compare the state a reset would change
    const shape = (d) => JSON.stringify(d.sources.map((x) =>
      [x.name, x.pending, x.done, x.resettable, x.mutuals, x.held, x.can_reset]));
    const before = shape(await (await fetch(`${BASE}/api/queue/sources`)).json());
    await mutualRow.locator('.src-reset').click();
    await page.waitForTimeout(400);
    check('clicking asks for confirmation first', dialogs === 1);
    check('confirm names the list', lastDialog.includes('karpathy'), lastDialog);
    check('confirm says how many come back', lastDialog.includes('20 handle(s) go back to pending'), lastDialog);
    check('confirm says the CSVs are cleared', lastDialog.includes('followed.csv / unfollowed.csv'), lastDialog);
    check('confirm spells out who is left alone',
      lastDialog.includes('3 who follow you back') && lastDialog.includes('1 still counted as followed'),
      lastDialog);
    const afterCancel = shape(await (await fetch(`${BASE}/api/queue/sources`)).json());
    check('cancelling changes nothing', before === afterCancel);

    // Accept it for real
    page.removeAllListeners('dialog');
    page.on('dialog', async (d) => { lastDialog = d.message(); await d.accept(); });
    await mutualRow.locator('.src-reset').click();
    await page.waitForSelector('#toast', { state: 'visible' });
    const toast = await page.locator('#toast').textContent();
    check('toast confirms the re-queue', toast.includes('Re-queued 20 from this list'), toast);

    await page.waitForFunction(
      (n) => {
        const r = document.querySelector(`.src-row[data-name="${encodeURIComponent(n)}"]`);
        return r && r.textContent.includes('20 pending');
      }, MUTUALS, { timeout: 5000 });
    const repainted = await row(page, MUTUALS).textContent();
    // The 3 mutuals + 1 held row still count as done, so this drops to 4/24, not 0%.
    check('row repaints as unfinished', repainted.includes('4/24') && !repainted.includes('Complete'), repainted);
    check('row still shows the kept mutuals', repainted.includes('🤝3 follow you back'), repainted);
    check('row shows when it was reset', /↻ reset \d+[smhd] ago/.test(repainted), repainted);
    // Nothing is left to reset on that list, so the button goes away entirely.
    check('its button is gone', (await row(page, MUTUALS).locator('.src-reset').count()) === 0);

    const api = await (await fetch(`${BASE}/api/queue/sources`)).json();
    const after = api.sources.find((s) => s.name === MUTUALS);
    check('backend agrees the list is pending again', after.pending === 20 && after.pct < 100, JSON.stringify(after));
    check('backend kept the mutuals', after.mutuals === 3 && after.held === 1, JSON.stringify(after));

    // The button lives inside the drag container — make sure reordering still works
    // clear the reset toast first, otherwise we would just read it again
    await page.evaluate(() => { const t = document.getElementById('toast'); t.style.display = 'none'; t.textContent = ''; });
    const grip = row(page, DONE).locator('.src-grip');
    const gb = await grip.boundingBox();
    const topBox = await page.locator('.src-row').first().boundingBox();
    await page.mouse.move(gb.x + gb.width / 2, gb.y + gb.height / 2);
    await page.mouse.down();
    await page.mouse.move(topBox.x + topBox.width / 2, topBox.y - 12, { steps: 12 });
    await page.mouse.up();
    await page.waitForFunction(
      () => document.getElementById('toast').textContent.includes('Follow order saved'), null, { timeout: 5000 });
    check('drag-to-reorder still works', true);
    const order = await page.evaluate(() =>
      [...document.querySelectorAll('.src-row')].map((r) => decodeURIComponent(r.dataset.name)));
    check('dragged list moved to the top', order[0] === DONE, order[0]);

    await page.screenshot({ path: `${SHOTS}/reset-ui-after.png`, fullPage: true });
    check('no console or page errors', errors.length === 0, errors.join(' | '));

    console.log(`\nAll ${passed.length} UI checks passed`);
  } catch (e) {
    await page.screenshot({ path: `${SHOTS}/reset-ui-failure.png`, fullPage: true }).catch(() => {});
    console.error(`\nFAILED after ${passed.length} checks: ${e.message}`);
    process.exitCode = 1;
  } finally {
    await browser.close();
    server.kill();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
})();
