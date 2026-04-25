# System Stats (:8007)

Live system monitoring dashboard showing CPU, GPU, RAM, disk usage, and service health. Includes historical data stored in SQLite.

## Screenshots

### Dashboard Overview
Live metrics cards for uptime, GPU VRAM, RAM, CPU, disk, network, and GPU power — with auto-refresh and service health table.

![System Stats Overview](screenshots/dashboard-overview.png)

### Service Health & Historical Trends
Per-service status with PID and memory, plus CPU/RAM and GPU VRAM/temperature charts over time.

![Service Health & Trends](screenshots/dashboard-services.png)

## How It Works

- Flask server with inline HTML/CSS/JS dashboard
- Uses `psutil` for CPU/RAM/disk metrics
- Uses `nvidia-smi` subprocess for GPU metrics (NVIDIA only)
- Checks all service ports for online/offline status
- Stores historical metrics in SQLite (`stats_history.db`, auto-created)

## Dependencies

```
Python 3.10+
nvidia-smi (for GPU monitoring — comes with NVIDIA drivers)

pip packages:
  flask==3.1.2
  psutil==7.2.1
```

### System packages (Ubuntu 22.04)

```bash
pip3 install -r requirements.txt
# nvidia-smi is available if NVIDIA drivers are installed
```

## Files

| File | Purpose |
|------|---------|
| `server.py` | Flask app — API + inline dashboard |
| `stats_history.db` | SQLite metrics history (auto-created at runtime) |

## Run Locally

```bash
pip3 install -r requirements.txt
python3 server.py --port 8007
```
