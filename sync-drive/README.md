# Sync Drive

Sync Drive runs Syncthing in Docker so a normal Mac Finder folder can sync with the home server, similar to a Google Drive or Proton Drive workflow.

The user-facing folder on macOS is:

```text
$HOME/Server Drive
```

The canonical server-side storage folder is:

```text
/home/REDACTED_USER/file-manager/drive
```

Inside the Syncthing container, that server folder is mounted at:

```text
/data/drive
```

The Syncthing web UI is only for setup and administration. Day to day, use the Mac Finder folder like a normal local folder.

## Important Sync Notes

Syncthing is real file sync, not cloud placeholder storage. If 300 GB is synced, the Mac needs space for 300 GB unless folders are selectively synced.

Sync is not backup. Deleting a file on one side can delete it on the other side depending on the folder type and ignore rules. Keep backups of `/home/REDACTED_USER/file-manager/drive`.

## Server Setup

Run this on the Ubuntu server:

```bash
sudo mkdir -p /home/REDACTED_USER/syncthing/config /home/REDACTED_USER/file-manager/drive
sudo chown -R o:o /home/REDACTED_USER/syncthing /home/REDACTED_USER/file-manager
cd /home/REDACTED_USER/server-services/sync-drive
docker compose up -d
docker logs sync-drive
```

Or use the helper script from the repo root:

```bash
sudo ./scripts/setup-sync-drive-server.sh o /home/REDACTED_USER/server-services
```

Open the server Syncthing UI:

```text
http://<server-ip>:8384
```

Set a Syncthing GUI username and password immediately.

## Mac Setup

Run this on the Mac:

```bash
mkdir -p "$HOME/Server Drive"
brew install syncthing
brew services start syncthing
open "$HOME/Server Drive"
open http://127.0.0.1:8384
```

Or use the helper script from the repo root on the Mac:

```bash
./scripts/setup-sync-drive-mac.sh
```

The Mac Syncthing UI is:

```text
http://127.0.0.1:8384
```

Drag `$HOME/Server Drive` into the Finder sidebar to make it feel like a normal drive shortcut.

## Pairing Mac And Server

1. Open Syncthing on the Mac.
2. Copy the Mac Device ID.
3. Open Syncthing on the server.
4. Add the Mac device on the server.
5. Add the server device on the Mac if needed.
6. Share the server folder from path `/data/drive`.
7. Accept the folder on the Mac at `$HOME/Server Drive`.
8. Use `Send & Receive` on both sides for Google Drive-like behavior.

## Folder Type Options

`Send & Receive` on both sides is closest to Google Drive. Changes on either side sync to the other side.

Mac `Send Only`, server `Receive Only` creates a one-way Mac-to-server upload flow.

Server `Send Only`, Mac `Receive Only` creates a one-way server-to-Mac mirror.

Choose the folder type deliberately. Folder type affects how edits and deletes propagate.

## Security

Set a Syncthing GUI username and password immediately.

Do not expose port `8384` to the public internet. Use LAN, VPN, or Tailscale access only. This repo does not add public reverse proxy exposure, Cloudflare Tunnel exposure, or any public Syncthing endpoint.

Keep backups of `/home/REDACTED_USER/file-manager/drive`. Sync is not backup.

## Ports

| Port | Purpose |
|------|---------|
| 8384 | Syncthing admin UI |
| 22000/tcp | Sync protocol |
| 22000/udp | QUIC sync protocol |
| 21027/udp | Local discovery |

## Health Check

From the repo root on the server:

```bash
sudo ./scripts/check-sync-drive.sh
```
