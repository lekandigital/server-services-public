#!/usr/bin/env python3
"""System stats dashboard for the remote host."""

import argparse
import csv
import os
import socket
import subprocess
import time
from datetime import datetime
import threading
import sqlite3

import psutil
from flask import Flask, Response, jsonify, request

app = Flask(__name__)

HOST_IP = "REDACTED_SERVER_IP"
SERVICE_TARGETS = [
    {"name": "Server Portal", "port": 8001, "unit": "server-portal.service"},
    {"name": "Ollama GUI", "port": 8002, "unit": "ollama.service", "url": "http://REDACTED_SERVER_IP:8002/"},
    {"name": "Twitter Bot Dashboard", "port": 8003, "unit": "xb-dashboard.service"},
    {"name": "Video Manager", "port": 8004, "unit": "cast-manager.service"},
    {"name": "Whisper Transcriber", "port": 8005, "unit": "faster-whisper.service"},
    {"name": "OCR Engine", "port": 8006, "unit": "paddleocr.service"},
    {"name": "System Stats", "port": 8007, "unit": "system-stats.service"},
]

# ── History tracking (SQLite-backed) ──
HISTORY_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats_history.db")
_prev_net = {"sent": 0, "recv": 0, "ts": 0}
_db_lock = threading.Lock()

def _init_history_db():
    conn = sqlite3.connect(HISTORY_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        cpu REAL, ram REAL, gpu_vram REAL, gpu_temp REAL,
        gpu_power REAL, disk REAL, net_rx REAL, net_tx REAL, load_avg REAL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON snapshots(ts)")
    conn.commit()
    conn.close()

def _store_snapshot(snap):
    with _db_lock:
        conn = sqlite3.connect(HISTORY_DB)
        conn.execute(
            "INSERT INTO snapshots (ts,cpu,ram,gpu_vram,gpu_temp,gpu_power,disk,net_rx,net_tx,load_avg) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (snap["ts"], snap["cpu"], snap["ram"], snap["gpu_vram"], snap["gpu_temp"],
             snap["gpu_power"], snap["disk"], snap["net_rx"], snap["net_tx"], snap["load"])
        )
        conn.commit()
        conn.close()

def _query_history(since_ts=None, limit=720):
    with _db_lock:
        conn = sqlite3.connect(HISTORY_DB)
        conn.row_factory = sqlite3.Row
        if since_ts:
            rows = conn.execute(
                "SELECT * FROM snapshots WHERE ts >= ? ORDER BY ts ASC", (since_ts,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM snapshots ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
            rows = list(reversed(rows))
        conn.close()
    return [{"ts": r["ts"], "cpu": r["cpu"], "ram": r["ram"], "gpu_vram": r["gpu_vram"],
             "gpu_temp": r["gpu_temp"], "gpu_power": r["gpu_power"], "disk": r["disk"],
             "net_rx": r["net_rx"], "net_tx": r["net_tx"], "load": r["load_avg"]} for r in rows]

def _downsample(rows, max_points=800):
    if len(rows) <= max_points:
        return rows
    step = len(rows) / max_points
    result = []
    i = 0.0
    while i < len(rows):
        result.append(rows[int(i)])
        i += step
    return result

def record_snapshot(stats_fn):
    global _prev_net
    try:
        data = stats_fn()
        gpu = (data.get("gpus") or [{}])[0] if data.get("gpus") else {}
        net = data.get("network", {})
        now = time.time()
        dt = now - _prev_net["ts"] if _prev_net["ts"] else 10
        if dt < 1: dt = 1
        rx_rate = (net.get("bytes_recv", 0) - _prev_net["recv"]) / dt if _prev_net["ts"] else 0
        tx_rate = (net.get("bytes_sent", 0) - _prev_net["sent"]) / dt if _prev_net["ts"] else 0
        _prev_net = {"sent": net.get("bytes_sent", 0), "recv": net.get("bytes_recv", 0), "ts": now}
        gpu_total = gpu.get("memory_total_mb", 1) or 1
        snap = {
            "ts": now,
            "cpu": data.get("cpu", {}).get("usage_percent", 0),
            "ram": round(data.get("memory", {}).get("percent", 0), 1),
            "gpu_vram": round((gpu.get("memory_used_mb", 0) / gpu_total) * 100, 1) if gpu else 0,
            "gpu_temp": gpu.get("temp_c", 0) if gpu else 0,
            "gpu_power": gpu.get("power_draw_w", 0) if gpu else 0,
            "disk": data.get("disk", {}).get("percent", 0),
            "net_rx": round(rx_rate),
            "net_tx": round(tx_rate),
            "load": (data.get("load_avg") or [0])[0],
        }
        _store_snapshot(snap)
    except Exception:
        pass

def start_history_collector():
    _init_history_db()
    def _loop():
        while True:
            record_snapshot(stats_payload)
            time.sleep(10)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()



def run_command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
        if result.returncode != 0:
            return ""
        return result.stdout.strip()
    except Exception:
        return ""


def format_uptime(seconds):
    seconds = int(max(0, seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if days or hours:
        parts.append(f"{hours}h")
    if days or hours or minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def find_cpu_temperature():
    try:
        temps = psutil.sensors_temperatures(fahrenheit=False)
    except Exception:
        return None

    if not temps:
        return None

    preferred_keys = ("coretemp", "k10temp", "cpu_thermal", "acpitz")
    readings = []
    for key in preferred_keys:
        readings.extend(temps.get(key, []))
    if not readings:
        for entries in temps.values():
            readings.extend(entries)

    values = [entry.current for entry in readings if getattr(entry, "current", None) is not None]
    if not values:
        return None

    return round(max(values), 1)


def get_gpu_stats():
    query = (
        "index,name,temperature.gpu,utilization.gpu,memory.used,memory.total,"
        "power.draw,power.limit,fan.speed"
    )
    output = run_command(
        ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"]
    )
    if not output:
        return []

    rows = []
    reader = csv.reader(output.splitlines())
    for row in reader:
        if len(row) != 9:
            continue
        rows.append(
            {
                "index": int(row[0].strip()),
                "name": row[1].strip(),
                "temp_c": float(row[2].strip()),
                "util_percent": float(row[3].strip()),
                "memory_used_mb": int(float(row[4].strip())),
                "memory_total_mb": int(float(row[5].strip())),
                "power_draw_w": float(row[6].strip()),
                "power_limit_w": float(row[7].strip()),
                "fan_percent": None if row[8].strip() == "[N/A]" else float(row[8].strip()),
            }
        )

    proc_output = run_command(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_memory",
            "--format=csv,noheader,nounits",
        ]
    )
    gpu_processes = []
    if proc_output:
        for line in proc_output.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 3:
                continue
            gpu_processes.append(
                {
                    "pid": int(parts[0]),
                    "name": parts[1],
                    "memory_mb": int(float(parts[2])),
                }
            )

    for row in rows:
        row["processes"] = gpu_processes
    return rows


def port_is_open(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        return sock.connect_ex(("127.0.0.1", int(port))) == 0
    finally:
        sock.close()


def service_snapshot():
    services = []
    for item in SERVICE_TARGETS:
        show = run_command(
            ["systemctl", "show", item["unit"], "-p", "ActiveState", "-p", "SubState", "-p", "MainPID"]
        )
        state = "unknown"
        sub_state = ""
        pid = 0
        for line in show.splitlines():
            if line.startswith("ActiveState="):
                state = line.split("=", 1)[1]
            elif line.startswith("SubState="):
                sub_state = line.split("=", 1)[1]
            elif line.startswith("MainPID="):
                try:
                    pid = int(line.split("=", 1)[1])
                except ValueError:
                    pid = 0

        memory_rss = None
        cpu_percent = None
        process_name = None
        if pid > 0:
            try:
                proc = psutil.Process(pid)
                memory_rss = proc.memory_info().rss
                cpu_percent = proc.cpu_percent(interval=0.0)
                process_name = proc.name()
            except Exception:
                pass

        services.append(
            {
                "name": item["name"],
                "unit": item["unit"],
                "port": item["port"],
                "url": item.get("url", f"http://REDACTED_SERVER_IP:{item['port']}/"),
                "online": port_is_open(item["port"]),
                "active_state": state,
                "sub_state": sub_state,
                "pid": pid,
                "memory_rss": memory_rss,
                "cpu_percent": cpu_percent,
                "process_name": process_name,
            }
        )
    return services


def top_processes(limit=8):
    processes = []
    for proc in psutil.process_iter(["pid", "name", "username", "memory_info", "cpu_percent", "cmdline"]):
        try:
            info = proc.info
            memory_rss = info["memory_info"].rss if info.get("memory_info") else 0
            cmdline = " ".join(info.get("cmdline") or [])
            processes.append(
                {
                    "pid": info["pid"],
                    "name": info.get("name") or "",
                    "user": info.get("username") or "",
                    "memory_rss": memory_rss,
                    "cpu_percent": info.get("cpu_percent") or 0.0,
                    "cmdline": cmdline[:180],
                }
            )
        except Exception:
            continue

    processes.sort(key=lambda item: item["memory_rss"], reverse=True)
    return processes[:limit]


def stats_payload():
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk_root = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    boot_time = psutil.boot_time()
    now = time.time()

    return {
        "host": HOST_IP,
        "hostname": socket.gethostname(),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "uptime_seconds": int(now - boot_time),
        "uptime_text": format_uptime(now - boot_time),
        "load_avg": [round(value, 2) for value in os.getloadavg()],
        "cpu": {
            "usage_percent": round(psutil.cpu_percent(interval=0.15), 1),
            "cores_logical": psutil.cpu_count() or 0,
            "cores_physical": psutil.cpu_count(logical=False) or 0,
            "temp_c": find_cpu_temperature(),
        },
        "memory": {
            "used": vm.used,
            "available": vm.available,
            "total": vm.total,
            "percent": round(vm.percent, 1),
        },
        "swap": {
            "used": swap.used,
            "total": swap.total,
            "percent": round(swap.percent, 1),
        },
        "disk": {
            "used": disk_root.used,
            "free": disk_root.free,
            "total": disk_root.total,
            "percent": round(disk_root.percent, 1),
        },
        "network": {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
        },
        "gpus": get_gpu_stats(),
        "services": service_snapshot(),
        "top_processes": top_processes(),
    }


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/stats")
def api_stats():
    return jsonify(stats_payload())



@app.route("/api/history")
def api_history():
    range_param = request.args.get("range", "2h")
    range_map = {"30m": 1800, "1h": 3600, "2h": 7200, "6h": 21600, "12h": 43200,
                 "1d": 86400, "7d": 604800, "30d": 2592000, "all": 0}
    seconds = range_map.get(range_param, 7200)
    if seconds > 0:
        since = time.time() - seconds
        rows = _query_history(since_ts=since, limit=10000)
    else:
        rows = _query_history(limit=100000)
    return jsonify(_downsample(rows))


@app.route("/api/history/stats")
def api_history_stats():
    with _db_lock:
        conn = sqlite3.connect(HISTORY_DB)
        row = conn.execute("SELECT COUNT(*) as cnt, MIN(ts) as oldest, MAX(ts) as newest FROM snapshots").fetchone()
        db_size = os.path.getsize(HISTORY_DB) if os.path.exists(HISTORY_DB) else 0
        conn.close()
    return jsonify({"count": row[0], "oldest": row[1], "newest": row[2], "db_size_bytes": db_size})

@app.route("/")
def index():
    return Response(HTML_PAGE, mimetype="text/html")


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>System Stats</title>
  <style>
    :root {
      --bg: #0b1220;
      --panel: #111a2c;
      --panel-alt: #0f1728;
      --border: #22304a;
      --text: #e5eefb;
      --muted: #8fa3bf;
      --accent: #4ade80;
      --warn: #fb7185;
      --gold: #f59e0b;
      --blue: #38bdf8;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top right, rgba(56, 189, 248, 0.12), transparent 28%),
        radial-gradient(circle at top left, rgba(74, 222, 128, 0.10), transparent 26%),
        var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 28px 18px 40px;
    }
    .shell {
      width: min(1180px, 100%);
      margin: 0 auto;
    }
    .hero {
      display: grid;
      grid-template-columns: 1.4fr 1fr;
      gap: 16px;
      margin-bottom: 16px;
    }
    .panel {
      background: linear-gradient(180deg, rgba(17, 26, 44, 0.98), rgba(12, 19, 33, 0.98));
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 18px;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.22);
    }
    .title {
      font-size: 34px;
      font-weight: 700;
      margin: 0 0 6px;
      letter-spacing: -0.03em;
    }
    .subtitle {
      color: var(--muted);
      margin: 0;
      font-size: 14px;
    }
    .stamp {
      display: inline-flex;
      gap: 8px;
      align-items: center;
      margin-top: 18px;
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(56, 189, 248, 0.09);
      border: 1px solid rgba(56, 189, 248, 0.18);
      color: var(--blue);
      font-size: 13px;
    }
    .headline-metrics {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .headline-metric {
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px;
    }
    .label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .value {
      margin-top: 8px;
      font-size: 28px;
      font-weight: 700;
      letter-spacing: -0.03em;
    }
    .subvalue {
      color: var(--muted);
      margin-top: 6px;
      font-size: 13px;
    }
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 16px;
      margin-bottom: 16px;
    }
    .metric-card {
      min-height: 140px;
    }
    .table-wrap {
      overflow-x: auto;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }
    th, td {
      text-align: left;
      padding: 12px 10px;
      border-bottom: 1px solid rgba(143, 163, 191, 0.14);
      vertical-align: top;
    }
    th {
      color: var(--muted);
      font-weight: 600;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .status-chip {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 12px;
      border-radius: 999px;
      padding: 4px 10px;
      border: 1px solid rgba(143, 163, 191, 0.2);
      color: var(--text);
    }
    .status-chip::before {
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--warn);
    }
    .status-chip.ok::before {
      background: var(--accent);
      box-shadow: 0 0 10px rgba(74, 222, 128, 0.5);
    }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 13px;
    }
    .grid-two {
      display: grid;
      grid-template-columns: 1.4fr 1fr;
      gap: 16px;
    }
    .list {
      display: grid;
      gap: 12px;
    }
    .process-row {
      display: grid;
      grid-template-columns: minmax(0, 1.6fr) auto;
      gap: 10px;
      padding: 10px 0;
      border-bottom: 1px solid rgba(143, 163, 191, 0.12);
    }
    .process-row:last-child {
      border-bottom: 0;
      padding-bottom: 0;
    }
    .process-name {
      font-weight: 600;
      margin-bottom: 4px;
    }
    .muted {
      color: var(--muted);
    }
    @media (max-width: 980px) {
      .hero, .metric-grid, .grid-two {
        grid-template-columns: 1fr;
      }
    }
    .range-btn {
      background: rgba(56,189,248,0.08);
      border: 1px solid rgba(56,189,248,0.2);
      color: var(--blue);
      padding: 5px 12px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
    }
    .range-btn:hover {
      background: rgba(56,189,248,0.18);
    }
    .range-btn.active {
      background: var(--blue);
      color: var(--bg);
      border-color: var(--blue);
    }
    .chart-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
    }
    .chart-panel {
      padding: 18px;
    }
    .chart-panel canvas {
      width: 100%;
      height: 180px;
      display: block;
    }
    .chart-title {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 12px;
    }
    .chart-legend {
      display: flex;
      gap: 16px;
      margin-top: 10px;
    }
    .legend-item {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      color: var(--muted);
    }
    .legend-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      display: inline-block;
    }
    @media (max-width: 980px) {
      .range-btn {
      background: rgba(56,189,248,0.08);
      border: 1px solid rgba(56,189,248,0.2);
      color: var(--blue);
      padding: 5px 12px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
    }
    .range-btn:hover {
      background: rgba(56,189,248,0.18);
    }
    .range-btn.active {
      background: var(--blue);
      color: var(--bg);
      border-color: var(--blue);
    }
    .chart-grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="panel">
        <h1 class="title">System Stats</h1>
        <p class="subtitle">Live machine health for REDACTED_SERVER_IP. This page focuses on the things that usually matter when a box feels "off": temperatures, VRAM, RAM, disk pressure, uptime, and service health.</p>
        <div class="stamp">
          <span>Auto-refreshing</span>
          <span id="last-updated" class="mono">waiting...</span>
        </div>
      </div>
      <div class="panel headline-metrics">
        <div class="headline-metric">
          <div class="label">Uptime</div>
          <div class="value" id="uptime">--</div>
          <div class="subvalue" id="load-avg">load --</div>
        </div>
        <div class="headline-metric">
          <div class="label">GPU VRAM</div>
          <div class="value" id="gpu-vram">--</div>
          <div class="subvalue" id="gpu-heat">GPU idle</div>
        </div>
        <div class="headline-metric">
          <div class="label">RAM</div>
          <div class="value" id="ram">--</div>
          <div class="subvalue" id="swap">swap --</div>
        </div>
        <div class="headline-metric">
          <div class="label">CPU</div>
          <div class="value" id="cpu">--</div>
          <div class="subvalue" id="cpu-heat">temp --</div>
        </div>
      </div>
    </section>

    <section class="metric-grid">
      <div class="panel metric-card">
        <div class="label">Disk</div>
        <div class="value" id="disk">--</div>
        <div class="subvalue" id="disk-detail">root volume</div>
      </div>
      <div class="panel metric-card">
        <div class="label">Network</div>
        <div class="value" id="network-rx">--</div>
        <div class="subvalue" id="network-tx">--</div>
      </div>
      <div class="panel metric-card">
        <div class="label">GPU Power</div>
        <div class="value" id="gpu-power">--</div>
        <div class="subvalue" id="gpu-util">util --</div>
      </div>
    </section>

    <section class="grid-two">
      <div class="panel">
        <div class="label" style="margin-bottom: 12px;">Service Health</div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Service</th>
                <th>Port</th>
                <th>Status</th>
                <th>PID</th>
                <th>RSS</th>
              </tr>
            </thead>
            <tbody id="services-body"></tbody>
          </table>
        </div>
      </div>

      <div class="panel">
        <div class="label" style="margin-bottom: 12px;">Top Processes</div>
        <div id="process-list" class="list"></div>
      </div>
    </section>

    <section class="charts-section" style="margin-top:16px;">
      <div class="charts-header panel" style="margin-bottom:16px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;">
        <div>
          <div class="label">Historical Trends</div>
          <div class="subvalue" id="history-status">Collecting data...</div>
        </div>
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
          <button class="range-btn active" data-range="2h">2h</button>
          <button class="range-btn" data-range="6h">6h</button>
          <button class="range-btn" data-range="12h">12h</button>
          <button class="range-btn" data-range="1d">1d</button>
          <button class="range-btn" data-range="7d">7d</button>
          <button class="range-btn" data-range="30d">30d</button>
          <button class="range-btn" data-range="all">All</button>
          <div class="stamp" style="margin:0;margin-left:8px;">
            <span id="history-count">0</span> samples
          </div>
        </div>
      </div>
      <div class="chart-grid">
        <div class="panel chart-panel">
          <div class="chart-title">CPU & RAM Usage</div>
          <canvas id="chart-cpu-ram"></canvas>
          <div class="chart-legend">
            <span class="legend-item"><span class="legend-dot" style="background:#38bdf8;"></span>CPU %</span>
            <span class="legend-item"><span class="legend-dot" style="background:#4ade80;"></span>RAM %</span>
          </div>
        </div>
        <div class="panel chart-panel">
          <div class="chart-title">GPU VRAM & Temperature</div>
          <canvas id="chart-gpu"></canvas>
          <div class="chart-legend">
            <span class="legend-item"><span class="legend-dot" style="background:#a78bfa;"></span>VRAM %</span>
            <span class="legend-item"><span class="legend-dot" style="background:#fb923c;"></span>Temp &deg;C</span>
          </div>
        </div>
        <div class="panel chart-panel">
          <div class="chart-title">Network I/O</div>
          <canvas id="chart-network"></canvas>
          <div class="chart-legend">
            <span class="legend-item"><span class="legend-dot" style="background:#38bdf8;"></span>RX</span>
            <span class="legend-item"><span class="legend-dot" style="background:#fb7185;"></span>TX</span>
          </div>
        </div>
        <div class="panel chart-panel">
          <div class="chart-title">GPU Power & Load Average</div>
          <canvas id="chart-power"></canvas>
          <div class="chart-legend">
            <span class="legend-item"><span class="legend-dot" style="background:#f59e0b;"></span>Power W</span>
            <span class="legend-item"><span class="legend-dot" style="background:#4ade80;"></span>Load</span>
          </div>
        </div>
      </div>
    </section>
  </div>

  <script>
    function formatBytes(bytes) {
      if (bytes === null || bytes === undefined) return "--";
      const units = ["B", "KB", "MB", "GB", "TB"];
      let value = bytes;
      let unit = 0;
      while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
      }
      return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
    }

    function formatMbPair(used, total) {
      if (used === null || total === null || used === undefined || total === undefined) return "--";
      return `${(used / 1024).toFixed(1)} / ${(total / 1024).toFixed(1)} GB`;
    }

    function formatPercent(value) {
      if (value === null || value === undefined) return "--";
      return `${Number(value).toFixed(0)}%`;
    }

    function renderServices(services) {
      const body = document.getElementById("services-body");
      body.innerHTML = services.map((service) => {
        const ok = service.online && service.active_state === "active";
        const statusClass = ok ? "status-chip ok" : "status-chip";
        const statusText = ok ? "online" : `${service.active_state}/${service.sub_state}`;
        return `
          <tr>
            <td><a href="${service.url || `http://REDACTED_SERVER_IP:${service.port}/`}" target="_blank" style="color:var(--text);text-decoration:none;border-bottom:1px dashed rgba(143,163,191,0.3);" onmouseover="this.style.color='var(--blue)'" onmouseout="this.style.color='var(--text)'">${service.name}</a></td>
            <td class="mono">:${service.port}</td>
            <td><span class="${statusClass}">${statusText}</span></td>
            <td class="mono">${service.pid || "-"}</td>
            <td class="mono">${service.memory_rss ? formatBytes(service.memory_rss) : "-"}</td>
          </tr>
        `;
      }).join("");
    }

    function renderProcesses(processes) {
      const list = document.getElementById("process-list");
      list.innerHTML = processes.map((proc) => `
        <div class="process-row">
          <div>
            <div class="process-name">${proc.name || proc.cmdline || "process"}</div>
            <div class="muted mono">${proc.cmdline || `pid ${proc.pid}`}</div>
          </div>
          <div class="mono">${formatBytes(proc.memory_rss)}</div>
        </div>
      `).join("");
    }

    async function refresh() {
      const res = await fetch("/api/stats", { cache: "no-store" });
      const data = await res.json();
      const gpu = (data.gpus || [])[0] || null;

      document.getElementById("last-updated").textContent = new Date().toLocaleTimeString();
      document.getElementById("uptime").textContent = data.uptime_text;
      document.getElementById("load-avg").textContent = `load ${data.load_avg.join(" / ")}`;
      document.getElementById("ram").textContent = `${formatBytes(data.memory.used)} / ${formatBytes(data.memory.total)}`;
      document.getElementById("swap").textContent = `swap ${formatBytes(data.swap.used)} / ${formatBytes(data.swap.total)}`;
      document.getElementById("cpu").textContent = formatPercent(data.cpu.usage_percent);
      document.getElementById("cpu-heat").textContent = data.cpu.temp_c === null ? "temp unavailable" : `temp ${data.cpu.temp_c.toFixed(1)} C`;
      document.getElementById("disk").textContent = `${formatPercent(data.disk.percent)}`;
      document.getElementById("disk-detail").textContent = `${formatBytes(data.disk.used)} used of ${formatBytes(data.disk.total)}`;
      document.getElementById("network-rx").textContent = `RX ${formatBytes(data.network.bytes_recv)}`;
      document.getElementById("network-tx").textContent = `TX ${formatBytes(data.network.bytes_sent)}`;

      if (gpu) {
        document.getElementById("gpu-vram").textContent = formatMbPair(gpu.memory_used_mb, gpu.memory_total_mb);
        document.getElementById("gpu-heat").textContent = `${gpu.temp_c.toFixed(0)} C, fan ${gpu.fan_percent === null ? "n/a" : `${gpu.fan_percent.toFixed(0)}%`}`;
        document.getElementById("gpu-power").textContent = `${gpu.power_draw_w.toFixed(0)} / ${gpu.power_limit_w.toFixed(0)} W`;
        document.getElementById("gpu-util").textContent = `util ${gpu.util_percent.toFixed(0)}%`;
      } else {
        document.getElementById("gpu-vram").textContent = "no GPU";
        document.getElementById("gpu-heat").textContent = "not detected";
        document.getElementById("gpu-power").textContent = "--";
        document.getElementById("gpu-util").textContent = "--";
      }

      renderServices(data.services || []);
      renderProcesses(data.top_processes || []);
    }

    // ── Chart rendering engine ──
    const chartInstances = {};
    function drawChart(canvasId, datasets, opts = {}) {
      const canvas = document.getElementById(canvasId);
      if (!canvas) return;
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      const ctx = canvas.getContext("2d");
      ctx.scale(dpr, dpr);
      const W = rect.width, H = rect.height;
      const pad = { top: 8, right: 12, bottom: 24, left: 44 };
      const plotW = W - pad.left - pad.right;
      const plotH = H - pad.top - pad.bottom;

      ctx.clearRect(0, 0, W, H);

      // compute Y range
      let yMin = opts.yMin !== undefined ? opts.yMin : 0;
      let yMax = opts.yMax !== undefined ? opts.yMax : 100;
      if (opts.autoMax) {
        let dMax = 0;
        for (const ds of datasets) {
          for (const v of ds.data) { if (v > dMax) dMax = v; }
        }
        yMax = Math.max(dMax * 1.15, 1);
      }

      const len = datasets[0].data.length;
      if (len < 2) return;
      const xStep = plotW / (len - 1);

      // grid lines
      ctx.strokeStyle = "rgba(143,163,191,0.10)";
      ctx.lineWidth = 1;
      const gridLines = 4;
      for (let i = 0; i <= gridLines; i++) {
        const y = pad.top + (plotH / gridLines) * i;
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(pad.left + plotW, y);
        ctx.stroke();
        // label
        const val = yMax - (yMax - yMin) * (i / gridLines);
        ctx.fillStyle = "rgba(143,163,191,0.5)";
        ctx.font = "11px ui-monospace, monospace";
        ctx.textAlign = "right";
        ctx.fillText(opts.formatY ? opts.formatY(val) : val.toFixed(0), pad.left - 6, y + 4);
      }

      // time labels
      if (datasets[0].timestamps && datasets[0].timestamps.length > 0) {
        ctx.fillStyle = "rgba(143,163,191,0.4)";
        ctx.font = "10px ui-monospace, monospace";
        ctx.textAlign = "center";
        const step = Math.max(1, Math.floor(len / 5));
        for (let i = 0; i < len; i += step) {
          const ts = datasets[0].timestamps[i];
          const x = pad.left + i * xStep;
          const d = new Date(ts * 1000);
          ctx.fillText(d.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}), x, H - 4);
        }
      }

      // draw each dataset
      for (const ds of datasets) {
        const data = ds.data;
        ctx.beginPath();
        for (let i = 0; i < data.length; i++) {
          const x = pad.left + i * xStep;
          const ratio = (data[i] - yMin) / (yMax - yMin);
          const y = pad.top + plotH - ratio * plotH;
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.strokeStyle = ds.color;
        ctx.lineWidth = 2;
        ctx.lineJoin = "round";
        ctx.stroke();

        // fill
        if (ds.fill) {
          ctx.lineTo(pad.left + (data.length - 1) * xStep, pad.top + plotH);
          ctx.lineTo(pad.left, pad.top + plotH);
          ctx.closePath();
          const grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + plotH);
          grad.addColorStop(0, ds.color.replace(")", ",0.25)").replace("rgb", "rgba"));
          grad.addColorStop(1, ds.color.replace(")", ",0.02)").replace("rgb", "rgba"));
          ctx.fillStyle = grad;
          ctx.fill();
        }
      }
    }

    let historyData = [];
    let currentRange = "2h";

    // Range button handlers
    document.querySelectorAll(".range-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".range-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        currentRange = btn.dataset.range;
        refreshHistory();
      });
    });

    async function refreshHistory() {
      try {
        const res = await fetch(`/api/history?range=${currentRange}`, { cache: "no-store" });
        historyData = await res.json();
        document.getElementById("history-count").textContent = historyData.length;
        if (historyData.length > 1) {
          const oldest = new Date(historyData[0].ts * 1000);
          const newest = new Date(historyData[historyData.length-1].ts * 1000);
          const diffMs = newest - oldest;
          const diffMin = Math.round(diffMs / 60000);
          let rangeText;
          if (diffMin < 60) rangeText = `${diffMin} min`;
          else if (diffMin < 1440) rangeText = `${(diffMin/60).toFixed(1)} hours`;
          else rangeText = `${(diffMin/1440).toFixed(1)} days`;
          document.getElementById("history-status").textContent = `Showing ${rangeText} of data`;
        } else {
          document.getElementById("history-status").textContent = "Collecting data...";
        }
        if (historyData.length < 2) return;
        const ts = historyData.map(h => h.ts);
        drawChart("chart-cpu-ram", [
          { data: historyData.map(h => h.cpu), color: "rgb(56,189,248)", fill: true, timestamps: ts },
          { data: historyData.map(h => h.ram), color: "rgb(74,222,128)", fill: false, timestamps: ts },
        ], { yMin: 0, yMax: 100, formatY: v => v + "%" });
        drawChart("chart-gpu", [
          { data: historyData.map(h => h.gpu_vram), color: "rgb(167,139,250)", fill: true, timestamps: ts },
          { data: historyData.map(h => h.gpu_temp), color: "rgb(251,146,60)", fill: false, timestamps: ts },
        ], { yMin: 0, yMax: 100, formatY: v => v.toFixed(0) });
        drawChart("chart-network", [
          { data: historyData.map(h => h.net_rx), color: "rgb(56,189,248)", fill: true, timestamps: ts },
          { data: historyData.map(h => h.net_tx), color: "rgb(251,113,133)", fill: true, timestamps: ts },
        ], { autoMax: true, formatY: v => formatBytes(v) + "/s" });
        drawChart("chart-power", [
          { data: historyData.map(h => h.gpu_power), color: "rgb(245,158,11)", fill: true, timestamps: ts },
          { data: historyData.map(h => h.load), color: "rgb(74,222,128)", fill: false, timestamps: ts },
        ], { autoMax: true, formatY: v => v.toFixed(0) });
      } catch (e) { console.error("History fetch failed:", e); }
    }

    // Refresh history on each stats refresh cycle
    const _origRefresh = refresh;
    refresh = async function() {
      await _origRefresh();
      await refreshHistory();
    };
    window.addEventListener("resize", () => { if (historyData.length > 1) refreshHistory(); });

    refresh().catch(console.error);
    setInterval(() => refresh().catch(console.error), 2500);
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8007)
    args = parser.parse_args()
    start_history_collector()
    app.run(host="0.0.0.0", port=args.port, debug=False)
