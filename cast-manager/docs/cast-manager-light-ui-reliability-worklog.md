# Cast Manager Light UI Reliability Worklog

**Branch:** `fix/cast-manager-light-ui-reliability`

**Started:** 2026-06-19

## Current-state summary

- **Frontend stack:** Vue 3, TypeScript, Pinia, Vite, and plain CSS. The current redesign is a dark prototype with emoji navigation, low information hierarchy, sparse dashboard cards, and incomplete interaction coverage.
- **How the app is served:** Vite builds into `public/app/`; Express serves that directory and sends the SPA index for `/` and `/file-manager`.
- **Build command:** `npm run build` from `cast-manager` (delegates to `npm --prefix frontend run build`).
- **Start command:** `npm start` from `cast-manager`; the configured local port is 8004.
- **Known broken UI areas:** light mode is absent; Diagnostics is a drawer rather than a reachable nav page; Quick Cast casts without a clear analyze/result phase; several file actions are missing or misleading; queue/playlists are local-only but not clearly described; buttons lack consistent disabled reasons; existing Playwright only checks six shallow flows; dashboard data can remain in vague loading states.
- **Available backend endpoints:** config/media-root, files/list/read/download/stream/create/move/rename/delete, media info/analyze, thumbnails, cast status/devices/preflight/start/controls/doctor/diagnostics, torrents and batch controls, disk/storage, starred, recent (GET and POST), trash/restore/permanent delete, shares/revoke, activity, search, stream tokens, and URL/public-stream helpers.
- **Testing approach:** preserve the existing backend and cast paths; capture the current UI; expand Playwright into a route/control audit with console and network monitoring; add API contract, UI smoke, button audit, and cast-safe smoke scripts; save deterministic screenshots under `diagnostics/cast-manager-light-ui/`; verify casts/seek non-destructively locally and retain the prior ADB evidence for real-device casting.

## Baseline

- Current UI screenshot: `diagnostics/cast-manager-light-ui/before/dashboard-dark-prototype.png`
- The integrated in-app browser was unavailable; repository-installed Playwright is being used for browser automation and screenshot evidence.

## Guardrails

- Do not alter casting internals unless a small, proven compatibility fix is required.
- Preserve the existing cast backend alias normalization and single-release seek semantics.
- Never send a destructive cast control unless an active session exists.
- Never show a clickable action that has no implementation or explicit disabled reason.

## Implementation completed

- Replaced the dark prototype with a light, warm-neutral app shell, grouped labeled navigation, responsive layout, accessible SVG icons, and a persistent polished Now Playing surface.
- Made Diagnostics a required navigation page with failed endpoints, cast state, doctor status, timeline, retry, and copy-debug actions.
- Rebuilt Dashboard, Library, media preview, cast setup, Torrents, Storage, Settings, Recent, Starred, Shared, Trash, Activity, Queue, and Playlists.
- Centralized API parsing now rejects HTML as `ApiRouteMismatchError`, records method/URL/status/body snippets, uses JSON headers correctly, supports timeout/abort, and surfaces failures to Diagnostics.
- Added `POST /api/url/analyze` for safe direct-media/HLS/known-site/embed classification. The ntvs regression URL is classified as an unsupported HTML embed with a specific no-bypass explanation.
- Restored `ppv-relay.html` to Vite’s source public directory so future builds preserve the existing relay artifact.
- Kept backend casting internals unchanged. Frontend seek handling queues the latest release target, pauses polling while dragging, and sends a single seek per release.

## Verification completed

```text
npm run build                                      PASS
bash scripts/test-cast-manager-ui-smoke.sh        PASS (8 Playwright tests)
bash scripts/test-cast-manager-button-audit.sh    PASS (3 targeted tests)
bash scripts/test-cast-manager-api-contract.sh    PASS (19 endpoints; inactive cast control skipped)
bash scripts/test-cast-manager-cast-smoke.sh      PASS (status + scrub contract; inactive control skipped)
npm test                                           PASS (3 backend suites)
node scripts/capture-cast-manager-screenshots.mjs PASS (12 screenshots, zero console/page errors)
remote Chromecast smoke                            PASS (start, advance, seek, stop)
```

## Live Chromecast verification

- The first local-server cast attempt correctly failed because its public stream URLs target the deployed host while its temporary stream token lived only in the local database. It left no active session. This is an environment limitation of local casting, not a silent UI success.
- Re-ran against the actual `REDACTED_SERVER_IP:8004` service where public URLs and tokens align.
- Preflight: direct backend, no blocking failures.
- Started `/home/REDACTED_USER/watch_list/cast-manager-smoke-tests/smoke_direct_h264_aac.mp4` on REDACTED_DEVICE using direct playback.
- Status advanced from 20 to 26 seconds while `state=playing`.
- `POST /api/cast/controls` with `{ "action": "seek", "value": 10 }` returned `verified: true`; fallback restart was handled as a normal seek and status settled at 13 seconds playing.
- Stop returned success and the follow-up status reported no active session.

## Honest limitations

- Browser support remains codec-dependent; incompatible HEVC/MKV/E-AC-3 media is intentionally cast-first.
- Embedded PGS/VobSub subtitles may require burn-in/transcode.
- Local-device casting requires `CAST_PUBLIC_HOST` and stream-token state to resolve to the same service the receiver can reach; use the deployed host for real-device QA.
- The visual screenshot run uses deterministic API fixtures to show active-cast and populated states. Live API and functional Playwright runs were executed separately against the local server.
