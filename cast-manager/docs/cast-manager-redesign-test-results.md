# Cast Manager Redesign — Test Results

**Branch:** `rewrite/cast-manager-frontend-vue`  
**Date:** 2026-06-16

## Automated

| Test | Command | Result |
|------|---------|--------|
| TypeScript + Vite build | `npm run build` | **PASS** |
| Build artifacts | `scripts/test-cast-manager-frontend-build.sh` | **PASS** |
| API contract | `scripts/test-cast-manager-api-contract.sh` | Run against live server |
| Playwright E2E | `scripts/test-cast-manager-playwright.sh` | Run against live server |
| ADB smoke | `scripts/test-cast-manager-adb-smoke.sh` | **PASS** on Ubuntu via SSH |

## ADB (Ubuntu)

Run from Mac:

```bash
ssh -i ~/.ssh/pinn_rtx3090 o@REDACTED_SERVER_IP
# on host:
CAST_MANAGER_URL=http://127.0.0.1:8004 CAST_ADB_SERIAL=14291HFDD2RTE3 \
  AUDIT_DIR=diagnostics/cast-manager-audit/20260616-frontend-rewrite \
  bash scripts/audit-casting-adb.sh
```

**2026-06-16 result:** known-good MP4 cast PASS (playback advanced, seek OK, ADB screenshots).  
Evidence: `diagnostics/cast-manager-audit/20260616-frontend-rewrite/casting-adb/`

## Evidence paths

- Audit baseline: `diagnostics/cast-manager-audit/20260616-004644/`
- Build output: `public/app/`
- Playwright reports: `frontend/playwright-report/` (after run)

## Known limitations

- Thumbnails may still return `unavailable` when ffmpeg extract fails on server
- Browser HEVC MKV remains cast-first UX by design
- `/api/subtitles` may be empty for PGS-only titles (UI explains burn-in)
- Queue/playlists remain client-local (legacy behavior)
