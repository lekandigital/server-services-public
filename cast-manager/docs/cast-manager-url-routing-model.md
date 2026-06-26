# Cast Manager URL routing model

## Contract

The browser URL is the durable source of truth for file-manager location. Linux paths never appear as raw frontend routes.

| Purpose | Frontend route | Server path |
| --- | --- | --- |
| Default files | `/file-manager` | configured default media root |
| Media Library | `/file-manager/library` | `/home/REDACTED_USER/watch_list` when available/configured |
| Downloads | `/file-manager/user/o/downloads` | `/home/REDACTED_USER/downloads` |
| Downloads (case-sensitive alternate) | `/file-manager/user/o/Downloads` | `/home/REDACTED_USER/Downloads` |
| Nested folder | `/file-manager/user/o/downloads/movies/action` | `/home/REDACTED_USER/downloads/movies/action` |
| File preview/app link | `/file-manager/user/o/downloads/movies/title.mkv?preview=1` | `/home/REDACTED_USER/downloads/movies/title.mkv` |

Configured roots outside the canonical roots receive an opaque `/file-manager/root/<root-id>` prefix. The server path remains available to the authenticated app through safe config, but arbitrary filesystem paths are never derived from arbitrary route text.

When only `/home/REDACTED_USER/Downloads` exists, the backend advertises it with the canonical lowercase frontend prefix `/file-manager/user/o/downloads`. If both case-sensitive directories exist, both are returned and the capitalized directory keeps `/file-manager/user/o/Downloads` so they remain distinguishable.

## Rules

1. Root matching uses the longest matching route prefix or server-path prefix.
2. Every path segment is encoded/decoded separately so spaces, Unicode, and reserved URL characters round-trip safely.
3. `.` and `..` path segments, encoded separators, NULs, and paths outside an explicitly returned root are rejected.
4. `/file-manager` resolves to `defaultRootId` from `/api/config`.
5. Folder navigation pushes browser history. Refresh derives the server path from `location.pathname`; back/forward reload from `popstate`.
6. Preview links add `?preview=1`. The app lists the parent folder, selects the named item, and opens the details/preview panel.
7. Folder links copy an app URL. File app links copy a preview URL. Stream URLs and share URLs are created by their dedicated backend endpoints.

## Backend safety boundary

`GET /api/config` returns only explicit safe roots: configured media/download roots plus the known `/home/REDACTED_USER/watch_list`, `/home/REDACTED_USER/downloads`, and `/home/REDACTED_USER/Downloads` candidates that exist on the server. Every file operation revalidates its requested and real path against the same allowlist, including symlink resolution.
