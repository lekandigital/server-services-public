# Cast Manager — State of the System Audit Report

**Audit ID:** `20260616-004644`  
**Date:** 2026-06-16 (UTC)  
**Target server:** `http://REDACTED_SERVER_IP:8004`  
**Repo branch:** `fix/cast-reliability-adb-harness` @ `6faa797`  
**Audit runner:** macOS (Darwin 25.5.0), Node v25.9.0  
**Evidence folder:** `cast-manager/diagnostics/cast-manager-audit/20260616-004644/`

---

## Executive summary

Cast Manager’s **casting pipeline is largely functional** on the live Ubuntu host: Chromecast (REDACTED_DEVICE / Google Chromecast with Google TV) receives streams, playback advances, pause/play/seek via `/api/cast/controls` work, and large HEVC MKV (27.6 GB Project Hail Mary) plays via direct stream URL.

The **static web frontend has critical backend mismatches and UX gaps**: `POST /api/files/recent` is not implemented (frontend silently fails), thumbnails return `null`, file browser defaults to **filesystem root `/`** instead of the media library, browser video range responses are **mathematically invalid for small files**, and Playwright could not exercise in-browser playback because no video rows appeared in the default library view.

**ADB/TV screenshots were not captured from the audit Mac** (no local device). Server-side `/api/cast/doctor` confirms ADB is connected on the Ubuntu host (`14291HFDD2RTE3`, Chromecast).

**Audit status: PARTIAL-COMPLETE** — API, UI screenshots, casting API controls, streaming range samples, and environment via remote doctor are complete; full ADB screencap/scrub on TV must be re-run on Ubuntu (`scripts/audit-casting-adb.sh` with `CAST_ADB_SERIAL`).

---

## Phase 1 — Baseline

| Item | Value |
|------|-------|
| Git clean at start | Yes (audit added untracked `scripts/audit-*.sh`) |
| Branch | `fix/cast-reliability-adb-harness` |
| Working directory | `cast-manager/` |
| Node / npm | v25.9.0 / 11.12.1 |

See: `diagnostics/cast-manager-audit/20260616-004644/baseline.txt`

---

## Phase 2 — Environment

**Local (Mac):** ffmpeg/ffprobe present; catt/VLC/adb **not** available; no ADB devices.

**Remote (via API / doctor on REDACTED_SERVER_IP):**

| Component | Status |
|-----------|--------|
| Server URL | `http://REDACTED_SERVER_IP:8004` |
| Chromecast | REDACTED_DEVICE @ REDACTED_CHROMECAST_IP |
| catt | `/home/REDACTED_USER/.local/bin/catt` — OK |
| ffmpeg | 4.4.2 Ubuntu — OK |
| VLC (cvlc) | OK |
| ADB | USB `14291HFDD2RTE3`, network `REDACTED_CHROMECAST_IP:5555` — OK |
| TV ping server | OK (~8–9 ms RTT) |
| HLS backend check | **Failed** in doctor (`hls_backend` ok:false) |
| DOWNLOAD_DIR (effective) | `/home/REDACTED_USER/watch_list` (from cast session paths) |
| FILE_MANAGER_ROOT | **`/`** (file API lists `/boot`, `/home`, etc.) |

Env redaction sample from cast preflight: `PORT=8004`, `CAST_PUBLIC_BASE_URL=http://REDACTED_SERVER_IP:8004`, `CHROMECAST_NAME=REDACTED_DEVICE`, `CAST_BACKEND_DEFAULT=vlc-renderer`, `CAST_ENABLE_HLS_BACKEND=0`.

See: `env.txt`, remote sections in `api-endpoints.json` (`/api/cast/doctor`, `/api/receiver/status`)

---

## Phase 3 — API endpoint inventory

Full matrix: `diagnostics/.../api-endpoints.md` and `api-endpoints.json`

### Confirmed failures / mismatches

| Endpoint | Status | Issue | Frontend impact |
|----------|--------|-------|-----------------|
| **POST `/api/files/recent`** | **404** HTML `Cannot POST /api/files/recent` | No route; only **GET** exists in `server.js` | Continue watching / recent tracking broken; `app.js` POST swallows error |
| **POST `/api/thumbnail`** | 200 but `{thumbnail:null}` | Generation failed for 76 GB HEVC MKV (and test MP4) | Empty poster tiles |
| **POST `/api/files/duration`** | 200 `duration: 0` | Wrong/zero for large Amadeus MKV | Progress bars / continue watching |
| **GET `/api/search?q=mkv`** | **500** | Internal error | Search broken for some queries |
| **GET `/api/qrcode`** | **400** | Bad request with `?url=` param tested | QR feature may need correct params |

### Stable endpoints (sample)

| Endpoint | Status | Notes |
|----------|--------|-------|
| GET `/`, `/file-manager` | 200 HTML | App shell loads |
| GET `/api/files` | 200 | Lists **`/` root**, not watch_list by default |
| GET `/api/files/recent` | 200 | Read works |
| GET `/api/cast/status` | 200 | Rich session + diagnostics |
| POST `/api/cast/controls` | 200 | pause/play/seek/stop |
| POST `/api/cast/start` | 200 | Full cast orchestration |
| GET `/api/cast/diagnostics` | 200 | Session history, preflight |
| GET `/api/cast/doctor` | 200 | Environment checks |
| GET `/api/torrents` | 200 | Many stopped torrents |
| GET `/api/storage/stats` | 200 | Largest files, breakdown |
| POST `/api/media/analyze` | 200 | Codec/compatibility |
| GET `/api/media/info` | 200 | ffprobe summary |
| POST `/api/share` | 200 | Share link creation |

---

## Phase 4 — Media library

See `media-inventory.json`, `media-inventory.md`

**Storage:** ~1.8 TB total, ~634 GB used on library volume.

**Largest video files (watch_list):**

| Size | Path |
|------|------|
| 76.1 GB | Amadeus…mkv (HEVC 4K, DTS, PGS subs) |
| 30.1 GB | Seven Samurai…mkv (HEVC, FLAC) |
| 27.6 GB | Project Hail Mary…mkv (HEVC 4K, EAC3) |

**Extension sampling via `/api/search`:** returned empty during audit (search index / 500 issues). Inventory relies on `/api/storage/stats` + `/api/media/info`.

**Cast compatibility (analyze):**

- HEVC 4K MKV → `playbackMode: full-transcode` / `hls-full-transcode` recommended for Chromecast
- Live system still casts large HEVC via **`backend: direct`** successfully (receiver tolerates it)

---

## Phase 5 — Portal UI (Playwright)

See `portal-ui/` screenshots, `portal-ui-report.md`, `portal-ui-results.json`

| Section | Loaded | Notes |
|---------|--------|-------|
| home, recent, starred, shared, torrents, queue, playlists, library, trash, activity, settings, storage | **Yes** | Full-page screenshots captured |
| Library file rows | **0 items** | Default API path is `/` — UI does not navigate to `/home/REDACTED_USER/watch_list` automatically |
| Video playback test | **Skipped** | No video row to click |
| Console / network | **POST /api/files/recent → 404** | Confirmed in browser |
| Thumbnails in DOM | **None observed** | `thumbnailTests: []` |

Screenshots: `diagnostics/.../portal-ui/section-*.png`, `00-initial-load.png`

---

## Phase 6 — Browser streaming / ranges

See `streaming-range-report.md`

- **10 MB range chunks** on large MKV: correct 206 responses.
- **Small MP4 (648 KB):** 206 responses claim `bytes 0-10485759/648783` — **invalid**; likely root cause of glitchy scrubber/metadata behavior.
- MKV HEAD can timeout on huge remote files.

---

## Phase 7 — Thumbnails & previews

See `thumbnails-previews.json`

| Type | Result |
|------|--------|
| Video thumbnail (MKV/MP4) | `{thumbnail: null}` |
| Image/text/sub sidecars | No samples found in search; tests skipped |

**Conclusion:** Thumbnail pipeline unreliable; ffmpeg extract may fail silently (errors redirected to `/dev/null` in server).

---

## Phase 8 — Casting (API + partial ADB)

See `casting-adb/summary.md`

| Test | Result |
|------|--------|
| known_good_h264_aac.mp4 | **PASS** — cast start, time 12→19s, seek, pause/play, stop |
| mkv-large (76 GB Amadeus) | **PARTIAL** — re-cast skipped; baseline captured Project Hail Mary session (27 GB) playing/paused at 1851s |
| ADB screenshots | **Not captured** (audit ran on Mac without adb device) |
| Server doctor | ADB connected on Ubuntu; media_session not collected in this run |

**Large MKV behavior:** Slow start possible; once playing, direct stream to Chromecast works for Project Hail Mary HEVC (unexpected vs analyze recommendation). Session diagnostics show states: `waiting_for_receiver_request` → `buffering` → `playing`.

**Scrubbing (API):** Seek forward +50% via `/api/cast/controls` returned updated `currentTime` in known-good test.

---

## Phase 9 — Subtitles

See `subtitles-report.md`

- `/api/subtitles` returns empty for all tested videos.
- Embedded PGS in Amadeus not listed despite analyze detecting `hdmv_pgs_subtitle`.
- No sidecar `.srt` found in quick search.

---

## Commands run

```bash
cd cast-manager
bash scripts/run-cast-manager-audit.sh
# Re-runs:
export AUDIT_DIR=diagnostics/cast-manager-audit/20260616-004644
export CAST_MANAGER_URL=http://REDACTED_SERVER_IP:8004
python3 scripts/audit-api-endpoints.py
# Manual curl range/thumbnail/subtitle probes documented in streaming-range-report.md
```

---

## Blockers for a complete audit

1. **Run casting ADB script on Ubuntu** with USB serial for TV screenshots + logcat.
2. **Fix or document FILE_MANAGER_ROOT** — UI should open `watch_list` not `/`.
3. **Implement POST `/api/files/recent`** before continue-watching can work.

---

## Deliverables index

| Document | Path |
|----------|------|
| This report | `docs/cast-manager-audit-report.md` |
| Feature matrix | `docs/cast-manager-feature-audit.md` |
| ChatGPT handoff | `docs/frontend-redesign-handoff-for-chatgpt.md` |
| Send package | `diagnostics/.../SEND_TO_CHATGPT.md` |
| Raw evidence | `diagnostics/cast-manager-audit/20260616-004644/` |
