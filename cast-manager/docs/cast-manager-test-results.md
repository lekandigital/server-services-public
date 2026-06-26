# Cast Manager Light UI Reliability — Test Results

**Branch:** `fix/cast-manager-light-ui-reliability`

**Date:** 2026-06-19

| Test | Command | Result |
| --- | --- | --- |
| TypeScript + Vite production build | `npm run build` | **PASS** |
| Playwright UI smoke | `scripts/test-cast-manager-ui-smoke.sh` | **PASS — 8/8** |
| Playwright button/control audit | `scripts/test-cast-manager-button-audit.sh` | **PASS — 3/3 targeted** |
| Live API JSON contract | `scripts/test-cast-manager-api-contract.sh` | **PASS — 19 endpoints** |
| Cast-safe smoke | `scripts/test-cast-manager-cast-smoke.sh` | **PASS status + scrub contract** |
| Live Chromecast start/seek/stop | deployed host `REDACTED_SERVER_IP:8004` | **PASS** |
| Existing backend unit suites | `npm test` | **PASS — media pipeline, starred, preflight** |
| Visual screenshot pass | `node scripts/capture-cast-manager-screenshots.mjs` | **PASS — 12 screenshots** |

## Playwright coverage

- Light mode is the default.
- Initial load has zero console errors and zero uncaught page errors.
- All 13 required navigation sections render.
- Library opens `/home/REDACTED_USER/watch_list`, loads real files, and switches list/grid.
- Video preview and cast setup open when video is available.
- Image and escaped text previews are exercised with deterministic fixtures.
- The ntvs regression URL is analyzed as unsupported HTML embed; Cast stays disabled with a specific explanation.
- Torrents, Settings, and Diagnostics primary controls work.
- Every visible button has an accessible name; every disabled button has a reason.
- Safe mocked interaction tests click file mutations, sharing, trash, torrent actions, cast setup, queue/playlists, settings, diagnostics, and Now Playing controls without changing real user data.
- No raw HTML API response appears in the UI.

## Live API results

Passed JSON contracts: config, media-root file list, recent GET/POST, media info, media analyze, thumbnail, cast status, cast doctor, cast devices, URL analyze, torrents, starred, trash, shares, activity, disk, storage stats, and search.

The local safe script skipped `POST /api/cast/controls` because no active local session existed. A separate authorized real-device smoke ran against the deployed service, where receiver-reachable public URLs and tokens align:

- Direct H.264/AAC MP4 cast started on REDACTED_DEVICE.
- Playback advanced 20 → 26 seconds.
- Seek to 10 seconds returned `verified: true`; status settled at 13 seconds playing.
- Stop succeeded; follow-up status had no active session.

The local dev server cannot be used for receiver playback with the deployed public host unless its public URL and token store point to the same reachable service. The failed local attempt left no active session and was not counted as a product pass.

## Screenshot evidence

Saved under `diagnostics/cast-manager-light-ui/`:

- `dashboard-light.png`
- `library-list-light.png`
- `library-grid-light.png`
- `media-preview-video-light.png`
- `cast-panel-light.png`
- `now-playing-light.png`
- `quick-cast-light.png`
- `torrents-light.png`
- `storage-light.png`
- `settings-light.png`
- `diagnostics-light.png`
- `mobile-light.png`

Baseline: `before/dashboard-dark-prototype.png`.

The screenshot pass uses deterministic fixtures for populated/active states and aborts on console or page errors. Live functionality was tested separately against `http://127.0.0.1:8004`.
