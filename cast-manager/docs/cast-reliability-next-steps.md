# Cast Reliability — Next Steps

## Completed (2026-06-15)

- [x] HLS enabled (`CAST_ENABLE_HLS_BACKEND=1`) — MKV/EAC3 casts via HLS
- [x] Library E2E: `scripts/cast-library-e2e.sh` (MKV, HEVC, MP4)
- [x] Subtitle auto (external VTT via catt)
- [x] Subtitle burn-in via ffmpeg-live (`-vf subtitles=`)
- [x] `deploy.sh` uses `SSH_KEY=~/.ssh/pinn_rtx3090` by default

## Optional follow-ups

- [ ] Test a full-size library rip (e.g. Family Guy MKV) manually from UI
- [ ] HLS burn-in (currently burn-in is ffmpeg-live only)
- [ ] `CAST_SUBTITLE_BURN_IN_FALLBACK=1` auto-fallback when external VTT not requested
- [ ] Fix deploy.sh to reliably kill existing server before restart (avoid EADDRINUSE)

## Commands

```bash
# Deploy from Mac
cd cast-manager && bash deploy.sh

# Full library + subtitle suite on Ubuntu
bash scripts/cast-library-e2e.sh

# Single test
bash scripts/cast-e2e-adb-test.sh --backend auto --subtitle burn-in --file /path/to/video.mkv
```
