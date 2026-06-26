# Cast Manager Frontend Architecture

## Stack

- Vue 3 + TypeScript + Vite
- Pinia state management
- Plain CSS with design tokens (`src/styles/`)
- Playwright E2E tests (`frontend/tests/e2e/`)

## Build output

Production build writes static assets to `public/app/`. Express serves:

- `express.static('public/app')` for hashed assets
- `GET /` and `GET /file-manager` → `public/app/index.html`
- SPA fallback for non-API routes

Legacy UI preserved in `public_legacy_backup/`.

## Structure

```
frontend/src/
  api/          HTTP client + domain API wrappers
  stores/       Pinia stores (app, library, cast, media, torrents, settings, activity)
  components/   Shell, library, preview, cast, torrents, storage, activity, settings
  utils/        File typing, cast state mapping
  styles/       tokens, base, layout, components
```

## Routing

Client-side section routing via Pinia `app.section` (no vue-router). All nav sections map 1:1 to views.

## Data sources

| Feature | Source |
|---------|--------|
| Stars, trash, shares, recent, activity | Server SQLite via API |
| Queue, playlists | localStorage (legacy parity) |
| UI preferences | localStorage |
| Cast session | Server status polling |

## Key behaviors

- Library defaults to `GET /api/config` → `mediaRoot` (fallback `/home/REDACTED_USER/watch_list`)
- Thumbnails: loading / available / unavailable / error states
- Browser video: analyze first; show cast CTA for incompatible MKV/HEVC
- Scrubber: optimistic drag, single seek on release, polling paused while dragging
- Errors: toasts + diagnostics drawer; no silent `.catch(() => {})`

## Dev

```bash
# Terminal 1 — backend
cd cast-manager && npm start

# Terminal 2 — Vite dev (proxies /api to :8004)
npm run frontend:dev
```

## Production

```bash
cd cast-manager
npm run build
npm start
```
