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
    9091: "transmission-daemon",
}

DEFAULT_SERVICES = [
    {
        "name": "Server Portal",
        "description": "This page \u2014 directory of all services",
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
        "name": "Video Manager",
        "description": "Manage videos and cast to devices",
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
    }
]


def _services_path():
    return os.path.join(DATA_DIR, SERVICES_FILE)


def normalize_services(services):
    return sorted(services, key=lambda service: int(service.get("port", 0)))


def merge_with_defaults(services):
    merged = []
    seen_ports = set()
    default_by_port = {int(service["port"]): service for service in DEFAULT_SERVICES}

    for service in services:
        port = int(service.get("port", 0))
        base = default_by_port.get(port, {})
        merged_service = {
            "name": service.get("name") or base.get("name") or f"Service {port}",
            "description": service.get("description", base.get("description", "")),
            "port": port,
            "icon": service.get("icon") or base.get("icon", "📦"),
            "color": service.get("color") or base.get("color", "#8b949e"),
        }
        merged.append(merged_service)
        seen_ports.add(port)

    for default_service in DEFAULT_SERVICES:
        port = int(default_service["port"])
        if port not in seen_ports:
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


def _systemctl(action, service_name):
    """Run systemctl action on a service. Returns (success, message)."""
    try:
        result = subprocess.run(
            ["sudo", "-n", "systemctl", action, service_name],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            return True, f"{action} {service_name} succeeded"
        return False, result.stderr.strip() or f"{action} failed (exit {result.returncode})"
    except Exception as e:
        return False, str(e)


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
        "name": data["name"],
        "description": data.get("description", ""),
        "port": int(data["port"]),
        "icon": data.get("icon", "\U0001f4e6"),
        "color": data.get("color", "#8b949e"),
    })
    save_services(services)
    return jsonify({"ok": True})


@app.route("/api/services", methods=["DELETE"])
def api_services_delete():
    data = request.get_json()
    if not data or "port" not in data:
        return jsonify({"error": "port is required"}), 400
    services = load_services()
    port = int(data["port"])
    services = [s for s in services if s["port"] != port]
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
    online = check_port(port)
    action = "stop" if online else "start"
    
    if svc == "ollama":
        # Ollama is managed via Docker Compose
        ok, msg = _docker_compose(action, "/home/REDACTED_USER/ollama-gui")
    else:
        ok, msg = _systemctl(action, svc)
        
    return jsonify({"ok": ok, "action": action, "message": msg, "port": port})


@app.route("/api/service-map")
def api_service_map():
    """Return which ports have toggleable systemd services."""
    return jsonify(SERVICE_MAP)


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
.card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;cursor:pointer;transition:border-color .2s,box-shadow .2s;position:relative;text-decoration:none;color:var(--text);display:block}}
.card:hover{{text-decoration:none}}
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

/* Toggle switch */
.toggle-row{{display:flex;align-items:center;gap:8px;margin-top:10px;padding-top:10px;border-top:1px solid var(--border)}}
.toggle-label{{font-size:11px;color:var(--muted);flex:1}}
.toggle{{position:relative;width:40px;height:22px;flex-shrink:0}}
.toggle input{{opacity:0;width:0;height:0}}
.toggle .slider{{position:absolute;cursor:pointer;inset:0;background:var(--border);border-radius:22px;transition:background .3s}}
.toggle .slider::before{{content:'';position:absolute;width:16px;height:16px;left:3px;bottom:3px;background:var(--muted);border-radius:50%;transition:transform .3s,background .3s}}
.toggle input:checked + .slider{{background:var(--green)}}
.toggle input:checked + .slider::before{{transform:translateX(18px);background:#fff}}
.toggle.loading .slider{{opacity:.5}}
.toggle.loading .slider::before{{animation:pulse .8s infinite alternate}}
@keyframes pulse{{0%{{opacity:.4}}100%{{opacity:1}}}}

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

<script>
const HOST='{HOST_IP}';
let services=[];
let serviceMap={{}};
let statusCache={{}};

function showToast(msg, type) {{
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + (type || '');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.className = 'toast', 2500);
}}

function render(){{
  const grid=document.getElementById('grid');
  grid.innerHTML=services.map((s,i)=>{{
    const hasToggle = serviceMap[s.port] !== undefined;
    const isOn = statusCache[s.port];
    return `
    <div class="card" id="card-${{s.port}}"
       style="--accent:${{s.color}}"
       onmouseover="this.style.borderColor='${{s.color}}';this.style.boxShadow='0 0 12px ${{s.color}}30'"
       onmouseout="this.style.borderColor='var(--border)';this.style.boxShadow='none'">
      <button class="remove-btn" onclick="event.preventDefault();event.stopPropagation();removeService(${{s.port}})" title="Remove">&times;</button>
      <a href="http://${{HOST}}:${{s.port}}/" target="_blank" style="text-decoration:none;color:inherit">
        <div class="icon">${{s.icon}}</div>
        <div class="name">${{s.name}}</div>
        <div class="desc">${{s.description}}</div>
      </a>
      <div class="footer">
        <span class="port-badge">:${{s.port}}</span>
        <span class="status" id="status-${{s.port}}"><span class="dot pending"></span><span class="status-text">checking...</span></span>
      </div>
      ${{hasToggle ? `
      <div class="toggle-row">
        <span class="toggle-label">${{isOn ? 'Running' : 'Stopped'}}</span>
        <label class="toggle" id="toggle-${{s.port}}">
          <input type="checkbox" ${{isOn ? 'checked' : ''}} onchange="toggleService(${{s.port}}, this)">
          <span class="slider"></span>
        </label>
      </div>` : ''}}
    </div>`;
  }}).join('');
  checkAll();
}}

function checkAll(){{
  services.forEach(s=>{{
    fetch('/api/check/'+s.port).then(r=>r.json()).then(d=>{{
      statusCache[s.port] = d.online;
      const el=document.getElementById('status-'+s.port);
      if(el)el.innerHTML=d.online
        ?'<span class="dot on"></span><span class="status-text">Online</span>'
        :'<span class="dot off"></span><span class="status-text">Offline</span>';
      // Update toggle state without re-rendering
      const tog=document.getElementById('toggle-'+s.port);
      if(tog) {{
        const cb = tog.querySelector('input');
        if (cb && cb.checked !== d.online) cb.checked = d.online;
        const lbl = tog.closest('.toggle-row')?.querySelector('.toggle-label');
        if (lbl) lbl.textContent = d.online ? 'Running' : 'Stopped';
      }}
    }}).catch(()=>{{}});
  }});
}}

function toggleService(port, checkbox) {{
  const tog = document.getElementById('toggle-' + port);
  if (tog) tog.classList.add('loading');
  fetch('/api/toggle/' + port, {{method: 'POST'}})
    .then(r => r.json())
    .then(d => {{
      if (tog) tog.classList.remove('loading');
      if (d.ok) {{
        showToast((d.action === 'start' ? '▶ Started' : '⏹ Stopped') + ' service on :' + port, 'success');
        // Wait a moment for the service to start/stop, then re-check
        setTimeout(checkAll, 1500);
      }} else {{
        showToast('Error: ' + d.message, 'error');
        checkbox.checked = !checkbox.checked;
      }}
    }})
    .catch(err => {{
      if (tog) tog.classList.remove('loading');
      showToast('Network error', 'error');
      checkbox.checked = !checkbox.checked;
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
