# File Manager (:8004)

File Manager is deployed as `cast-manager.service` at
`http://REDACTED_SERVER_IP:8004`. Its Drive / Files section is part of the same app
and Node process; there is no separately deployed service. The
persistent runtime, SQLite database, and `.env` live at
`/home/REDACTED_USER/cast_manager_v3` and are preserved by `deploy.sh`.

Video file manager with provider-based casting, Transmission torrent integration, remote streaming, and AirPlay receiver management. Built with Node.js/Express + SQLite.

## Screenshots

### Home — Quick Cast & Torrent Status
File browser with one-click Chromecast casting, torrent integration, and storage overview.

![Cast Manager Home](screenshots/cast-manager-home.png)

## How It Works

- Express server manages a video library from the downloads directory
- Integrates with Transmission daemon for torrent management
- Casts to Chromecast via a Chromecast provider (`catt` for device control)
- Casts to AirPlay receivers via a Python `pyatv` sidecar
- Uses a shared media pipeline for direct HTTP, HLS compatibility, audio transcode, full transcode, and VLC fallback
- Normalizes MKV timestamps/audio by default instead of sending raw MKV to receivers
- Can manage an AirPlay receiver service such as UxPlay separately from sender mode
- Remote streaming with token-based auth
- SQLite database for video metadata and watch history
- Native Drive / Files page for browsing and managing server storage

## Integrated Drive / Files

Open **Drive / Files** in File Manager. It defaults to `/home/REDACTED_USER/file-manager/drive`, a persistent folder outside the repository and runtime directory. The server creates it when missing.

Drive can explicitly navigate to `/`, `/home/REDACTED_USER`, `/etc`, and other server paths. It is not jailed to the library. Hidden files are visible by default, folders sort first, and a toggle can hide dotfiles. Normal Linux permissions apply because the whole app runs as user `o`, not root; use ACLs for deliberately broader access rather than running File Manager as root.

Drop files onto the page to upload into the current directory, or drop them onto a visible folder row to target that folder. Uploads stream directly to the destination, and duplicate names become `name (1).ext`, `name (2).ext`, and so on. The view also supports folder creation, text/image/PDF/audio/video preview, download, rename, copy, confirmed move, and strongly confirmed permanent deletion.

Environment variables:

```bash
FILE_MANAGER_LIBRARY=/home/REDACTED_USER/file-manager/drive
FILE_MANAGER_MAX_UPLOAD_MB=4096
FILE_MANAGER_TEXT_PREVIEW_MB=2
```

This is a powerful LAN administration feature. Do not expose File Manager publicly without VPN/Tailscale, strict firewall rules, or reverse-proxy authentication.

## Dependencies

```
Node.js v20.20.0 (via nvm)
npm packages: see package.json

System services:
  - Transmission daemon (torrent client)
  - catt (Chromecast CLI tool — pip install catt)
  - ffmpeg (video transcoding)
  - Python 3 + pyatv sidecar for AirPlay casting
  - Optional: UxPlay + Avahi for AirPlay receiver mode
  - SSH access for remote file operations
```

### System packages (Ubuntu 22.04)

```bash
# Node.js via nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
nvm install 20.20.0

# System deps
sudo apt install -y ffmpeg transmission-daemon
pip3 install catt

# Install npm packages
cd cast-manager
npm install
```

## Environment Variables (.env)

```
SSH_HOST=your-server-ip
SSH_USER=your-user
SSH_PASSWORD=<your-password>
TRANSMISSION_USER=transmission
TRANSMISSION_PASS=<your-password>
DOWNLOAD_DIR=/home/your-user/watch_list
CHROMECAST_NAME=Living Room TV
CATT_PATH=/home/your-user/.local/bin/catt
PORT=8004
NODE_ENV=production
PUBLIC_URL=
CAST_PUBLIC_BASE_URL=http://REDACTED_SERVER_IP:8004
FILE_MANAGER_LIBRARY=/home/your-user/file-manager/drive
FILE_MANAGER_MAX_UPLOAD_MB=4096
FILE_MANAGER_TEXT_PREVIEW_MB=2
CAST_BACKEND_DEFAULT=auto
CAST_ENABLE_HLS_BACKEND=1
CAST_ENABLE_VLC_BACKEND=1
AIRPLAY_SIDECAR_URL=http://127.0.0.1:8765
STREAM_TOKEN_EXPIRY_HOURS=24
TRANSCODE_CACHE_DIR=/tmp/cast_manager_cache
TRASH_DIR=/home/your-user/.cast_manager/trash
```

Do not commit real passwords or pairing credentials. Use `.env`, systemd environment files, or existing deployment secrets.

## Casting Architecture

The casting architecture separates device control from media preparation:

- Providers: `chromecast` and `airplay`
- Shared media pipeline: direct tokenized HTTP for safe MP4, HLS compatibility for MKV and timestamp-risky media, AAC stereo audio transcode, full H.264/AAC transcode when needed, VLC compatibility fallback when requested
- Receiver URLs are built from `CAST_PUBLIC_BASE_URL` or the LAN host, never `localhost`
- Active sessions are tracked by the app, so transient receiver status does not immediately hide playback controls

For MKV files, File Manager does not send raw MKV by default. It prepares HLS with stable timestamps and AAC stereo audio using ffmpeg options such as `-fflags +genpts`, `-avoid_negative_ts make_zero`, and `aresample=async=1:first_pts=0`.

## AirPlay Casting

AirPlay sender support is handled by the sidecar in `sidecars/airplay`:

```bash
cd sidecars/airplay
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn airplay_sidecar:app --host 127.0.0.1 --port 8765
```

Then set:

```bash
AIRPLAY_SIDECAR_URL=http://127.0.0.1:8765
```

AirPlay limitations:

- DRM-protected media is not supported.
- Screen mirroring is not part of sender mode.
- Pairing may be required depending on the receiver.
- Seek/status support varies by receiver and tvOS version.

## AirPlay Receiver Mode

Receiver mode is separate from casting to an Apple TV. File Manager does not fake an AirPlay receiver in Node; it manages a real receiver service.

For an Android TV target, prefer running a receiver on the Android TV itself. This deployment uses the open-source [`rcarmo/kotlin-airplay-receiver`](https://github.com/rcarmo/kotlin-airplay-receiver) APK sideloaded with ADB, with a local patch that avoids Android 14 mDNS name conflicts by advertising video mirroring as `<device name> AirPlay`. That makes Apple devices stream directly to the Android TV; the Ubuntu host is only used for File Manager, discovery diagnostics, and optional sender-side AirPlay.

Recommended packages:

```bash
sudo apt install -y avahi-daemon uxplay \
  gstreamer1.0-libav gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-base \
  gstreamer1.0-pulseaudio
```

The example service is set up for a GNOME/X11 desktop on `:0` and uses `ximagesink`. If your receiver host is headless or uses a different display server, adjust `DISPLAY`, `XAUTHORITY`, `XDG_RUNTIME_DIR`, and the `-vs` sink before enabling the service.

Use `systemd/uxplay-receiver.service.example` as a starting point:

```bash
sudo cp systemd/uxplay-receiver.service.example /etc/systemd/system/uxplay-receiver.service
sudo systemctl daemon-reload
sudo systemctl enable --now avahi-daemon
sudo systemctl enable --now uxplay-receiver
```

Optional audio-only receiver mode can be provided by `shairport-sync` instead. DRM and full AirPlay 2 multiroom behavior are not guaranteed.

## Files

| File | Purpose |
|------|---------|
| `server.js` | Express app — API + SSE + streaming |
| `db.js` | SQLite database layer |
| `package.json` | npm dependencies |
| `public/` | Frontend HTML/CSS/JS |
| `routes/` | Express route modules |
| `lib/media/` | Shared media pipeline, HLS jobs, receiver URL helpers |
| `lib/cast/` | Chromecast/AirPlay providers and session helpers |
| `sidecars/airplay/` | pyatv AirPlay sender sidecar |
| `systemd/` | Service examples |
| `deploy.sh` | Deployment helper script |
| `.env` | Environment configuration |

## Cast Reliability (Chromecast / Android TV)

Default casting method is **Auto**, which scores backends and falls back on failure. The orchestrator verifies the TV actually requested the stream URL before reporting success.

### Key env vars (Ubuntu server)

```bash
CAST_PUBLIC_BASE_URL=http://REDACTED_SERVER_IP:8004   # LAN URL the TV can reach
CAST_BACKEND_DEFAULT=auto
CAST_ADB_SERIAL=14291HFDD2RTE3                   # optional; USB serial auto-detected
CHROMECAST_NAME=REDACTED_DEVICE
CATT_PATH=/home/REDACTED_USER/.local/bin/catt
```

Connect to the TV via **SSH to Ubuntu**, then **USB ADB** (device plugged into the Ubuntu host). Multiple ADB endpoints may appear; USB is preferred automatically.

### Diagnostics

```bash
curl http://REDACTED_SERVER_IP:8004/api/cast/doctor
curl -X POST http://REDACTED_SERVER_IP:8004/api/cast/preflight \
  -H 'Content-Type: application/json' \
  -d '{"filePath":"/path/to/video.mp4","backend":"auto"}'
curl http://REDACTED_SERVER_IP:8004/api/cast/diagnostics
curl -OJ http://REDACTED_SERVER_IP:8004/api/cast/doctor/bundle
```

Preflight returns structured JSON (`stage`, `blocking`, `message`, `suggestedFix`). Only fatal issues block casting (localhost stream URL, catt cannot reach receiver, invalid stream URL). ADB noise, scan misses, and live-stream probe skips are warnings only.

### Test scripts (run on Ubuntu)

```bash
bash scripts/cast-diagnose-env.sh
bash scripts/cast-create-test-media.sh
export CAST_TEST_FILE_PATH=/tmp/cast-manager-test-media/known_good_h264_aac.mp4
export CAST_ADB_SERIAL=14291HFDD2RTE3
bash scripts/cast-e2e-adb-test.sh --backend auto
bash scripts/cast-control-e2e-adb.sh --backend auto
```

See `docs/cast-reliability-worklog.md` for pass/fail history and `docs/cast-reliability-next-steps.md` for remaining work (subtitles burn-in, HLS with `CAST_ENABLE_HLS_BACKEND=1`, library file tests).

## Run Locally

```bash
npm install
node server.js
# Serves on http://0.0.0.0:8004
```

## Drive manual test checklist

1. Visit `http://REDACTED_SERVER_IP:8004` and confirm the File Manager dashboard and casting controls remain present.
2. Open **Drive / Files** and confirm `/home/REDACTED_USER/file-manager/drive` opens.
3. Confirm hidden files are visible by default after navigating to `/home/REDACTED_USER`.
4. Drop a file onto the page, then create a subfolder and drop a second file directly onto that folder row.
5. Navigate to `/`, `/home/REDACTED_USER`, and `/etc`; try `/root` and confirm a clean permission error.
6. Preview text and image files, plus a PDF if available, and download a file.
7. Rename, copy, move, and confirmed-delete test files only.
8. Upload the same filename twice and confirm the original is not overwritten.
9. Restart `cast-manager.service`; confirm both casting and Drive still work on port 8004.
