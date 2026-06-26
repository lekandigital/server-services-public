# Cast Manager Frontend Rewrite — Worklog

**Branch:** `rewrite/cast-manager-frontend-vue`  
**Started:** 2026-06-16

## Audit docs read

- `docs/cast-manager-audit-report.md`
- `docs/cast-manager-feature-audit.md`
- `docs/frontend-redesign-handoff-for-chatgpt.md`
- `diagnostics/cast-manager-audit/20260616-004644/SEND_TO_CHATGPT.md`

## Audit pass/fail summary (baseline)

| Status | Count |
|--------|-------|
| Pass | 22 |
| Fail | 10 |
| Flaky/Partial | 4 |
| Untested | 9 |

### Critical failures to address

1. POST `/api/files/recent` → 404 (backend fix)
2. Library defaults to `/` not `/home/REDACTED_USER/watch_list` (frontend + config)
3. Thumbnails return `null` (backend status + frontend fallback)
4. Browser range invalid for small files (backend fix)
5. Search 500 on `q=mkv` (backend fix)
6. Duration POST returns 0 for large MKV (use `/api/media/info` in frontend)
7. Subtitles API empty despite embedded PGS (frontend messaging)
8. Silent error swallowing in old `app.js` (frontend fix)
9. MKV browser playback assumptions (frontend compatibility check)
10. Monolithic `app.js` (~4800 lines) (Vue rewrite)

### Working (must preserve)

- Cast pipeline: start, status, controls, diagnostics, doctor
- Public stream URLs `/stream/:token/:filename`
- Device discovery, preflight, media analyze/info
- Torrent list, storage stats, starred/trash/shares/activity
- ADB-verified MP4 cast + seek on Ubuntu host

## Design decisions

- **Stack:** Vue 3 + Vite + TypeScript + Pinia + plain CSS
- **Build output:** `public/app/` served by Express at `/`
- **Routing:** Hash-based (`#/dashboard`, `#/library`, etc.)
- **Queue/Playlists:** Client-side localStorage (matches legacy behavior)
- **Media root:** `GET /api/config` → `mediaRoot`; fallback `/home/REDACTED_USER/watch_list`
- **Library default path:** `mediaRoot` from config, never bare `/`

## Commands run

```bash
git checkout -b rewrite/cast-manager-frontend-vue
cp -r cast-manager/public/* cast-manager/public_legacy_backup/
git commit -m "backup: preserve legacy cast manager frontend"
```

## Backend fixes (planned/in progress)

- [x] POST `/api/files/recent`
- [x] GET `/api/config`
- [x] `/api/*` JSON error handler
- [x] Range cap for files smaller than chunk size
- [x] Search FTS escape / fallback + downloadDir search root
- [x] Thumbnail response status field

## Tests run

| Test | Result |
|------|--------|
| `npm run build` | PASS |
| `scripts/test-cast-manager-frontend-build.sh` | PASS |
| `scripts/test-cast-manager-api-contract.sh` | PASS (local :8004) |
| `scripts/test-cast-manager-playwright.sh` | PASS (6/6) |
| `scripts/test-cast-manager-adb-smoke.sh` | PASS on Ubuntu via SSH (known-good MP4) |

## Screenshots / results

- Playwright: 6 passed — `frontend/tests/e2e/app.spec.ts`
- Build output: `public/app/`
- API contract: all essential endpoints JSON 200 including POST recent and search mkv
- ADB Ubuntu: `diagnostics/cast-manager-audit/20260616-frontend-rewrite/casting-adb/` — known-good MP4 **PASS**

## ADB

Ran on Ubuntu via `ssh -i ~/.ssh/pinn_rtx3090 o@REDACTED_SERVER_IP`:

- Device: `14291HFDD2RTE3` (Chromecast USB)
- known-good MP4: cast success, playing, time 12→19s, seek OK, screenshots captured
