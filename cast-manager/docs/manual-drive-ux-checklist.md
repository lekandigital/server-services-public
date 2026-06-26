# Cast Manager manual Drive UX checklist

Use the deployed server and at least one real streamable media file. Record the tested path, device, backend, result, and any diagnostics session ID.

- [ ] Open `/file-manager/user/o/downloads`; confirm the first request is `GET /api/files?path=/home/REDACTED_USER/downloads`.
- [ ] Open three nested folders; confirm every URL segment changes and every breadcrumb opens the matching server folder.
- [ ] Refresh the deepest folder; confirm the same folder and contents return.
- [ ] Use browser Back twice and Forward twice; confirm folder contents and breadcrumbs follow the URL.
- [ ] Copy folder URL, paste it into a new tab, and confirm it opens the same folder.
- [ ] Copy a file app link, paste it into a new tab, and confirm the parent folder loads with the details/preview panel open.
- [ ] Copy a stream URL and open it outside the app; confirm the tokenized media response works or the UI explains why stream sharing is unavailable.
- [ ] Create a share link, open it in a private/new tab, then revoke it from Shared.
- [ ] Click the labeled Cast action on a video; confirm preflight, device, backend, subtitles, and Start Cast remain visible.
- [ ] Start a real cast; confirm `/api/cast/start`, `/api/cast/status`, and the Now Playing bar report success, or a precise persistent failure appears with a Diagnostics link.
- [ ] Star a file and folder; confirm both immediately show a filled star and appear under the sidebar Starred page.
- [ ] From Starred, open the item and its location; confirm the browser URL is durable.
- [ ] Force a file-list or cast error; confirm the sidebar and breadcrumbs remain, Retry works, no raw HTML appears, and Diagnostics shows the failed endpoint.
- [ ] Check Dashboard, Torrents, Queue, Playlists, Storage, Activity, Diagnostics, and Settings remain reachable.
- [ ] Repeat the primary folder/cast/star flow at mobile width; confirm actions remain labeled and reachable.
