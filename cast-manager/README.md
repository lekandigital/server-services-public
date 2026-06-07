# Cast Manager v3 (:8004)

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

Cast Manager now separates device control from media preparation:

- Providers: `chromecast` and `airplay`
- Shared media pipeline: direct tokenized HTTP for safe MP4, HLS compatibility for MKV and timestamp-risky media, AAC stereo audio transcode, full H.264/AAC transcode when needed, VLC compatibility fallback when requested
- Receiver URLs are built from `CAST_PUBLIC_BASE_URL` or the LAN host, never `localhost`
- Active sessions are tracked by the app, so transient receiver status does not immediately hide playback controls

For MKV files, Cast Manager does not send raw MKV by default. It prepares HLS with stable timestamps and AAC stereo audio using ffmpeg options such as `-fflags +genpts`, `-avoid_negative_ts make_zero`, and `aresample=async=1:first_pts=0`.

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

Receiver mode is separate from casting to an Apple TV. Cast Manager does not fake an AirPlay receiver in Node; it manages a real receiver service.

For an Android TV target, prefer running a receiver on the Android TV itself. This deployment uses the open-source [`rcarmo/kotlin-airplay-receiver`](https://github.com/rcarmo/kotlin-airplay-receiver) APK sideloaded with ADB, with a local patch that avoids Android 14 mDNS name conflicts by advertising video mirroring as `<device name> AirPlay`. That makes Apple devices stream directly to the Android TV; the Ubuntu host is only used for Cast Manager, discovery diagnostics, and optional sender-side AirPlay.

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

## Run Locally

```bash
npm install
node server.js
# Serves on http://0.0.0.0:8004
```
