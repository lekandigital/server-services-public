# Cast Reliability Worklog

Handoff reference: `/Users/lekan/Dev/misccomputerthings/RECEIVER_ANDROID_TV_RELIABILITY_HANDOFF.md`  
(Cast Manager uses Ubuntu SSH + USB ADB to NVIDIA Chromecast / Android TV.)

## Session baseline

| Item | Value |
|------|-------|
| Branch | `fix/cast-reliability-adb-harness` |
| Dev machine OS | macOS (Darwin) — tests run on Ubuntu via SSH |
| Ubuntu host | `o@REDACTED_SERVER_IP` (cast-manager deployment) |
| Cast Manager URL | `http://REDACTED_SERVER_IP:8004` |
| Chromecast name | `REDACTED_DEVICE` @ `REDACTED_CHROMECAST_IP` |
| ADB USB serial | `14291HFDD2RTE3` (prefer USB over TCP/wireless) |
| TV model | Chromecast (sabrina), Android 14 |
| catt | `/home/REDACTED_USER/.local/bin/catt` v0.12.12 |

## Hypothesis (initial)

Cast failures were likely caused by:

1. No verification that the TV actually requested the stream URL after `catt` success
2. `ffmpeg-live` path bypassed by newer provider-based pipeline (auto chose HLS only)
3. Multiple ADB devices without USB serial selection
4. Missing preflight/diagnostics made failures opaque

## Completed

- [x] Phase 0: branch, `.gitignore`, worklog
- [x] Phase 1: `scripts/cast-diagnose-env.sh`
- [x] Phase 3: `scripts/cast-create-test-media.sh`
- [x] Phase 4: diagnostics store, HTTP logging, `/api/cast/diagnostics*`
- [x] Phase 6: preflight checks (blocking vs warn, structured JSON, `/api/cast/preflight`)
- [x] Phase 7: session state machine in diagnostics
- [x] Phase 8–9: orchestrator, backend scoring, auto fallback
- [x] Phase 10: device profiles (`data/cast-device-profiles.json`)
- [x] Phase 15–16: Cast Doctor + debug bundle endpoints
- [x] Phase 17/19: E2E + smoke scripts
- [x] USB ADB auto-resolve (`14291HFDD2RTE3` when `CAST_ADB_SERIAL` unset)
- [x] Preflight/CORS fix: live-stream probes no longer block; catt `-d` reachability; expanded OPTIONS headers
- [x] Control/seek: ffmpeg-live restart-on-seek; idle play restarts session; mini-player stays visible during seek
- [x] `scripts/cast-control-e2e-adb.sh` full pause/play/seek/scrub/stop harness

## In progress

- [ ] Deploy updated server.js to Ubuntu and restart
- [ ] Run `cast-control-e2e-adb.sh` on Ubuntu after deploy

## Next command

```bash
# On Ubuntu (via SSH):
cd ~/server-services/cast-manager   # or synced path
bash scripts/cast-create-test-media.sh
export CAST_MANAGER_URL=http://REDACTED_SERVER_IP:8004
export CAST_TEST_FILE_PATH=/tmp/cast-manager-test-media/known_good_h264_aac.mp4
export CAST_ADB_SERIAL=14291HFDD2RTE3
bash scripts/cast-control-e2e-adb.sh --backend auto
```

## Test results

| Test | Result | Notes |
|------|--------|-------|
| `simple-direct` / `auto` MP4 | PASS | Golden path |
| MKV H.264+EAC3 `auto` | PASS | Auto chose **HLS** (EAC3 audio transcode) |
| MKV H.264+EAC3 `hls` | PASS | Explicit HLS |
| HEVC smoke MKV `auto` | PASS | Library E2E suite |
| Subtitle `auto` (external VTT) | PASS | Direct + `--subtitles` URL |
| Subtitle `burn-in` | PASS | **ffmpeg-live** + `-vf subtitles=` |
| `deploy.sh` + SSH key | PASS | `~/.ssh/pinn_rtx3090` |
| `CAST_ENABLE_HLS_BACKEND=1` | PASS | Enabled on Ubuntu `.env` |

Server env on Ubuntu (`/home/REDACTED_USER/cast_manager_v3/.env`):
- `CAST_PUBLIC_BASE_URL=http://REDACTED_SERVER_IP:8004`
- `CAST_BACKEND_DEFAULT=auto`
- `CAST_ENABLE_HLS_BACKEND=1`
- `CAST_ADB_SERIAL=14291HFDD2RTE3`

E2E evidence (2026-06-15): diagnostics show REDACTED_DEVICE requested stream URL on `REDACTED_SERVER_IP:8004` with HTTP 206.

## Handoff doc

Use `/Users/lekan/Dev/misccomputerthings/RECEIVER_ANDROID_TV_RELIABILITY_HANDOFF.md` for Android TV context.  
Cast testing: SSH to Ubuntu → USB ADB serial `14291HFDD2RTE3` (auto-detected when `CAST_ADB_SERIAL` unset).
