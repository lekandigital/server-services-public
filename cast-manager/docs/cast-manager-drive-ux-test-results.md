# Cast Manager Drive UX test results

Date: 2026-06-19  
Branch: `fix/cast-manager-drive-ux`

## Automated results

| Check | Result | Evidence |
| --- | --- | --- |
| Server syntax | PASS | `node --check server.js` |
| Vue/TypeScript production build | PASS | `npm run frontend:build` |
| Existing backend unit tests | PASS | media pipeline, starred, and preflight suites |
| Drive UX Playwright | PASS — 3/3 | nested routes, refresh, back/forward, folder/API mapping, clipboard values, app/stream/share links, cast preflight/start/status/Now Playing, star UI, Starred/open-location, preview deep links, and no raw HTML |
| SPA fallback | PASS | nested `/file-manager/...` returned `200 text/html` |
| API fallback | PASS | unknown `/api/*` returned JSON 404, never SPA HTML |
| Root safety | PASS | `/api/files?path=/etc` returned JSON 403 `INVALID_PATH` |
| Search degradation | PASS | live `/api/search?q=test` returned safe-root results; frontend keeps folder browsing available on failure |

## Live root verification

The local app server connected to the configured media host. `/api/config` returned `/home/REDACTED_USER/watch_list` plus the existing `/home/REDACTED_USER/Downloads`. Because lowercase `/home/REDACTED_USER/downloads` is absent, config correctly advertised:

```json
{
  "id": "downloads",
  "serverPath": "/home/REDACTED_USER/Downloads",
  "routePrefix": "/file-manager/user/o/downloads"
}
```

Opening `/file-manager/user/o/downloads` preserved that browser URL and issued `GET /api/files?path=/home/REDACTED_USER/Downloads`; the live response rendered 9 entries with no page errors.

## Design QA

PASS. Ten required screenshots were generated and visually inspected in `diagnostics/cast-manager-drive-ux/`. The pass caught and fixed an overflow-menu clipping defect before final capture. Primary screenshot flows produced no browser errors; the diagnostics screenshot intentionally forces a JSON 500 to prove readable endpoint/error reporting.

## Limitations

- Automated cast start/status/Now Playing is verified with API mocks so this pass cannot disrupt a real TV session. A real device cast remains a manual checklist item.
- Rename and move still use native prompts because the backend requires a name/destination but does not expose a folder-picker API. The requests now use the correct backend fields and errors remain visible.
- Queue and Playlists remain browser-local, as documented by the pre-existing UI; no backend endpoints exist for them.
