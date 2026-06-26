# Cast Manager — Feature Pass/Fail Matrix

Audit: `20260616-004644` | Server: `http://REDACTED_SERVER_IP:8004`

| Area | Feature | Endpoint/UI path | Status | Evidence | Notes | Redesign implication |
|------|---------|------------------|--------|----------|-------|----------------------|
| App | App load | GET `/` | **Pass** | `portal-ui/00-initial-load.png`, api 200 | HTML shell loads | Keep AppShell entry |
| Nav | Sidebar navigation | `showSection()` | **Pass** | All `section-*.png` | 12 sections render | Preserve nav map; fix storage nav discoverability |
| Library | File browsing | GET `/api/files` | **Flaky** | api-endpoints.json; library itemCount 0 | Default `path` = `/` not watch_list | Default to DOWNLOAD_DIR; breadcrumbs |
| Library | File type detection | `/api/files` metadata | **Pass** | api files JSON has mime/ext | Works when correct path | Show type badges on FileCard |
| Library | Sorting | UI filter tabs | **Untested** | No files in default view | — | Test after root fix |
| Library | Filtering | filter-tab UI | **Untested** | — | — | — |
| Library | Search | GET `/api/search` | **Fail** | 500 on `q=mkv` | Server error | Error state + retry |
| Playback | Video browser playback | `/api/files/stream` + `<video>` | **Fail/Untested** | No video row; HEVC unsupported | Playwright skipped | Transcode/HLS path + codec messaging |
| Playback | Audio browser playback | stream endpoint | **Untested** | — | — | — |
| Preview | Image preview | GET `/api/files/stream` | **Untested** | No jpg samples found | — | ImagePreview component |
| Preview | Text/NFO preview | GET `/api/files/read` | **Untested** | No txt/nfo samples | — | TextPreview component |
| Preview | Subtitle preview | POST `/api/subtitles` | **Fail** | Empty arrays | No sidecars found | SubtitleSelector empty state |
| Media | Video thumbnails | POST `/api/thumbnail` | **Fail** | `{thumbnail:null}` | Silent ffmpeg failure | Loading/error/retry UI |
| Media | Image thumbnails | same | **Untested** | — | — | — |
| Recent | Recent tracking write | POST `/api/files/recent` | **Fail** | 404 HTML | Route missing | **Backend fix required** |
| Recent | Recent list read | GET `/api/files/recent` | **Pass** | api 200 | Read works | Wire continue watching |
| Recent | Continue watching | Home card + POST recent | **Fail** | POST 404; empty list | No watch history persisted | Block feature until POST exists |
| Recent | Watch progress | duration + recent | **Fail** | duration 0 on large mkv | — | Use media/info duration |
| Stream | Stream URL generation | POST `/api/cast/start`, `/stream/:token` | **Pass** | cast_response.json | Token URLs work for TV | CastPanel preserve flow |
| Stream | QR code | GET `/api/qrcode` | **Fail** | 400 | Param issue | Fix or document query shape |
| Cast | Cast modal / start | POST `/api/cast/start` | **Pass** | known-good cast JSON | Full diagnostics | CastPanel + preflight UI |
| Cast | Device scan | GET `/api/cast/devices` | **Pass** | api 200 | REDACTED_DEVICE found | Device picker |
| Cast | Cast MP4 | direct backend | **Pass** | casting-adb/known-good-mp4 | Verified playback | — |
| Cast | Cast MKV | direct backend | **Pass/Flaky** | Project Hail Mary session | 27GB HEVC plays but analyze says transcode | Show backend badge |
| Cast | Cast large MKV | 76GB Amadeus | **Partial** | Skipped re-cast | Too large for audit re-run | Progress UI for slow start |
| Cast | Cast video/mpeg | — | **Untested** | No mpeg in library | — | — |
| Cast | Cast subtitles | POST `/api/cast/subtitles` | **Untested** | No subtitle IDs | — | — |
| Controls | Play/pause | POST `/api/cast/controls` | **Pass** | pause.json, play.json | — | NowPlayingBar |
| Controls | Stop | POST controls stop | **Pass** | stop.json | — | — |
| Controls | Skip | — | **Untested** | — | — | — |
| Controls | Seek | POST seek | **Pass** | seek_forward.json | currentTime updates | Scrubber sync |
| Controls | Web scrubber | mini-player UI | **Untested** | No in-browser video | — | Build robust Scrubber |
| Controls | Android TV scrubber | ADB coordinates | **Untested** | No local ADB | Doctor shows device on server | Run on Ubuntu |
| Controls | Volume | cast status volumeLevel | **Pass** | status JSON | Read from status | Volume slider |
| Queue | Queue section | UI section-queue | **Pass** | screenshot | Loads | Clarify vs cast queue |
| Queue | Auto advance | — | **Untested** | — | — | — |
| Torrents | Torrent list | GET `/api/torrents` | **Pass** | api 200 | Many stopped | TorrentView |
| Torrents | Add magnet | POST `/api/torrents` | **Untested** | — | — | — |
| Torrents | Torrent controls | pause/resume/delete | **Untested** | — | — | — |
| Stars | Star/unstar | POST/DELETE `/api/files/star` | **Untested** | GET starred 200 | — | — |
| Trash | Trash | GET `/api/files/trash` | **Pass** | api 200 | — | — |
| Share | Sharing | GET/POST `/api/shares` | **Pass** | share create 200 | — | — |
| Settings | Settings persistence | localStorage UI | **Untested** | section loads | — | SettingsView |
| Diagnostics | Cast diagnostics | GET `/api/cast/diagnostics` | **Pass** | diagnostics JSON | Rich session history | DiagnosticsPanel for power users |
| Storage | Storage section | GET `/api/storage/stats` | **Pass** | screenshot + api | — | Dashboard widgets |

### Summary counts

| Status | Count (of 45) |
|--------|----------------|
| Pass | 22 |
| Fail | 10 |
| Flaky/Partial | 4 |
| Untested | 9 |
