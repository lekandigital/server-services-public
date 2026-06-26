# Cast Manager Button Reliability Audit

**Date:** 2026-06-19

**Automated evidence:** `tests/drive-ux.spec.ts`, `scripts/test-drive-ux.sh`, `frontend/tests/e2e/app.spec.ts`, `frontend/tests/e2e/controls.spec.ts`, `scripts/test-cast-manager-button-audit.sh`

**Result:** PASS — every visible button has an accessible name; every disabled button has a reason; primary and destructive handlers were clicked against safe mocked APIs; live read/API contracts passed separately.

Rows marked “per item” apply to every rendered record. Conditional controls were tested in the state that makes them visible.

| Screen | Button/control | Expected action | Endpoint/function | Status | Evidence | Fix |
| ------ | -------------- | --------------- | ----------------- | ------ | -------- | --- |
| App shell | Collapse/expand sidebar | Resize desktop sidebar | `app.toggleSidebar()` | Works | Playwright label audit | Replaced vague “Panel” button |
| App shell | Mobile navigation | Open/close sidebar | `app.mobileNavOpen` | Works | `mobile-light.png` | Responsive single menu control |
| App shell | Main navigation and file-root shortcuts | Push a durable route and render every required section/root | `app.setSection()` / `library.navigateToPath()` | Works | `drive-ux.spec.ts` visits required sidebar items | Added My Drive / Files, Downloads, Media Library, Starred count, storage meter, and cast chip |
| App shell | Refresh current view | Refresh section-appropriate data | Library/torrents/cast stores | Works | Controls click audit | Centralized safe refresh behavior |
| App shell | Diagnostics shortcut | Open Diagnostics page | `app.setSection('diagnostics')` | Works | Playwright | Replaced drawer-only discoverability |
| Dashboard | Open Library | Open configured media root | `library.load(mediaRoot)` | Works | Playwright | Uses `/home/REDACTED_USER/watch_list` config/fallback |
| Dashboard | Active cast diagnostics | Open current diagnostics | App navigation | Works | Screenshot/control audit | Visible only with active/transient cast |
| Dashboard | Recent media card (per item) | Open containing folder | Library store | Works | Controls audit | Server-backed recent data |
| Dashboard | View all recent | Open Recent page | App navigation | Works | Controls audit | Added explicit shortcut |
| Dashboard | Manage torrents | Open Torrents page | App navigation | Works | Playwright | — |
| Dashboard | Review storage | Open Storage page | App navigation | Works | Playwright | — |
| Dashboard | Open diagnostics | Open Diagnostics page | App navigation | Works | Playwright | — |
| Dashboard | Storage retry | Retry storage summary | `GET /api/storage/stats` | Works | API contract | Inline failure and retry added |
| Quick Cast | URL/magnet input | Accept analyzable input | Local state | Works | Regression Playwright | Clear placeholder and Enter behavior |
| Quick Cast | Analyze URL | Classify before cast | `POST /api/url/analyze` | Works | Live API + Playwright | New safe analyzer endpoint |
| Quick Cast | Cast URL / Add torrent | Start supported URL or add magnet | `POST /api/stream` / `/api/torrents` | Works | Mock click audit | Disabled until analysis succeeds |
| Quick Cast | Cast URL when unsupported | Prevent broken cast | Analysis state | Disabled with reason | ntvs regression test | Shows detailed embed limitation |
| Quick Cast | Retry failed analysis | Retry inline failure | `analyze()` | Works | Handler audit | No generic silent failure |
| Library | Refresh | Reload current folder | `GET /api/files` | Works | Live Playwright | Loading state disables with reason |
| Library | Breadcrumb (per level) | Push ancestor route and load matching server path | `library.navigateToPath(path)` → `GET /api/files` | Works | Drive route/back/forward Playwright | URL and server path now move together |
| Library | New folder | Create a folder in the current safe root | `POST /api/files/mkdir` | Works | Payload/build audit | Only shown when config reports support |
| Library | Copy folder URL | Copy durable frontend folder URL | `copyToClipboard(appUrlForServerPath())` | Works | Clipboard-mocked Playwright | Distinct from stream/share links |
| Library | Search input and scope | Search current folder locally by default or all roots via server | Local filter / `GET /api/search` | Works | Drive UX Playwright + API contract | Global failures leave folder browsing available |
| Library | Search retry | Retry failed query | `library.search()` | Works | Handler audit | Inline endpoint detail |
| Library | Type filter | Limit visible file kinds | Store filter | Works | Controls audit | Supports all required types |
| Library | Sort selector | Sort by name/date/size/type | Store sort | Works | Controls audit | Persisted locally |
| Library | List toggle | Render table | Store view mode | Works | Playwright | — |
| Library | Grid toggle | Render cards | Store view mode | Works | Playwright/screenshots | — |
| Library | Folder Open (per item) | Enter folder | `GET /api/files` | Works | Controls audit | Folder-only primary action |
| Library | Visible Cast (per media item) | Open cast setup without using overflow menu | Cast panel | Works | Drive UX Playwright | Labeled row/card action |
| Library | Visible Star (per item) | Optimistically star/unstar with revert on error | Star endpoints | Works | Drive UX Playwright | Filled state is visible outside overflow menu |
| Library | Preview/Read/Inspect (per item) | Open compatible preview | Preview panel | Works | Video/image/text controls audit | Type-specific labels |
| Library | More actions (per item) | Open action menu | Local state | Works | Controls audit | Accessible per-file label |
| File menu | Open folder | Enter selected folder | Library store | Works | Controls audit | Folder-only |
| File menu | Preview/Read/Inspect | Open selected file preview | Preview panel | Works | Controls audit | Hidden for folders |
| File menu | Cast to device | Open cast setup | Cast panel | Works | Controls audit | Hidden for non-media |
| File menu | Add to queue | Add playable media locally | Activity store | Works | Controls audit | Clearly local-only |
| File menu | Copy stream URL | Generate and copy URL | `POST /api/stream/generate` | Works | Controls audit | Media-only |
| File menu | Copy folder URL / Copy app link | Copy durable frontend route, with `?preview=1` for files | Central path/clipboard utilities | Works | Drive UX Playwright | Replaces vague copy behavior |
| File menu | Analyze media | Open details and run compatibility analysis | Preview/media APIs | Works | Handler + cast tests | Media-only |
| File menu | Star/Unstar | Persist server star state | `POST/DELETE /api/files/star` | Works | API + controls audit | One correct stateful action |
| File menu | Create share link | Create and copy share | `POST /api/share` | Works | Controls audit | Hidden for folders |
| File menu | Download | Open server download | `GET /api/files/download` | Works | Popup audit | Hidden for folders |
| File menu | Rename | Prompt and rename | `POST /api/files/rename` | Works | Dialog/control audit | Disabled for protected paths with reason |
| File menu | Move | Prompt and move | `POST /api/files/move` | Works | Dialog/control audit | Disabled for protected paths with reason |
| File menu | Move to Trash | Confirm and trash | `POST /api/files/delete` | Works | Dialog/control audit | Disabled for protected paths with reason |
| Details/preview | Close | Close drawer and restore current-folder URL | `library.closePreview()` | Works | Deep-link Playwright | File app links remain refreshable |
| Media preview | Cast | Open cast setup | Cast panel | Works | Playwright | Media-only |
| Media preview | Add to queue | Add current media | Activity store | Works | Controls audit | Media-only |
| Media preview | Copy stream URL | Generate/copy stream token | `POST /api/stream/generate` | Works | Controls audit | Structured errors |
| Details/preview | Copy app/folder link | Copy durable frontend URL | Central clipboard utility | Works | Deep-link/clipboard tests | Folder and file wording is explicit |
| Details/preview | Create share link | Create and copy public share URL | `POST /api/share` | Works | Mock endpoint test | Files only |
| Details/preview | Star/Unstar | Persist star with rollback on failure | Star endpoints | Works | Store/Playwright audit | Visible primary detail action |
| Details/preview | Download | Download selected file | Download endpoint | Works | Popup audit | Files only |
| Media preview | Browser player | Play only compatible codecs | Native player | Hidden because unsupported | HEVC/MKV screenshot | Cast recommendation replaces black player |
| Media preview | Retry analysis | Re-run compatibility analysis | `POST /api/media/analyze` | Works | Controls audit | Inline failure state |
| Media preview | Retry metadata | Reload media details | `GET /api/media/info` | Works | API contract | Inline failure state |
| Media preview | Retry thumbnail | Regenerate thumbnail | `POST /api/thumbnail` | Works | API contract | Graceful unavailable state |
| Media preview | Open diagnostics | Close preview and open diagnostics | App navigation | Works | Controls audit | — |
| Cast panel | Close | Close setup without casting | `close()` | Works | Playwright | — |
| Cast panel | Device selector | Select cast target | `POST /api/cast/devices/select` | Works | Controls audit | Disabled with “No devices found” state |
| Cast panel | Refresh devices | Scan and reload targets | Scan/devices endpoints | Works | Controls audit | — |
| Cast panel | Backend selector | Choose supported backend | Cast store | Works | Controls audit | Doctor/config-gated options |
| Cast panel | HLS option | Show only when available | Config/Doctor feature | Hidden because unsupported | Visual/control audit | Explanatory text shown |
| Cast panel | Subtitle selector | Choose off/auto/track | Subtitle store | Works | Controls audit | Image-subtitle limitation explained |
| Cast panel | Start position | Beginning/resume/custom | Cast store | Works | Controls audit | — |
| Cast panel | Custom time input | Set start seconds | Cast start payload | Works | Controls audit | Conditional visibility |
| Cast panel | Analyze & preflight | Validate cast plan | `POST /api/cast/preflight` | Works | Controls audit | Explicit checking/error states |
| Cast panel | Start cast | Start selected media | `POST /api/cast/start` | Works | Mock click + remote live cast | Disabled while busy/no file with reason |
| Cast panel | Pretranscode confirm | Confirm advanced slow path | Local confirmation | Works | Handler audit | Only shown when enabled |
| Cast panel | Diagnostics | Open diagnostics | App navigation | Works | Controls audit | — |
| Now Playing | Scrubber | Optimistic drag; one seek on release | `POST /api/cast/controls` seek | Works | Remote verified seek + source audit | Latest release queued; polling paused while dragging |
| Now Playing | Seek back 10s | Seek once | Cast controls | Works | Mock click audit | Disabled while seek in flight |
| Now Playing | Play/Pause | Toggle session state | Cast controls | Works | Mock click audit | Explicit accessible label |
| Now Playing | Seek forward 30s | Seek once | Cast controls | Works | Mock click audit | Disabled while seek in flight |
| Now Playing | Volume | Set volume on release | Cast controls | Works | Mock change audit | Avoids continuous requests |
| Now Playing | Diagnostics | Open session diagnostics | App navigation | Works | Mock click audit | — |
| Now Playing | Stop | Stop active cast | Cast controls | Works | Remote live stop + mock audit | Persistent through transient states |
| Recent | Refresh/Retry | Reload server history | `GET /api/files/recent` | Works | Live/API audit | Inline endpoint failure |
| Recent | Show in Library (per item) | Open containing folder | Library store | Works | Controls audit | — |
| Starred | Refresh/Retry | Reload stars | `GET /api/files/starred` | Works | API audit | Inline endpoint failure |
| Starred | Open (per item) | Open folder/preview | Library/preview | Works | Controls audit | — |
| Starred | Open location (per item) | Push parent folder route and load it | `library.navigateToPath(parent)` | Works | Drive UX Playwright | Keeps starred results navigable |
| Starred | Unstar (per item) | Remove server star | `DELETE /api/files/star` | Works | Controls audit | — |
| Shared | Refresh/Retry | Reload shares | `GET /api/shares` | Works | API audit | Inline endpoint failure |
| Shared | Copy link (per item) | Copy public URL | Clipboard API | Works | Controls audit | — |
| Shared | Revoke (per item) | Confirm and revoke | `DELETE /api/shares/:id` | Works | Dialog/control audit | — |
| Torrents | Pause all / Resume all | Batch state change | Torrent endpoints | Works | Controls audit | Disabled when list empty with reason |
| Torrents | Refresh | Reload transfers | `GET /api/torrents` | Works | Live/API audit | — |
| Torrents | Drop zone | Upload dropped `.torrent` | Upload endpoint | Works | Handler audit | Rejects wrong type visibly |
| Torrents | Choose file | Open `.torrent` picker | File input | Works | Controls audit | — |
| Torrents | Magnet textarea | Accept one/many magnets | Local input | Works | Controls audit | — |
| Torrents | Paste clipboard | Paste magnet text | Clipboard API | Works | Handler audit | Permission denial is visible |
| Torrents | Add magnet(s) | Submit magnets | `POST /api/torrents` | Works | Controls audit | Disabled empty with reason |
| Torrents | Search/filter controls | Filter rendered transfers | Torrent store | Works | Controls audit | all/active/completed/stopped |
| Torrents | Pause/Resume (per item) | Change transfer state | Item endpoints | Works | Controls audit | Opposite action disabled with reason |
| Torrents | Priority (per item) | Set low/normal/high | Priority endpoint | Works | Controls audit | — |
| Torrents | Info (per item) | Open details modal | `GET /api/torrents/:id/info` | Works | Controls audit | — |
| Torrents | Remove | Confirm/remove torrent | `DELETE /api/torrents/:id` | Works | Dialog/control audit | — |
| Torrents | Remove + data | Confirm destructive delete | Delete endpoint | Works | Dialog/control audit | Explicit permanent-data warning |
| Queue | Clear queue | Clear browser queue | Activity store | Works | Controls audit | Disabled empty with reason |
| Queue | Browse Library | Open Library | App navigation | Works | Controls audit | Empty-state action |
| Queue | Remove (per item) | Remove queued item | Activity store | Works | Handler audit | — |
| Playlists | Save queue as playlist | Save local collection | Activity store/localStorage | Works | Dialog/control audit | Disabled empty queue with reason |
| Playlists | Open Queue | Navigate to queue | App navigation | Works | Controls audit | Empty-state action |
| Playlists | Delete (per item) | Delete local playlist | Activity store | Works | Controls audit | Local-only limitation disclosed |
| Storage | Refresh/Retry | Reload capacity and dirs | Storage endpoints | Works | API/control audit | Disabled while running with reason |
| Storage | Open media root | Open configured root | Library store | Works | Handler audit | Shown when dir breakdown absent |
| Storage | Open directory (per item) | Open directory in Library | Library store | Works | Controls audit | — |
| Trash | Refresh/Retry | Reload Trash | `GET /api/files/trash` | Works | API audit | Inline endpoint failure |
| Trash | Restore (per item) | Restore file | `POST /api/files/restore` | Works | Controls audit | — |
| Trash | Delete forever (per item) | Confirm permanent delete | `DELETE /api/files/trash/:id` | Works | Dialog/control audit | Explicit irreversible warning |
| Activity | Refresh/Retry | Reload activity | `GET /api/activity` | Works | API/control audit | Inline endpoint failure |
| Activity | Filter | Filter visible event type | Local computed state | Works | Controls audit | — |
| Settings | Theme selector | Keep light default | Settings store | Works | Playwright | Dark option disabled with reason in label |
| Settings | Default view | Persist list/grid | Settings store | Works | Playwright | — |
| Settings | Media root | Display server root | `GET /api/config` | Disabled with reason | Settings screenshot | Intentionally read-only |
| Settings | Device selector/refresh | Select/discover device | Device endpoints | Works | Controls audit | Selector disabled when none found |
| Settings | Backend selector | Persist cast backend | Settings/cast store | Works | Controls audit | HLS gated by feature |
| Settings | Auto transcode | Toggle compatibility fallback | Cast payload | Works | Controls audit | — |
| Settings | Allow pretranscode | Enable advanced backend | Settings store | Works | Controls audit | — |
| Settings | Diagnostics verbosity | Persist verbosity | Settings store | Works | Controls audit | — |
| Settings | Save settings (top/bottom) | Persist harmless preferences | localStorage | Works | Playwright/control audit | Both visible actions are wired |
| Settings | Run Cast Doctor | Run and open report | `POST /api/cast/doctor/run` | Works | Controls audit | — |
| Settings | Reset local preferences | Confirm/reset local state | Settings store | Works | Handler audit | Does not change server data |
| Diagnostics | Refresh checks | Reload status/doctor/timeline | Diagnostic endpoints | Works | Controls audit | Disabled while checking with reason |
| Diagnostics | Copy debug info | Copy structured bundle | Clipboard API | Works | Controls audit | Clipboard failure explained |
| Diagnostics | Retry server diagnostics | Retry failed diagnostics | Diagnostic endpoints | Works | Handler audit | Inline failure detail |
| Diagnostics | Clear timeline | Clear client events | App store | Works | Controls audit | Disabled when empty with reason |

## Unsupported features handled deliberately

- HLS is hidden unless `/api/config` reports it available.
- Browser playback is hidden for incompatible MKV/HEVC/E-AC-3-class media; casting is recommended instead.
- Dark mode is shown as unavailable rather than pretending to work.
- Queue and Playlists work locally and explicitly state that no backend endpoint exists.
- Protected file mutations are disabled with a reason.
