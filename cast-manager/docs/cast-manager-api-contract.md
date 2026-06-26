# Cast Manager API Contract (Frontend)

Essential endpoints used by the Vue frontend.

## Config & library

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/config` | `mediaRoot`, `features` |
| GET | `/api/files?path=` | Directory listing |
| POST | `/api/files/recent` | Track watch history |
| GET | `/api/files/recent` | Continue watching |
| GET | `/api/search?q=` | Search with JSON errors |
| POST | `/api/thumbnail` | Returns `status` field |
| GET | `/api/media/info` | Duration/metadata |
| POST | `/api/media/analyze` | Browser/cast compatibility |
| GET | `/api/files/stream?path=&raw=1` | Direct stream |
| GET | `/api/files/read` | Text/NFO preview |

## Cast

| Method | Path |
|--------|------|
| POST | `/api/cast/start` |
| POST | `/api/cast` (legacy) |
| GET | `/api/cast/status` |
| POST | `/api/cast/controls` |
| POST | `/api/cast/preflight` |
| GET | `/api/cast/devices` |
| POST | `/api/cast/devices/select` |
| GET | `/api/cast/diagnostics` |
| GET | `/api/cast/doctor` |
| POST | `/api/subtitles` |
| POST | `/api/cast/subtitles` |

## Torrents, storage, activity

Full torrent CRUD via `/api/torrents/*`, storage via `/api/storage/*`, stars/trash/shares/activity endpoints preserved.

## Backend fixes in this rewrite

1. `POST /api/files/recent` — implemented
2. `GET /api/config` — public safe config
3. `/api/*` JSON 404 handler
4. Stream range cap for files smaller than chunk size
5. Search FTS sanitization + search root prefers `downloadDir`
6. Thumbnail JSON includes `status` and `reason`

## Contract test

```bash
CAST_MANAGER_URL=http://127.0.0.1:8004 bash scripts/test-cast-manager-api-contract.sh
```
