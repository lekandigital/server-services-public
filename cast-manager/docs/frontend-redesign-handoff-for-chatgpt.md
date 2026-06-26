# Frontend Redesign Handoff for ChatGPT

**Purpose:** Evidence-backed requirements for redesigning Cast Manager’s static frontend (`public/index.html`, `public/app.js`, `public/styles.css`). **Do not implement in this pass** — design only.

**Audit evidence:** `diagnostics/cast-manager-audit/20260616-004644/`  
**Live server tested:** `http://REDACTED_SERVER_IP:8004`

---

## 1. What works and must be preserved

### Casting core (high confidence)

- **POST `/api/cast/start`** — orchestrates preflight, backend selection, stream URL, session ID, diagnostics URL.
- **GET `/api/cast/status`** — provider, device, `currentTime`, `duration`, `state`, active session, `streamUrl`, backend.
- **POST `/api/cast/controls`** — `play`, `pause`, `stop`, `seek` (value in seconds).
- **GET `/api/cast/diagnostics`** (+ per-session) — state history, preflight checks, attempted backends.
- **GET `/api/cast/doctor`** — environment health for support UI.
- **Public stream URLs** `/stream/:token/:filename` — Chromecast fetches with HTTP 206; verified for MP4 and large MKV.
- **Chromecast device discovery** — `/api/cast/devices`, legacy `/api/devices`.

### Library & file ops (when correct path used)

- **GET `/api/files?path=`** — listing with metadata, stars, folder sizes.
- **POST `/api/files/info`**, **GET `/api/media/info`**, **POST `/api/media/analyze`** — technical metadata and cast compatibility.
- **Starred, trash, shares, activity, storage stats, torrent list** — endpoints return valid JSON.

### UX patterns worth keeping (behavioral, not visual)

- Sidebar sections: Home, Recent, Starred, Shared, Torrents, Queue, Playlists, File Manager, Trash, Activity, Settings (+ Storage section exists in DOM).
- Toast notifications, cast debug/diagnostics exposure for power users.
- Continue watching *concept* (card on home) — data layer broken, concept is right.

---

## 2. What is broken — redesign should fix

| Issue | Evidence | Design response |
|-------|----------|-----------------|
| **POST `/api/files/recent` missing** | 404 HTML; Playwright network log | Until backend adds POST, use GET-only + alternative tracking; show error if write fails |
| **File manager opens `/` not media library** | `/api/files` returns `/boot`, `/home`…; library itemCount 0 | On Library open, default `path` to server `rootPath` from first response OR env Exposed config endpoint |
| **Thumbnails always null** | POST `/api/thumbnail` → `{thumbnail:null}` | Placeholder art by mime; retry; show "preview unavailable" |
| **Duration POST returns 0** | `/api/files/duration` on 76GB mkv | Prefer GET `/api/media/info` for duration |
| **Browser range bugs on small files** | 206 with Content-Range past EOF | Player must validate ranges; prefer transcoded MP4/HLS for browser |
| **Search 500** | `/api/search?q=mkv` | Inline error, fall back to folder browse |
| **Subtitles API empty** | `subtitles: []` despite PGS in analyze | "No cast-compatible subtitles" + explain burn-in/transcode |
| **Silent API failures** | `app.js` `.catch(() => {})` on recent POST | Never swallow errors; toast + diagnostics |

---

## 3. What is flaky — needs clearer UI states

| Behavior | Notes | UI states needed |
|----------|-------|------------------|
| Large MKV cast start | 27–76 GB; may buffer | `starting`, `waiting_for_receiver_request`, `buffering`, `playing` from diagnostics |
| HEVC direct cast | Analyze says transcode; direct still plays | Show `backend: direct` vs recommended HLS |
| Browser MKV/HEVC | Codec unsupported | `unsupported_codec`, offer cast-only CTA |
| HEAD/stream timeout | Large remote files | Spinner + cancel |
| HLS backend | Doctor: hls_backend false | Disable HLS option or show warning |
| Cast status idle flicker | Server masks idle after recent command | Debounce UI; trust `seekInProgress` |

---

## 4. Backend endpoints the new frontend should use

### Essential

```
GET  /
GET  /api/files?path=
POST /api/files/info
GET  /api/media/info?path=
POST /api/media/analyze
GET  /api/files/stream?path=&raw=1
POST /api/thumbnail
GET  /api/thumbnail/serve/:name
GET  /api/files/recent
POST /api/files/recent          ← NEEDS BACKEND IMPLEMENTATION
POST /api/files/duration
GET  /api/files/read
POST /api/cast/start
POST /api/cast
GET  /api/cast/status
POST /api/cast/controls
GET  /api/cast/devices
POST /api/cast/devices/select
POST /api/cast/preflight
GET  /api/cast/diagnostics
GET  /api/cast/diagnostics/:sessionId
POST /api/subtitles
GET  /api/subtitles/:id.vtt
POST /api/subtitles/prepare
POST /api/cast/subtitles
GET  /api/storage/stats
GET  /api/storage/dirs
GET  /api/torrents
POST /api/torrents
GET  /api/files/starred
POST /api/files/star
GET  /api/files/trash
GET  /api/shares
POST /api/share
GET  /api/activity
GET  /api/search?q=
GET  /api/qrcode
GET  /stream/:token/:filename     (cast playback — link only)
```

### Secondary / power user

```
GET  /api/cast/doctor
GET  /api/receiver/status
GET  /api/cast/jobs/:jobId
GET  /api/disk
GET  /api/stream/tokens
```

---

## 5. Recommended frontend architecture (design only)

```
┌─────────────────────────────────────────────────────────┐
│ AppShell (layout, routing, global polling)              │
│  ├─ Sidebar/Nav                                         │
│  ├─ Main router (section → view)                        │
│  ├─ NowPlayingBar (cast status poll 2–5s)               │
│  └─ Toast + DiagnosticsDrawer                           │
├─────────────────────────────────────────────────────────┤
│ API client module                                       │
│  - typed wrappers, JSON parse guard                     │
│  - detect HTML error bodies (Cannot POST…)              │
│  - base URL from window.location                        │
├─────────────────────────────────────────────────────────┤
│ State stores (plain objects or light store)             │
│  - libraryPath, files[], sort, filter                   │
│  - castSession, status, diagnostics                   │
│  - recent[], starred[], preferences                   │
└─────────────────────────────────────────────────────────┘
```

**Tech suggestion:** Vite + vanilla or Preact (keep deploy simple — static build served by Express). Avoid heavy framework unless user requests.

**Routing:** hash or path-based sections matching current `showSection(name)` for incremental migration.

---

## 6. Core user flows

### Browse library

1. Open File Manager → fetch `/api/files?path=/home/REDACTED_USER/watch_list` (not bare `/api/files`).
2. Navigate folders; show type icon from `extension` / `mimeType`.
3. Search → `/api/search` with error handling.

### Open video

1. Analyze → POST `/api/media/analyze` target `browser`.
2. If direct: `<video src="/api/files/stream?path=&raw=1">`.
3. Else: show transcode/HLS job UI or "Cast only".
4. Duration from `/api/media/info`.

### Cast video

1. Optional preflight → POST `/api/cast/preflight`.
2. Start → POST `/api/cast/start` with `filePath`, `backend: auto`, subtitle mode.
3. Poll `/api/cast/status`; show diagnostics link on failure.

### Scrub/seek while casting

1. POST `/api/cast/controls` `{action:'seek', value: seconds}`.
2. Poll status; respect `seekInProgress`.
3. Do not restart cast on seek unless backend indicates.

### Subtitles

1. POST `/api/subtitles` → populate selector.
2. If empty but analyze shows PGS → explain burn-in.
3. VTT URL for browser `<track>` when available.

### Images / text / NFO

- Images: stream URL in `<img>`.
- Text/NFO: `/api/files/read` in scrollable panel.

### Torrents

- List `/api/torrents`; actions map to existing POST routes.

---

## 7. Screenshots of current UI (with notes)

| File | Notes |
|------|-------|
| `portal-ui/00-initial-load.png` | Home loads; sidebar visible |
| `portal-ui/section-home.png` | Continue watching empty (no recent POST) |
| `portal-ui/section-library.png` | Wrong root — system folders not media |
| `portal-ui/section-torrents.png` | Torrent section renders |
| `portal-ui/section-storage.png` | Storage section (no nav button in sidebar — hidden section) |
| `portal-ui/section-settings.png` | Settings panel |
| `portal-ui/thumbnails-state.png` | No thumbnail images loaded |

---

## 8. Broken state screenshots / logs

- Network: `Cannot POST /api/files/recent` — see `portal-ui-results.json` `nonJsonApi`.
- Thumbnails: empty — `thumbnails-previews.json`.
- Casting evidence (working): `casting-adb/known-good-mp4/cast_response.json`.

---

## 9. Frontend anti-patterns to avoid

1. **Swallowing fetch errors** (`.catch(() => {})`) — breaks debugging and empty states.
2. **Assuming POST exists because GET exists** — recent files case.
3. **Defaulting file browser to server root** — never show `/boot` to media users.
4. **HTML `<video>` for all MKV** — HEVC/EAC3 will fail in browser.
5. **Scrubber tied to broken range math** — validate `Content-Range` vs `duration`.
6. **No loading distinction** — thumbnail `null` vs loading vs error.
7. **Monolithic `app.js`** (4800+ lines) — split by view/domain in new code.
8. **Duplicate device APIs** — prefer `/api/cast/devices` over legacy `/api/devices`.

---

## 10. Suggested component list

| Component | Responsibility |
|-----------|----------------|
| **AppShell** | Layout, section routing, global cast poll |
| **Sidebar/Nav** | Section switch; include Storage link |
| **Dashboard** | Home: continue watching, quick cast, storage summary |
| **LibraryView** | Path state, breadcrumbs, file table/grid |
| **FileCard/FileRow** | Icon, size, star, click actions |
| **MediaPreviewPanel** | Slide-over for selected file |
| **VideoPlayer** | Browser playback, range-aware, transcode fallback |
| **ImagePreview** | img stream |
| **TextPreview** | read API monospace viewer |
| **CastPanel** | Device, backend, subtitle options, start |
| **NowPlayingBar** | Fixed bottom: title, scrubber, transport |
| **Scrubber** | Draggable; debounce seek API |
| **SubtitleSelector** | Sidecar + embedded list |
| **Thumbnail** | lazy load + fallback |
| **TorrentView** | List + actions |
| **SettingsView** | Preferences, doctor link |
| **DiagnosticsPanel** | Session timeline, copy debug bundle |

---

## 11. Data / state model recommendations

```typescript
// Illustrative — not for implementation yet
interface CastSession {
  sessionId: string;
  filePath: string;
  state: 'idle'|'buffering'|'playing'|'paused'|'error';
  currentTime: number;
  duration: number;
  backend: string;
  streamUrl?: string;
  seekInProgress?: boolean;
}

interface LibraryState {
  currentPath: string;
  rootPath: string;      // expect watch_list
  files: FileEntry[];
  loading: boolean;
  error?: string;
}

interface RecentEntry {
  file_path: string;
  action: string;
  accessed_at: string;
}
```

Persist UI preferences (sort, view mode) in `localStorage`. Server is source of truth for stars, trash, shares, cast.

---

## 12. API client requirements

- Parse JSON only when `content-type` includes `json`.
- On HTML body with `Cannot POST/GET`, surface `ApiRouteMismatchError`.
- Timeout tiers: 10s list, 60s analyze/thumbnail, 120s cast start.
- Include `Accept: application/json` on all API calls.
- Optional: `X-Cast-Session` header if server expects it for debug.

---

## 13. Testing requirements for new design

1. **Contract tests** — every endpoint in section 4 returns expected shape.
2. **Playwright E2E** — navigate to watch_list, open MP4, cast, seek.
3. **Regression** — POST `/api/files/recent` must return 200 before continue-watching ships.
4. **Range validation** — unit test player logic against malformed 206.
5. **Visual** — screenshot each section + error states.
6. **Cast smoke** — reuse `scripts/cast-control-e2e-adb.sh` on Ubuntu.

---

## Backend fixes recommended before / during redesign

1. Add **POST `/api/files/recent`** calling existing `trackRecent()` in `db.js`.
2. Set **FILE_MANAGER_ROOT** or frontend default to `/home/REDACTED_USER/watch_list`.
3. Fix **thumbnail** ffmpeg error logging; return error reason in JSON.
4. Fix **range cap** for files &lt; 10MB.
5. Fix **GET `/api/search`** 500 for common queries.
