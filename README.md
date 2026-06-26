# Server Services

All services running on the home server, organized for deployment on any Ubuntu 22.04 machine.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Ubuntu 22.04 Server                │
│                                                     │
│  :8001  🏠  Server Portal      (Python/Flask)       │
│  :8002  🦙  Ollama GUI         (Vue/Docker)         │
│  :8003  🤖  Twitter Bot Dash   (Python/Flask)       │
│  :8004  📁  File Manager + Drive (Node.js/Express)  │
│  :8005  🎙️  Whisper Transcriber (Python/Flask+GPU)  │
│  :8006  🔍  OCR Engine         (Python/Flask+GPU)   │
│  :8007  📊  System Stats       (Python/Flask)       │
│  :8008  🖼️  ML Image Studio    (Python/Flask+GPU)   │
│  :8009  📡  X-Bot Portal       (Uvicorn launcher)   │
│  :8010  🎥  VLC Stream         (directory indicator)│
│  :8011  🔐  Proton VPN Portal  (Python/Flask)       │
│  :8012  📄  PDF Portal         (Flask+WeasyPrint)   │
│                                                     │
│  Services managed by systemd                        │
│  VLC is shown as a non-clickable listener indicator │
│  Ollama GUI runs via Docker Compose                 │
└─────────────────────────────────────────────────────┘
```

## Screenshots

### Server Portal — Service Directory
![Server Portal](screenshots/portal-dashboard.png)

### System Stats — Live Monitoring
![System Stats](screenshots/system-stats-overview.png)

### Twitter Bot — Automation Dashboard
![Twitter Bot](screenshots/twitter-bot-dashboard.png)

### File Manager — Casting + LAN Server Drive

File Manager preserves its media, device, casting, streaming, and subtitle workflows while adding a native Drive section for permission-aware file management from the persistent library up to `/`.

> See each service's README for more screenshots: [Whisper Transcriber](whisper-transcriber/), [OCR Engine](ocr-engine/), [Ollama GUI](ollama-gui/)

## Quick Deploy

```bash
git clone <this-repo>
cd server-services
sudo ./deploy.sh <username> /home/<username>/server-services
```

The deploy script will:
1. Install system packages (Python 3, Chrome, Docker, ffmpeg, poppler)
2. Install Python dependencies for each service
3. Build and start Ollama GUI via Docker Compose
4. Create and enable systemd units for all services
5. Start everything

## Services

| Port | Service | Tech | Dir |
|------|---------|------|-----|
| 8001 | [Server Portal](server-portal/) | Python/Flask | `server-portal/` |
| 8002 | [Ollama GUI](ollama-gui/) | Vue/Vite + Docker | `ollama-gui/` |
| 8003 | [Twitter Bot Dashboard](twitter-bot/) | Python/Flask + Selenium | `twitter-bot/` |
| 8004 | [File Manager + Drive](cast-manager/) | Node.js/Express + Vue | `/home/REDACTED_USER/cast_manager_v3` |
| 8005 | [Whisper Transcriber](whisper-transcriber/) | Python/Flask + CUDA | `whisper-transcriber/` |
| 8006 | [OCR Engine](ocr-engine/) | Python/Flask + CUDA | `ocr-engine/` |
| 8007 | [System Stats](system-stats/) | Python/Flask | `system-stats/` |
| 8008 | [ML Image Studio](image-studio/) | Python/Flask + CUDA | `image-studio/` |
| 8009 | [X-Bot Portal](xbot-portal/) | systemd launcher for Uvicorn | `xbot-portal/` |
| 8010 | VLC Stream | Listener indicator only | external |
| 8011 | [Proton VPN Portal](vpn-portal/) | Python/Flask + Proton CLI | `vpn-portal/` |
| 8012 | [PDF Portal](https://github.com/lekandigital/pdf-gen) | Python/Flask + WeasyPrint | external: `lekandigital/pdf-gen` |

PDF Portal is an independent project. Deploy it separately from `lekandigital/pdf-gen`; this repository only links to and monitors its `pdf-portal.service`. Port 8012 is used on this host because 8009 is occupied by X-Bot Portal and 8010–8011 are already reserved.

```bash
git clone https://github.com/lekandigital/pdf-gen.git /home/<username>/pdf-gen
cd /home/<username>/pdf-gen
sudo ./deploy.sh <username> /home/<username>/pdf-gen 8012
```

## Integrated Drive storage and security

File Manager is deployed at `http://REDACTED_SERVER_IP:8004` as `cast-manager.service`. Its integrated Drive defaults to `/home/REDACTED_USER/file-manager/drive`; deployment creates that folder outside the repository so uploads survive redeploys. No separate process or systemd unit is deployed.

The browser is intentionally not jailed to the drive. It can navigate to `/`, `/home/REDACTED_USER`, `/etc`, and any other directory readable by the configured service user. Hidden files are visible by default. Normal Linux permissions control all reads and writes, and the service runs as the configured user rather than root.

This is a powerful LAN administration tool. Do not expose it publicly without VPN/Tailscale, strict firewall rules, or reverse-proxy authentication. See the [File Manager README](cast-manager/README.md) for Drive configuration and its manual checklist.

## System Requirements

- **OS:** Ubuntu 22.04 LTS
- **Python:** 3.10+
- **Node.js:** 20.20.0 (via nvm)
- **Docker:** 28+ (for Ollama GUI)
- **GPU:** NVIDIA with CUDA (for Whisper + OCR GPU acceleration)
- **RAM:** 8GB+ recommended (Whisper large-v3 model uses ~3GB VRAM)
- **Disk:** ~1GB extra for ML Image Studio model weights (auto-downloaded)

## Managing Services

```bash
# Check all service status
sudo systemctl status server-portal xb-dashboard faster-whisper paddleocr system-stats image-studio xbot-lan-dashboard proton-vpn-portal pdf-portal cast-manager

# Restart a service
sudo systemctl restart <service-name>

# View logs
journalctl -u <service-name> -f

# Stop all
for s in server-portal xb-dashboard faster-whisper paddleocr system-stats image-studio xbot-lan-dashboard proton-vpn-portal cast-manager; do sudo systemctl stop $s; done
```

## Private + Public Repo Workflow

This repository is intended to be your **private source-of-truth**.

To maintain a sanitized public mirror:

1. Add two remotes:
    - `origin` → private repo
    - `public` → public repo
2. Keep secret replacements in `.secrets-filter.txt` (gitignored).
3. Commit normally, then run:

```bash
./sync-repos.sh "your commit message"
```

The sync script pushes private changes to `origin`, then exports a filtered mirror to the public repo.

See [GIT_COMMIT_SYNCING_INSTRUCTIONS.md](GIT_COMMIT_SYNCING_INSTRUCTIONS.md) for full setup.

## Directory Structure

```
server-services/
├── README.md                    ← You are here
├── deploy.sh                    ← Master deploy script
├── .env.example                 ← Environment variable template
├── .gitignore
│
├── server-portal/               ← :8001
│   ├── portal.py
│   ├── services.json
│   ├── requirements.txt
│   ├── server-portal.service
│   └── README.md
│
├── ollama-gui/                  ← :8002
│   ├── src/                     (Vue components)
│   ├── public/
│   ├── nginx/
│   ├── compose.yml
│   ├── Dockerfile
│   ├── package.json
│   └── README.md               (original upstream docs)
│
├── twitter-bot/                 ← :8003 (+ :8001 portal)
│   ├── dashboard.py
│   ├── twitter_bot.py
│   ├── portal.py
│   ├── skip_trenton.py
│   ├── *.py                     (utilities)
│   ├── requirements.txt
│   ├── xb-dashboard.service
│   └── README.md
│
├── cast-manager/                ← :8004 (runtime: ~/cast_manager_v3)
│   ├── server.js
│   ├── lib/drive-routes.js      (integrated unrestricted Drive APIs)
│   ├── frontend/src/components/drive/
│   ├── cast-manager.service
│   ├── deploy.sh
│   └── README.md
│
├── whisper-transcriber/         ← :8005
│   ├── server.py
│   ├── static/index.html
│   ├── requirements.txt
│   ├── faster-whisper.service
│   └── README.md
│
├── ocr-engine/                  ← :8006
│   ├── server.py
│   ├── static/index.html
│   ├── requirements.txt
│   ├── paddleocr.service
│   └── README.md
│
├── system-stats/                ← :8007
│   ├── server.py
│   ├── requirements.txt
│   ├── system-stats.service
│   └── README.md
│
├── image-studio/                ← :8008
│   ├── server.py
│   ├── static/index.html
│   ├── requirements.txt
│   ├── image-studio.service
│   └── README.md
│
├── xbot-portal/                 ← :8009
│   ├── xbot-lan-dashboard.service
│   └── README.md
│
└── vpn-portal/                  ← :8011
    ├── server.py
    ├── requirements.txt
    ├── proton-vpn-portal.service
    └── README.md
```

## License

MIT — see individual service directories for any upstream license requirements (e.g., Ollama GUI).
