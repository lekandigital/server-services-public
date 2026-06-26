# Cast Manager Drive UX worklog

Date: 2026-06-19  
Branch: `fix/cast-manager-drive-ux`

## Inspection baseline

- Frontend stack: Vue 3, TypeScript, Pinia, Vite, and Playwright. The compiled SPA is served from `public/app` by the existing Express server.
- Route handling: the frontend has no client router. `appStore.section` and `libraryStore.currentPath` are kept in Pinia/localStorage, while Express serves the SPA only as a catch-all. URL paths do not drive app state.
- Folder-opening failure: folder handlers call `library.load(serverPath)` directly. They do not push a frontend route, do not respond to `popstate`, and do not reconstruct the current folder from `location.pathname` after refresh.
- Copy-link failure: existing actions copy raw server paths or backend URLs with direct `navigator.clipboard.writeText` calls. Several catches suppress clipboard failures. There is no central app-link builder and no distinct folder/app/stream/share link model.
- Casting exposure: casting is available in the file overflow menu and media preview, with a cast panel backed by `/api/cast/preflight`, `/api/cast/start`, `/api/cast/status`, and device endpoints. It is not consistently visible as a labeled action in every video/audio row or card.
- Starred behavior: the backend supports file and folder stars. A Starred page and sidebar entry exist, but row/card star controls and sidebar count are missing, optimistic failures are not consistently reverted, and opening a starred item does not update a durable folder URL.
- Backend endpoints found: `/api/config`, `/api/files`, file read/write/create/delete/rename/copy/mkdir/move/download/stream/upload, `/api/search`, recent/star/trash endpoints, `/api/share` and `/api/shares`, `/api/stream/generate` and `/stream/:token/:filename`, media analysis/thumbnail/subtitle endpoints, `/api/cast/start`, legacy `/api/cast`, cast preflight/devices/status/controls/diagnostics, torrents, storage, activity, settings-adjacent config, and diagnostics.
- Express routing: static assets are served before API routes; unknown `/api/*` already receives JSON 404; `/stream/*` and `/s/*` are excluded from the SPA fallback. Nested frontend paths can return `index.html`, but the SPA does not yet consume them.
- Safety issue: when `FILE_MANAGER_ROOT` is not configured, the backend currently defaults it to `/`. Although destructive critical paths have extra guards, listing/search can expose system-root folders. This pass will replace that browse boundary with explicit safe roots.

## Implementation log

- Added `frontend/src/utils/pathRoutes.ts` as the single path↔route boundary, including segment encoding, normalization, allowlist validation, longest-root matching, folder routes, and file preview URLs.
- Replaced the backend `/` browse default with explicit media/download roots. `/api/config` now returns `fileRoots`, `defaultRootId`, and feature flags; every file operation and resolved symlink is checked against the same safe-root set.
- Added case-sensitive Downloads detection. The live host has `/home/REDACTED_USER/Downloads` but not `/home/REDACTED_USER/downloads`, so it is advertised under canonical `/file-manager/user/o/downloads`; both receive distinct routes if both exist.
- Made `location.pathname` the durable navigation source. Folder opens and breadcrumbs push history; refresh reconstructs the server path; `popstate` handles back/forward; `?preview=1` restores file details.
- Fixed folder detection for backend entries that use `type: "folder"`; this was a direct cause of folder rows behaving like generic files.
- Added robust clipboard fallback and distinct Copy folder URL, Copy app link, Copy stream URL, and Create share link actions with visible success/failure diagnostics.
- Corrected frontend request payloads for rename, move, trash, copy, and mkdir to match the existing backend contracts.
- Rebuilt the sidebar with My Drive / Files, configured Downloads/Media roots, Recent, first-class Starred with count, Shared, Trash, retained management/system pages, storage indicator, and cast status.
- Added visible labeled Cast actions and visible Star controls to media rows/cards, automatic cast preflight, persistent cast errors, a details panel, open-location behavior from Starred, current/global search scope, new-folder control, and clear empty/error states.
- Moved file overflow menus to a viewport-level overlay after visual QA found the table container clipped lower actions.
- Added Drive UX Playwright coverage, manual checklist, deterministic screenshot capture, URL model, button-audit updates, and this result report.
