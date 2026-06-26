# Cast Manager Deployment Repair — 2026-06-19

## Problems found

1. Cursor started `node server.js` with `setsid` while the enabled systemd service also tried to start. The standalone process owned port 8004, and systemd crash-looped with `EADDRINUSE` more than 4,000 times.
2. `deploy.sh` used `pgrep -f`/`pkill -f` patterns that could match the SSH command itself.
3. A stale manual VLC relay process wrote repeated decompression errors to `/tmp/vlc-relay.log`. The log grew to approximately 1.0 TiB and filled `/` to 100%.
4. The full filesystem corrupted SQLite’s derived `file_index`/FTS search trees. User data tables remained readable.

## Live repair

- Stopped the stale VLC relay and truncated only its runaway log. Root filesystem usage changed from 100% with 0 free to 37% with about 1.0 TiB free.
- Stopped systemd and the legacy listener, then rebuilt SQLite into a new database after exporting all non-derived tables.
- Preserved 2 stream tokens, 61 activity rows, 1 starred item, 1 cast device, and 4 recent items. Shares, trash, tags, and watch progress were empty.
- Saved the original DB, WAL, SHM, and logical JSON export under:
  `/home/REDACTED_USER/cast_manager_v3/db-backups/search-repair-2026-06-20T01-33-48-215Z/`
- Started only `cast-manager.service`; it now owns port 8004 with `NRestarts=0`.
- Background indexing rebuilt 5,000 `file_index` and FTS rows.

## Verification

- Full SQLite `PRAGMA integrity_check`: `ok`
- Search endpoint: JSON 200 with MKV results
- Systemd: active, one Node 20 process, one port owner
- Served assets: `index-Bo7zD6Yw.js`, `index-DieEvy1p.css`
- Root disk: 37% used

## Deployment hardening

`deploy.sh` now:

- builds the Vite frontend before syncing;
- refuses to deploy when remote free space is below 2 GiB;
- stops systemd first and identifies any legacy server only by port ownership;
- never uses self-matching `pgrep -f`/`pkill -f` commands;
- starts systemd instead of launching a second background server;
- waits for `/api/config`, requires the systemd unit to stay active, and runs SQLite `quick_check`;
- verifies the deployed hashed assets and API after restart.

The reusable repair utility is `scripts/repair-cast-manager-search-db.js`. Run it only while Cast Manager is stopped; it always backs up the original database and refuses to replace it unless the rebuilt database passes integrity and foreign-key checks.
