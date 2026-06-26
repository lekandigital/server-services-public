#!/usr/bin/env python3
"""
portal.py — Server Portal / Service Directory

Single-page Flask app showing all services running on this machine.
Reads from services.json, provides live status checks via TCP probe.
Supports toggling services on/off via systemctl.

Usage:
    python3 portal.py [--port 8001]
"""

import argparse
import json
import os
import socket
import subprocess
import sys

from flask import Flask, Response, jsonify, request

app = Flask(__name__)

DATA_DIR = "."
SERVICES_FILE = "services.json"
HOST_IP = "REDACTED_SERVER_IP"

# Map port -> systemd service name for services we can toggle
SERVICE_MAP = {
    8001: "server-portal",
    8002: "ollama",
    8003: "xb-dashboard",
    8004: "cast-manager",
    8005: "faster-whisper",
    8006: "paddleocr",
    8007: "system-stats",
    8008: "image-studio",
    8009: "xbot-lan-dashboard",
    8011: "proton-vpn-portal",
    8012: "pdf-portal",
    9091: "transmission-daemon",
}

SUDO_PROMPT_MARKERS = (
    "a password is required",
    "a terminal is required",
    "no tty present",
    "password is required",
)
SUDO_AUTH_FAILURE_MARKERS = (
    "sorry, try again",
    "incorrect password",
    "incorrect password attempt",
)

DEFAULT_SERVICES = [
    {
        "name": "Server Portal",
        "description": "This page \u2014 directory of all services",
        "id": "server-portal",
        "port": 8001,
        "icon": "\U0001f3e0",
        "color": "#8b949e"
    },
    {
        "name": "Ollama",
        "description": "Local LLM inference server",
        "port": 8002,
        "icon": "\U0001f999",
        "color": "#a371f7"
    },
    {
        "name": "Twitter Bot Dashboard",
        "description": "Monitor and control the follow/unfollow bot",
        "port": 8003,
        "icon": "\U0001f916",
        "color": "#58a6ff"
    },
    {
        "name": "Cast Manager",
        "description": "Cast media, manage videos, and browse server files",
        "id": "cast-manager",
        "port": 8004,
        "icon": "\U0001f3ac",
        "color": "#f0883e"
    },
    {
        "name": "Whisper Transcriber",
        "description": "Audio & video transcription \u00b7 large-v3",
        "port": 8005,
        "icon": "\U0001f399\ufe0f",
        "color": "#7c3aed"
    },
    {
        "name": "OCR Engine",
        "description": "Image & PDF text extraction \u00b7 109 languages",
        "port": 8006,
        "icon": "\U0001f50d",
        "color": "#7c3aed"
    },
    {
        "name": "System Stats",
        "description": "Live CPU, GPU, RAM, disk, and service health",
        "port": 8007,
        "icon": "\U0001f4ca",
        "color": "#2ea043"
    },
    {
        "name": "ML Image Studio",
        "description": "GPU image editing \u00b7 bg removal, upscale, style transfer",
        "port": 8008,
        "icon": "\U0001f5bc\ufe0f",
        "color": "#7c3aed"
    },
    {
        "name": "X-Bot Portal",
        "description": "LAN dashboard for the current x-bot checkout",
        "id": "x-bot-portal",
        "port": 8009,
        "url": f"http://{HOST_IP}:8009/",
        "icon": "\U0001f4e1",
        "color": "#38bdf8"
    },
    {
        "name": "VLC Stream",
        "description": "Active media stream listener",
        "id": "vlc-stream",
        "port": 8010,
        "badge": ":8010",
        "clickable": False,
        "toggleable": False,
        "icon": "\U0001f3a5",
        "color": "#f0883e"
    },
    {
        "name": "Proton VPN",
        "description": "VPN tunnel status and connect/disconnect controls",
        "id": "proton-vpn",
        "port": 8011,
        "url": f"http://{HOST_IP}:8011/",
        "kind": "vpn",
        "icon": "\U0001f510",
        "color": "#38bdf8"
    },
    {
        "name": "PDF Portal",
        "description": "Markdown to polished PDF documents",
        "id": "pdf-portal",
        "port": 8012,
        "url": f"http://{HOST_IP}:8012/",
        "icon": "\U0001f4c4",
        "color": "#f59e0b"
    }
]


def _services_path():
    return os.path.join(DATA_DIR, SERVICES_FILE)


def service_key(service):
    return str(service.get("id") or int(service.get("port", 0)))


def normalize_services(services):
    return sorted(services, key=lambda service: (int(service.get("port", 0)), service.get("name", "")))


def merge_with_defaults(services):
    merged = []
    seen_keys = set()
    default_by_key = {service_key(service): service for service in DEFAULT_SERVICES}

    for service in services:
        port = int(service.get("port", 0))
        key = service_key(service)
        base = default_by_key.get(key, {})
        merged_service = {
            "name": service.get("name") or base.get("name") or f"Service {port}",
            "description": service.get("description", base.get("description", "")),
            "port": port,
            "icon": service.get("icon") or base.get("icon", "📦"),
            "color": service.get("color") or base.get("color", "#8b949e"),
        }
        for optional_key in ("id", "url", "status_port", "badge", "toggleable", "kind", "clickable"):
            if optional_key in service:
                merged_service[optional_key] = service[optional_key]
            elif optional_key in base:
                merged_service[optional_key] = base[optional_key]
        merged.append(merged_service)
        seen_keys.add(key)

    for default_service in DEFAULT_SERVICES:
        if service_key(default_service) not in seen_keys:
            merged.append(dict(default_service))

    return normalize_services(merged)


def load_services():
    path = _services_path()
    if not os.path.exists(path):
        save_services(DEFAULT_SERVICES)
        return normalize_services(DEFAULT_SERVICES)
    try:
        with open(path, "r", encoding="utf-8") as f:
            services = merge_with_defaults(json.load(f))
        save_services(services)
        return services
    except Exception:
        return normalize_services(DEFAULT_SERVICES)


def save_services(services):
    path = _services_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merge_with_defaults(services), f, indent=2, ensure_ascii=False)


def check_port(port, timeout=1):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        result = s.connect_ex(("127.0.0.1", int(port)))
        s.close()
        return result == 0
    except Exception:
        return False


def _needs_sudo_password(message):
    text = (message or "").lower()
    return any(marker in text for marker in SUDO_PROMPT_MARKERS)


def _sudo_auth_failed(message):
    text = (message or "").lower()
    return any(marker in text for marker in SUDO_AUTH_FAILURE_MARKERS)


def _systemctl(action, service_name, sudo_password=None):
    """Run systemctl action on a service. Returns (success, message, needs_sudo)."""
    if sudo_password is None:
        args = ["sudo", "-n", "systemctl", action, service_name]
        stdin = None
    else:
        args = ["sudo", "-k", "-S", "-p", "", "systemctl", action, service_name]
        stdin = f"{sudo_password}\n"
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            input=stdin,
            text=True,
            timeout=15,
        )
        if sudo_password is not None:
            subprocess.run(["sudo", "-k"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return True, f"{action} {service_name} succeeded", False
        message = result.stderr.strip() or result.stdout.strip() or f"{action} failed (exit {result.returncode})"
        if sudo_password is None and _needs_sudo_password(message):
            return False, "Sudo password required.", True
        if sudo_password is not None and _sudo_auth_failed(message):
            return False, "Incorrect sudo password.", True
        return False, message, False
    except Exception as e:
        return False, str(e), False


def _docker_compose(action, path):
    """Run docker compose start/stop in a directory. Returns (success, message)."""
    try:
        result = subprocess.run(
            ["docker", "compose", action],
            cwd=path,
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return True, f"{action} docker compose succeeded"
        return False, result.stderr.strip() or f"{action} failed (exit {result.returncode})"
    except Exception as e:
        return False, str(e)


# ============================================================
# API ROUTES
# ============================================================

@app.route("/api/services")
def api_services_get():
    return jsonify(load_services())


@app.route("/api/services", methods=["POST"])
def api_services_post():
    data = request.get_json()
    if not data or not data.get("name") or not data.get("port"):
        return jsonify({"error": "name and port are required"}), 400
    services = load_services()
    services.append({
        "id": data.get("id"),
        "name": data["name"],
        "description": data.get("description", ""),
        "port": int(data["port"]),
        "url": data.get("url"),
        "status_port": int(data["status_port"]) if data.get("status_port") else None,
        "badge": data.get("badge"),
        "toggleable": data.get("toggleable", True),
        "clickable": data.get("clickable", True),
        "kind": data.get("kind"),
        "icon": data.get("icon", "\U0001f4e6"),
        "color": data.get("color", "#8b949e"),
    })
    save_services(services)
    return jsonify({"ok": True})


@app.route("/api/services", methods=["DELETE"])
def api_services_delete():
    data = request.get_json()
    if not data or ("id" not in data and "port" not in data):
        return jsonify({"error": "id or port is required"}), 400
    services = load_services()
    target_id = data.get("id")
    target_port = int(data["port"]) if "port" in data else None
    if target_id:
        services = [s for s in services if service_key(s) != str(target_id)]
    else:
        services = [s for s in services if s["port"] != target_port]
    save_services(services)
    return jsonify({"ok": True})


@app.route("/api/check/<int:port>")
def api_check(port):
    online = check_port(port)
    return jsonify({"port": port, "online": online})


@app.route("/api/toggle/<int:port>", methods=["POST"])
def api_toggle(port):
    svc = SERVICE_MAP.get(port)
    if not svc:
        return jsonify({"error": "no systemd service mapped for this port"}), 400
    data = request.get_json(silent=True) or {}
    requested_action = data.get("action")
    sudo_password = data.get("sudo_password")
    if sudo_password is not None:
        sudo_password = str(sudo_password)
    if requested_action in {"start", "stop"}:
        action = requested_action
    else:
        online = check_port(port)
        action = "stop" if online else "start"

    if svc == "ollama":
        # Ollama is managed via Docker Compose
        ok, msg = _docker_compose(action, "/home/REDACTED_USER/ollama-gui")
        needs_sudo = False
    else:
        ok, msg, needs_sudo = _systemctl(action, svc, sudo_password=sudo_password)

    return jsonify({"ok": ok, "action": action, "message": msg, "needs_sudo": needs_sudo, "port": port})


@app.route("/api/service-map")
def api_service_map():
    """Return which ports have toggleable systemd services."""
    return jsonify(SERVICE_MAP)


def run_vpn_status():
    uid = os.getuid()
    home = os.path.expanduser("~")
    user = os.path.basename(home) or "o"
    runtime_dir = f"/run/user/{uid}"
    env = os.environ.copy()
    env.setdefault("HOME", home)
    env.setdefault("USER", user)
    env.setdefault("LOGNAME", user)
    env.setdefault("XDG_RUNTIME_DIR", runtime_dir)
    env.setdefault("XDG_CONFIG_HOME", os.path.join(env["HOME"], ".config"))
    env.setdefault("XDG_CACHE_HOME", os.path.join(env["HOME"], ".cache"))
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus")
    try:
        result = subprocess.run(
            ["protonvpn", "status"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=env,
        )
        output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    except Exception as e:
        output = str(e)
        result = None

    details = {}
    status = "Unknown"
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower().replace(" ", "_")
        details[key] = value.strip()
        if key == "status":
            status = value.strip()
    return {
        "ok": result is not None and result.returncode == 0,
        "connected": status.lower() == "connected",
        "status": status,
        "details": details,
        "raw": output,
    }


@app.route("/api/vpn/status")
def api_vpn_status():
    return jsonify(run_vpn_status())


# ============================================================
# PAGE
# ============================================================

@app.route("/")
def index():
    return Response(HTML_PAGE, mimetype="text/html")


# ============================================================
# HTML
# ============================================================

HTML_PAGE = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{HOST_IP} — Local Services</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🏠</text></svg>">
<style>
:root{{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#c9d1d9;--muted:#8b949e;--green:#3fb950;--red:#f85149;--blue:#58a6ff}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:40px 16px}}
h1{{font-size:28px;font-weight:700;font-family:'SF Mono',SFMono-Regular,Consolas,monospace;margin-bottom:4px}}
.subtitle{{color:var(--muted);font-size:14px;margin-bottom:32px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;width:100%;max-width:700px}}
@media(max-width:600px){{.grid{{grid-template-columns:1fr}}}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;cursor:pointer;transition:border-color .2s,box-shadow .2s,opacity .2s;position:relative;text-decoration:none;color:var(--text);display:block;--accent:#58a6ff}}
.card:hover{{border-color:var(--accent);box-shadow:0 0 12px rgba(88,166,255,.18);text-decoration:none}}
.card.disabled{{cursor:default;opacity:.82}}
.card.disabled:hover{{border-color:var(--border);box-shadow:none}}
.card-link{{display:block;text-decoration:none;color:inherit}}
.card-static{{cursor:default}}
.card .icon{{font-size:36px;margin-bottom:8px}}
.card .name{{font-size:16px;font-weight:600;margin-bottom:4px}}
.card .desc{{color:var(--muted);font-size:13px;margin-bottom:10px}}
.card .footer{{display:flex;align-items:center;justify-content:space-between}}
.port-badge{{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:2px 8px;font-size:11px;font-family:'SF Mono',SFMono-Regular,Consolas,monospace;color:var(--muted)}}
.status{{display:flex;align-items:center;gap:4px;font-size:11px}}
.dot{{width:7px;height:7px;border-radius:50%}}
.dot.on{{background:var(--green);box-shadow:0 0 6px var(--green)}}
.dot.off{{background:var(--red)}}
.dot.pending{{background:#d29922}}
.status-text{{color:var(--muted)}}
.remove-btn{{position:absolute;top:8px;right:10px;background:none;border:none;color:var(--muted);font-size:16px;cursor:pointer;opacity:0;transition:opacity .2s;padding:4px}}
.card:hover .remove-btn{{opacity:.6}}
.remove-btn:hover{{opacity:1;color:var(--red)}}

/* Power radios */
.toggle-row{{display:flex;align-items:center;gap:10px;margin-top:10px;padding-top:10px;border-top:1px solid var(--border)}}
.toggle-label{{font-size:11px;color:var(--muted);flex:1}}
.power-radio{{display:grid;grid-template-columns:1fr 1fr;gap:4px;flex-shrink:0;border:1px solid var(--border);background:var(--bg);border-radius:999px;padding:3px}}
.power-radio label{{position:relative;display:block}}
.power-radio input{{position:absolute;opacity:0;pointer-events:none}}
.power-radio span{{display:flex;align-items:center;gap:5px;min-width:46px;justify-content:center;border-radius:999px;padding:5px 8px;color:var(--muted);font-size:11px;font-weight:700;line-height:1;cursor:pointer;transition:background .2s,color .2s,opacity .2s}}
.power-radio span::before{{content:'';width:7px;height:7px;border-radius:50%;border:1px solid currentColor}}
.power-radio .on input:checked + span{{background:rgba(63,185,80,.18);color:var(--green)}}
.power-radio .off input:checked + span{{background:rgba(248,81,73,.18);color:var(--red)}}
.power-radio input:checked + span::before{{background:currentColor}}
.power-radio input:disabled + span{{cursor:not-allowed;color:var(--muted);background:transparent;opacity:.5}}
.power-radio input:disabled:checked + span{{background:#30363d;color:#c9d1d9;opacity:.72}}
.power-radio.loading span{{opacity:.45}}

.add-btn{{position:fixed;bottom:24px;right:24px;width:48px;height:48px;border-radius:50%;background:var(--blue);color:#0d1117;border:none;font-size:24px;cursor:pointer;box-shadow:0 2px 10px rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;transition:transform .2s}}
.add-btn:hover{{transform:scale(1.1)}}
.modal-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:200;align-items:center;justify-content:center}}
.modal-overlay.show{{display:flex}}
.modal{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:24px;width:90%;max-width:400px}}
.modal h2{{font-size:16px;margin-bottom:16px}}
.modal label{{display:block;font-size:12px;color:var(--muted);margin-bottom:4px;margin-top:12px}}
.modal input{{background:var(--bg);color:var(--text);border:1px solid var(--border);padding:8px 12px;border-radius:6px;font-size:13px;width:100%}}
.modal input:focus{{outline:none;border-color:var(--blue)}}
.modal .actions{{display:flex;gap:8px;margin-top:20px;justify-content:flex-end}}
.modal .btn{{padding:8px 16px;border-radius:6px;border:1px solid var(--border);cursor:pointer;font-size:13px}}
.modal .btn-cancel{{background:var(--border);color:var(--text)}}
.modal .btn-save{{background:var(--blue);color:#0d1117;border-color:var(--blue)}}

/* Toast notification */
.toast{{position:fixed;bottom:80px;left:50%;transform:translateX(-50%) translateY(20px);background:var(--card);border:1px solid var(--border);color:var(--text);padding:10px 20px;border-radius:8px;font-size:13px;opacity:0;transition:all .3s;pointer-events:none;z-index:300;white-space:nowrap}}
.toast.show{{opacity:1;transform:translateX(-50%) translateY(0)}}
.toast.success{{border-color:var(--green)}}
.toast.error{{border-color:var(--red)}}
</style></head><body>
<h1>{HOST_IP}</h1>
<div class="subtitle">Local Services</div>
<div class="grid" id="grid"></div>
<button class="add-btn" onclick="openModal()" title="Add service">+</button>
<div id="toast" class="toast"></div>

<div class="modal-overlay" id="modal">
  <div class="modal">
    <h2>Add Service</h2>
    <label>Name *</label><input id="m-name" placeholder="My Service">
    <label>Description</label><input id="m-desc" placeholder="What it does">
    <label>Port *</label><input id="m-port" type="number" placeholder="8080">
    <label>Icon (emoji)</label><input id="m-icon" placeholder="📦" maxlength="4">
    <label>Color (hex)</label><input id="m-color" placeholder="#58a6ff" value="#58a6ff">
    <div class="actions">
      <button class="btn btn-cancel" onclick="closeModal()">Cancel</button>
      <button class="btn btn-save" onclick="addService()">Add</button>
    </div>
  </div>
</div>

<div class="modal-overlay" id="sudo-modal">
  <div class="modal">
    <h2>Sudo Required</h2>
    <div class="subtitle" id="sudo-service">Service control needs sudo.</div>
    <label>Password</label><input id="sudo-password" type="password" autocomplete="current-password" onkeydown="if(event.key==='Enter')submitSudoPassword()">
    <div class="subtitle" id="sudo-error" style="margin-top:10px;color:var(--red)"></div>
    <div class="actions">
      <button class="btn btn-cancel" onclick="closeSudoModal()">Cancel</button>
      <button class="btn btn-save" id="sudo-submit" onclick="submitSudoPassword()">Continue</button>
    </div>
  </div>
</div>

<script>
const HOST='{HOST_IP}';
let services=[];
let serviceMap={{}};
let statusCache={{}};
let pendingSudoAction=null;

function showToast(msg, type) {{
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + (type || '');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.className = 'toast', 2500);
}}

function esc(value) {{
  return String(value ?? '')
    .replaceAll('&','&amp;')
    .replaceAll('<','&lt;')
    .replaceAll('>','&gt;')
    .replaceAll('"','&quot;');
}}

function safeColor(value) {{
  return /^#[0-9a-fA-F]{{3,8}}$/.test(value || '') ? value : '#8b949e';
}}

function serviceUrl(s) {{
  return s.url || `http://${{HOST}}:${{s.port}}/`;
}}

function statusPort(s) {{
  return Number(s.status_port || s.port);
}}

function canToggle(s) {{
  return s.toggleable !== false && serviceMap[s.port] !== undefined;
}}

function renderPowerControl(s) {{
  const toggleable = canToggle(s);
  const port = Number(s.port);
  const portStatus = statusCache[statusPort(s)];
  const hasStatus = portStatus !== undefined;
  const isOn = portStatus === true;
  const disabled = toggleable ? '' : 'disabled';
  const disabledClass = toggleable ? '' : ' disabled';
  const onChecked = isOn ? 'checked' : '';
  const offChecked = hasStatus && !isOn ? 'checked' : '';
  const stateText = toggleable ? (isOn ? 'Running' : 'Stopped') : (isOn ? 'Listening' : 'Not managed');
  return `
      <div class="toggle-row${{disabledClass}}">
        <span class="toggle-label" id="toggle-label-${{port}}">${{stateText}}</span>
        <div class="power-radio${{disabledClass}}" id="toggle-${{port}}" role="radiogroup" aria-label="${{esc(s.name)}} power">
          <label class="on"><input type="radio" name="power-${{port}}" value="start" ${{onChecked}} ${{disabled}} onchange="toggleService(${{port}}, 'start', this)"><span>On</span></label>
          <label class="off"><input type="radio" name="power-${{port}}" value="stop" ${{offChecked}} ${{disabled}} onchange="toggleService(${{port}}, 'stop', this)"><span>Off</span></label>
        </div>
      </div>`;
}}

function render(){{
  const grid=document.getElementById('grid');
  grid.innerHTML=services.map((s,i)=>{{
    const clickable = s.clickable !== false;
    const accent = safeColor(s.color);
    const content = `
        <div class="icon">${{esc(s.icon || '📦')}}</div>
        <div class="name">${{esc(s.name)}}</div>
        <div class="desc">${{esc(s.description)}}</div>`;
    const cardBody = clickable
      ? `<a class="card-link" href="${{esc(serviceUrl(s))}}" target="_blank">${{content}}</a>`
      : `<div class="card-link card-static">${{content}}</div>`;
    return `
    <div class="card${{clickable ? '' : ' disabled'}}" id="card-${{s.port}}" style="--accent:${{accent}}">
      <button class="remove-btn" onclick="event.preventDefault();event.stopPropagation();removeService(${{s.port}})" title="Remove">&times;</button>
      ${{cardBody}}
      <div class="footer">
        <span class="port-badge">${{esc(s.badge || ':' + s.port)}}</span>
        <span class="status" id="status-${{s.port}}"><span class="dot pending"></span><span class="status-text">checking...</span></span>
      </div>
      ${{renderPowerControl(s)}}
    </div>`;
  }}).join('');
  checkAll();
}}

function updatePowerState(service, online) {{
  const port = Number(service.port);
  const tog=document.getElementById('toggle-'+port);
  if(!tog) return;
  const on=tog.querySelector('input[value="start"]');
  const off=tog.querySelector('input[value="stop"]');
  if(on) on.checked = online;
  if(off) off.checked = !online;
  const lbl=document.getElementById('toggle-label-'+port);
  if(lbl) {{
    lbl.textContent = canToggle(service) ? (online ? 'Running' : 'Stopped') : (online ? 'Listening' : 'Not managed');
  }}
}}

function updateVpnStatus(port) {{
  fetch('/api/vpn/status', {{cache:'no-store'}}).then(r=>r.json()).then(d=>{{
    const el=document.getElementById('status-'+port);
    if(!el) return;
    if(d.connected) {{
      el.innerHTML='<span class="dot on"></span><span class="status-text">VPN Connected</span>';
    }} else if(d.ok) {{
      el.innerHTML='<span class="dot off"></span><span class="status-text">'+esc(d.status || 'VPN Disconnected')+'</span>';
    }} else {{
      el.innerHTML='<span class="dot pending"></span><span class="status-text">VPN Unknown</span>';
    }}
  }}).catch(()=>{{}});
}}

function checkAll(){{
  services.forEach(s=>{{
    const checkPort = statusPort(s);
    fetch('/api/check/'+checkPort).then(r=>r.json()).then(d=>{{
      statusCache[checkPort] = d.online;
      const el=document.getElementById('status-'+s.port);
      if(el)el.innerHTML=d.online
        ?'<span class="dot on"></span><span class="status-text">Online</span>'
        :'<span class="dot off"></span><span class="status-text">Offline</span>';
      updatePowerState(s, d.online);
      if(s.kind === 'vpn') updateVpnStatus(s.port);
    }}).catch(()=>{{}});
  }});
}}

function openSudoModal(port, action, previous, message) {{
  const service = services.find(s => Number(s.port) === Number(port));
  pendingSudoAction = {{port, action, previous}};
  document.getElementById('sudo-service').textContent = `${{service ? service.name : 'Service'}} :${{port}}`;
  document.getElementById('sudo-error').textContent = message || '';
  const input = document.getElementById('sudo-password');
  input.value = '';
  document.getElementById('sudo-submit').disabled = false;
  document.getElementById('sudo-modal').classList.add('show');
  setTimeout(() => input.focus(), 0);
}}

function closeSudoModal() {{
  pendingSudoAction = null;
  document.getElementById('sudo-password').value = '';
  document.getElementById('sudo-error').textContent = '';
  document.getElementById('sudo-submit').disabled = false;
  document.getElementById('sudo-modal').classList.remove('show');
}}

function submitSudoPassword() {{
  if (!pendingSudoAction) return;
  const input = document.getElementById('sudo-password');
  const password = input.value;
  if (!password) return;
  const action = pendingSudoAction;
  input.value = '';
  document.getElementById('sudo-submit').disabled = true;
  closeSudoModal();
  toggleService(action.port, action.action, null, password, action.previous);
}}

function toggleService(port, action, radio, sudoPassword, previousOverride) {{
  const tog = document.getElementById('toggle-' + port);
  if (tog) tog.classList.add('loading');
  const service = services.find(s => Number(s.port) === Number(port));
  const previous = previousOverride !== undefined ? previousOverride : (service ? statusCache[statusPort(service)] === true : false);
  const payload = {{action}};
  if (sudoPassword) payload.sudo_password = sudoPassword;
  fetch('/api/toggle/' + port, {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(payload)
    }})
    .then(r => r.json())
    .then(d => {{
      if (tog) tog.classList.remove('loading');
      if (d.ok) {{
        showToast((d.action === 'start' ? 'Started' : 'Stopped') + ' service on :' + port, 'success');
        setTimeout(checkAll, 1500);
      }} else if (d.needs_sudo) {{
        if (service) updatePowerState(service, previous);
        openSudoModal(port, action, previous, sudoPassword ? d.message : '');
      }} else {{
        showToast('Error: ' + d.message, 'error');
        if (service) updatePowerState(service, previous);
      }}
    }})
    .catch(err => {{
      if (tog) tog.classList.remove('loading');
      showToast('Network error', 'error');
      if (service) updatePowerState(service, previous);
    }});
}}

function loadServices(){{
  Promise.all([
    fetch('/api/services').then(r=>r.json()),
    fetch('/api/service-map').then(r=>r.json())
  ]).then(([svc, map]) => {{
    services = svc;
    serviceMap = map;
    render();
  }});
}}

function openModal(){{document.getElementById('modal').classList.add('show')}}
function closeModal(){{document.getElementById('modal').classList.remove('show')}}

function addService(){{
  const name=document.getElementById('m-name').value.trim();
  const port=document.getElementById('m-port').value.trim();
  if(!name||!port)return;
  fetch('/api/services',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{
    name,description:document.getElementById('m-desc').value.trim(),
    port:parseInt(port),icon:document.getElementById('m-icon').value.trim()||'📦',
    color:document.getElementById('m-color').value.trim()||'#8b949e'
  }})}}).then(()=>{{closeModal();loadServices()}});
}}

function removeService(port){{
  if(!confirm('Remove this service?'))return;
  fetch('/api/services',{{method:'DELETE',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{port}})}}).then(()=>loadServices());
}}

loadServices();
setInterval(checkAll,30000);
</script>
</body></html>"""


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Server Portal — Service Directory")
    parser.add_argument("--port", type=int, default=8001, help="Port (default: 8001)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    parser.add_argument("--data-dir", default=".", help="Directory for services.json")
    args = parser.parse_args()

    global DATA_DIR
    DATA_DIR = os.path.abspath(args.data_dir)

    print(f"Server Portal starting on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
