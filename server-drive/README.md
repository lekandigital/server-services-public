# Server Drive

Server Drive mounts the **entire server filesystem** as a native volume in the macOS Finder
sidebar under **Locations**, the way Google Drive or an external disk appears. Browse it, open
files from it, and drag and drop straight onto it.

It uses SMB, which macOS speaks natively — nothing to install on the Mac, no macFUSE, no
kernel extensions.

```text
smb://o@REDACTED_SERVER_WIRED_IP/Server   →   /Volumes/Server     (wired enp4s0)
smb://o@REDACTED_SERVER_IP/Server   →   fallback            (wifi wlp2s0)
```

## Server Drive vs Sync Drive

The repo has two different Finder integrations. They solve different problems and coexist
happily.

| | **Server Drive** (this service) | **[Sync Drive](../sync-drive/)** (Syncthing) |
|---|---|---|
| Coverage | Whole filesystem, `/` down | One folder, `/home/REDACTED_USER/file-manager/drive` |
| Mac disk used | None — files stream on demand | Full local copy of everything synced |
| Works offline | No, it's a network mount | Yes, it's a local folder |
| Feels like | A mounted network drive | A local folder |
| Good for | Reaching anything on the box, moving large files, editing configs | Files you want on both machines all the time |

Use Server Drive to *reach* the server. Use Sync Drive to *mirror* a working folder.

## What gets configured

**Server**

- Samba (`smbd`) serving a single share, `Server`, rooted at `/`
- Bonjour advertisement via the already-running `avahi-daemon`, so the box appears in Finder
  by itself and with a Mac Pro icon
- `nmbd` masked — NetBIOS is SMB1-era and macOS doesn't need it

**Mac**

- SMB password stored in the login Keychain
- A `com.lekan.serverdrive` LaunchAgent that mounts at login, on network changes, and every
  minute as a backstop — wired address first, wifi second
- `.DS_Store` creation on network volumes turned off

## Access model

The share runs as **root** with full read/write across the whole filesystem. `/etc`, `/var`,
`/home/REDACTED_USER`, and `/media/o/IMAGES` are all writable from Finder.

Two things keep that from being reckless:

- `inherit owner = unix only` — a file dragged into `/home/REDACTED_USER/…` ends up owned by `o`, not
  `root`, so services running as `o` can still modify it. Without this, every Finder drop would
  land root-owned and quietly break things. (`inherit permissions` is deliberately **off**: on
  Samba 4.15 it makes smbd PANIC when Finder writes a metadata stream, killing the session.)
- `bind interfaces only = yes` with `interfaces = lo en* wl*` — the share is bound to loopback,
  ethernet and wifi only. It is **never** bound to `proton0`, `ipv6leakintrf0` or the docker
  bridges, so it is not reachable over the VPN tunnel. `check-server-drive.sh` asserts this on
  every run. (Not by CIDR: a CIDR entry binds only the *first* interface in the subnet, which
  left the wired port unbound.)

It is still a root-writable share. A careless drag into `/Volumes/Server/etc` really will do
what it says.

## Deletes and the recycle bin

Deleting over SMB is **permanent and immediate**. Finder's *Move to Trash* on a network volume
does not use the Mac's Trash and does not create a `.Trashes` on the server — the file is
unlinked. There is no undo, and this share reaches system files.

So the share runs Samba's `recycle` module: deletes are moved to `/.recycle/<user>/`, keeping
the original directory tree.

```bash
sudo find /.recycle -type f            # what is in the bin
sudo cp -a /.recycle/o/home/REDACTED_USER/notes.md /home/REDACTED_USER/notes.md   # restore
```

The bin is root-owned and `0700` — it holds files deleted from anywhere, including root-only
paths, so it must not be world-readable.

Limits, because `/` only has ~78 GB free:

| Setting | Value | Why |
|---|---|---|
| `recycle:maxsize` | 500 MB | Larger files are deleted outright rather than filling the disk |
| Retention | 30 days | `server-drive-recycle-prune.timer`, daily |
| Excluded | `*.tmp`, `*.pyc`, `.DS_Store`, `node_modules`, `.git`, `/proc`, `/sys`, … | Churn not worth keeping |

Two things it does **not** cover:

- **`/media/o/IMAGES`** — recycling is a rename, which cannot cross a filesystem boundary.
  Deletes on the exfat drive stay permanent.
- **Overwrites** — dragging a file onto an existing one replaces it without recycling.

Prune manually with `sudo ./server-drive/prune-recycle.sh 30` (add `--dry-run` to preview).

## Setup

**Server** (from the repo root on the box):

```bash
sudo ./server-drive/setup-server-drive.sh o
```

You'll be asked once for a Samba password for user `o`.

> **Samba keeps its own password database.** It cannot read the Unix password hash, so this is a
> *separate* password from the Linux login/sudo password even when you choose the same string.
> Changing one never changes the other. To set it later: `sudo smbpasswd o`. To script the
> initial setup, export `SMB_PASSWORD` first.

**Mac**:

```bash
./server-drive/mac/setup-server-drive-mac.sh
```

Finder will ask for the password once. **Tick "Remember this password in my keychain"** — that
is what makes every later mount silent. The script waits for the connection, then installs the
auto-mount agent.

Optionally pass different hosts (tried in order), user, or share name:

```bash
./server-drive/mac/setup-server-drive-mac.sh "REDACTED_SERVER_WIRED_IP REDACTED_SERVER_IP" o Server
```

Keychain entries are per address. Setup connects to the first one; for each fallback, connect
once via Finder (`open smb://o@REDACTED_SERVER_IP/Server`) and tick *Remember*. Entries written with
the `security` CLI do not work — NetAuth only trusts ones it created (partition `apple:`).

## Health check

```bash
sudo ./server-drive/check-server-drive.sh
```

Checks `smbd`, `testparm`, the port-445 bindings (including the VPN-leak assertion), the Samba
user, and the Bonjour advertisement.

## Everyday use

The drive lives in the Finder sidebar under **Locations**. Nothing else to do — it remounts
itself after reboots, sleep, and rejoining the network. Off the home network the LaunchAgent
is a silent no-op.

To unmount by hand, click the eject arrow. To remount:

```bash
launchctl kickstart -k gui/$(id -u)/com.lekan.serverdrive
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| Drive doesn't appear | `tail ~/Library/Logs/server-drive.log`; confirm you're on the home LAN |
| Drive drops every few minutes | Something is restarting smbd: `journalctl -u smbd -u server-drive-rebind --since -1h`. Count restarts with `journalctl -u smbd --since today \| grep -c Started` — it should be ~0 |
| "Server connection interrupted" after writing a file | smbd crashed: `sudo grep -l PANIC /var/log/samba/log.*`. Check `inherit permissions` has not crept back into smb.conf |
| Mounted via `.152` instead of `.209` | Log says why (`skipped earlier addresses: …`). Usually no Keychain entry for `.209` — connect once via Finder |
| Finder asks for a password every time | The Keychain entry didn't take. Connect once via `open smb://o@REDACTED_SERVER_WIRED_IP/Server` and tick *Remember this password in my keychain* |
| "There was a problem connecting" | On the server: `sudo systemctl status smbd` and `sudo ./server-drive/check-server-drive.sh` |
| Copies are slow | Check the mount source with `mount \| grep smbfs` — `.152` is the box's wifi. Eject and let the agent remount on `.209` |
| Files land owned by `root` | `inherit owner` is missing from `/etc/samba/smb.conf`; re-run the setup script |

Change the Samba password:

```bash
sudo smbpasswd o                                                          # server
security delete-internet-password -a o -s REDACTED_SERVER_WIRED_IP -r 'smb '        # Mac, then re-run setup
security delete-internet-password -a o -s REDACTED_SERVER_IP -r 'smb '        # …and reconnect the fallback
```

## Off-LAN access over Tailscale

The drive follows you off the home network over Tailscale. smbd cannot bind `tailscale0` (a
point-to-point tun the Samba interface probe skips), so off-LAN traffic reaches the plain LAN
address through the box's advertised `REDACTED_LAN_SUBNET` subnet route — `hosts allow` admits the
`100.64.0.0/10` source addresses. `SERVER_TAILSCALE` in
`~/Library/Application Support/ServerDrive/config.sh` is only needed if the route is not
approved.

Requirements:

1. Server logged in: `sudo tailscale up`. The box also advertises `REDACTED_LAN_SUBNET` as a subnet
   route — approve it in the Tailscale admin console and the plain LAN address keeps working
   from anywhere, with no second Keychain entry needed.
2. Tailscale running on the Mac, same tailnet.
3. Nothing on the smbd side — the LAN address is already bound.

### Why the rebind watchdog exists

`bind interfaces only = yes` means `smbd` binds the addresses that exist *at startup*. Plug in
the Anker dock or let DHCP move an address afterwards and `smbd` knows nothing about it until it
restarts.

`server-drive-rebind.timer` runs `rebind-check.sh` every 5 minutes: it compares the IPv4
addresses of the interfaces named in smb.conf's `interfaces` line against the ones `smbd`
actually holds, and restarts it on a mismatch. A restart drops every Finder session, so:

- it never restarts while a client is connected (that client is already being served);
- it restarts at most **once** per distinct set of missing addresses — if that did not bind
  them, smbd cannot, and it stays quiet until the set changes.

The previous version expected the Tailscale address (which smbd can never bind) and the wired
address (which the CIDR `interfaces` line skipped), with only a 10-minute cooldown, so from
2026-10-22 it restarted smbd 144 times a day.

```bash
sudo ./server-drive/rebind-check.sh --dry-run   # report without restarting
```

On the LAN, Bonjour is pinned to the wired port (`allow-interfaces=enp4s0` in
`/etc/avahi/avahi-daemon.conf`, hand-edited, not rendered by setup) so the sidebar entry and
`o-MacPro.local` resolve to `.209`. Publishing on both interfaces would give the host two
addresses on the same subnet, and the Mac would pick either — including the flaky wifi one.

Bonjour does not cross Tailscale, so off-LAN the server will not appear in the sidebar by
itself — the agent mounts it explicitly instead. Expect noticeably slower transfers over the
tunnel than on the LAN.

Do **not** forward port 445 to the internet.

## Ports

| Port | Purpose |
|------|---------|
| 445 | SMB3 (the drive itself) |
| 5353/udp | Bonjour discovery (existing `avahi-daemon`) |

## Files

| File | Purpose |
|---|---|
| `smb.conf.template` | Samba config, rendered to `/etc/samba/smb.conf` |
| `avahi-smb.service` | Bonjour advertisement → `/etc/avahi/services/smb.service` |
| `setup-server-drive.sh` | Server install (idempotent) |
| `check-server-drive.sh` | Server health check |
| `prune-recycle.sh` | Delete recycled files older than N days |
| `rebind-check.sh` | Restart `smbd` (once, only when idle) when an interface address appears |
| `systemd/server-drive-recycle-prune.{service,timer}` | Daily recycle prune |
| `systemd/server-drive-rebind.{service,timer}` | 5-minute rebind watchdog |
| `mac/setup-server-drive-mac.sh` | Mac install (idempotent) |
| `mac/server-drive-mount.sh` | Idempotent mount helper run by the LaunchAgent |
| `mac/com.lekan.serverdrive.plist` | LaunchAgent template |
