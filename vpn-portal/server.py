#!/usr/bin/env python3
"""Proton VPN control page for the server."""

import argparse
import json
import os
import subprocess
from pathlib import Path

from flask import Flask, Response, jsonify, request


app = Flask(__name__)

HOST_IP = os.environ.get("HOST_IP", "REDACTED_SERVER_IP")
SERVERLIST_PATH = Path(
    os.environ.get(
        "PROTON_SERVERLIST_PATH",
        str(Path.home() / ".cache/Proton/VPN/serverlist.json"),
    )
)

FEATURES = {
    1: "Secure Core",
    2: "Tor",
    4: "P2P",
    8: "Streaming",
    16: "IPv6",
}

CONFIG_VALUES = {
    "kill-switch": {"off", "standard"},
    "netshield": {"off", "malware-only", "malware-ads-trackers"},
    "vpn-accelerator": {"off", "on"},
    "ipv6": {"off", "on"},
    "anonymous-crash-reports": {"off", "on"},
}


def proton_env():
    uid = os.getuid()
    home = Path.home()
    runtime_dir = f"/run/user/{uid}"
    env = os.environ.copy()
    env.setdefault("HOME", str(home))
    env.setdefault("USER", home.name)
    env.setdefault("LOGNAME", home.name)
    env.setdefault("XDG_RUNTIME_DIR", runtime_dir)
    env.setdefault("XDG_CONFIG_HOME", str(home / ".config"))
    env.setdefault("XDG_CACHE_HOME", str(home / ".cache"))
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus")
    return env


def run_proton(args, timeout=45):
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=proton_env(),
        )
        output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 124, "Command timed out."
    except Exception as exc:
        return 1, str(exc)


def parse_key_value_output(output):
    details = {}
    for line in output.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        details[key.strip().lower().replace(" ", "_")] = value.strip()
    return details


def vpn_status():
    code, output = run_proton(["protonvpn", "status"], timeout=20)
    details = parse_key_value_output(output)
    status = details.get("status", "Unknown")
    return {
        "ok": code == 0,
        "connected": status.lower() == "connected",
        "status": status,
        "details": details,
        "raw": output,
    }


def parse_config(output):
    settings = {}
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("-") or line.startswith("Setting ") or line.startswith("Current "):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0] in CONFIG_VALUES:
            settings[parts[0]] = parts[1].strip()
    return settings


def feature_names(bitmask):
    names = [name for bit, name in FEATURES.items() if bitmask & bit]
    return names or ["Standard"]


def load_servers():
    if not SERVERLIST_PATH.exists():
        run_proton(["protonvpn", "status"], timeout=30)
    try:
        payload = json.loads(SERVERLIST_PATH.read_text())
    except Exception:
        return []

    servers = []
    for item in payload.get("LogicalServers", []):
        servers.append(
            {
                "name": item.get("Name", ""),
                "entry_country": item.get("EntryCountry", ""),
                "exit_country": item.get("ExitCountry", ""),
                "city": item.get("City") or "",
                "region": item.get("Region") or "",
                "domain": item.get("Domain", ""),
                "tier": item.get("Tier", 0),
                "load": item.get("Load"),
                "score": item.get("Score"),
                "status": "online" if item.get("Status") == 1 else "maintenance",
                "features": feature_names(int(item.get("Features") or 0)),
            }
        )
    servers.sort(key=lambda row: (row["exit_country"], row["city"], row["name"]))
    return servers


@app.route("/")
def index():
    return Response(HTML_PAGE.replace("__HOST_IP__", HOST_IP), mimetype="text/html")


@app.route("/api/status")
def api_status():
    return jsonify(vpn_status())


@app.route("/api/servers")
def api_servers():
    return jsonify({"servers": load_servers()})


@app.route("/api/config")
def api_config():
    code, output = run_proton(["protonvpn", "config", "list"], timeout=20)
    return jsonify({"ok": code == 0, "settings": parse_config(output), "raw": output})


@app.route("/api/config", methods=["POST"])
def api_config_set():
    data = request.get_json() or {}
    setting = data.get("setting", "")
    value = data.get("value", "")
    if value not in CONFIG_VALUES.get(setting, set()):
        return jsonify({"ok": False, "message": "Unsupported setting value."}), 400
    code, output = run_proton(["protonvpn", "config", "set", setting, value], timeout=45)
    return jsonify({"ok": code == 0, "message": output}), 200 if code == 0 else 500


@app.route("/api/connect", methods=["POST"])
def api_connect():
    data = request.get_json() or {}
    mode = data.get("mode", "fastest")
    cmd = ["protonvpn", "connect"]

    if mode == "server" and data.get("server"):
        cmd.append(str(data["server"]).strip())
    elif mode == "country" and data.get("country"):
        cmd.extend(["--country", str(data["country"]).strip()])
    elif mode == "city" and data.get("city"):
        cmd.extend(["--city", str(data["city"]).strip()])
    elif mode == "random":
        cmd.append("--random")
    elif mode == "p2p":
        cmd.append("--p2p")
    elif mode == "securecore":
        cmd.append("--securecore")
    elif mode == "tor":
        cmd.append("--tor")

    code, output = run_proton(cmd, timeout=120)
    payload = vpn_status()
    payload.update({"ok": code == 0, "message": output})
    return jsonify(payload), 200 if code == 0 else 500


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    code, output = run_proton(["protonvpn", "disconnect"], timeout=60)
    payload = vpn_status()
    payload.update({"ok": code == 0, "message": output})
    return jsonify(payload), 200 if code == 0 else 500


HTML_PAGE = """<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Proton VPN</title>
<style>
:root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#c9d1d9;--muted:#8b949e;--green:#3fb950;--red:#f85149;--blue:#58a6ff;--gold:#d29922}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:28px 16px 44px}
.shell{width:min(1180px,100%);margin:0 auto}.top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:16px}
h1{margin:0 0 4px;font-size:30px}.subtitle,.muted{color:var(--muted);font-size:13px}a{color:var(--blue);text-decoration:none}
.grid{display:grid;grid-template-columns:1fr 1.4fr;gap:16px}.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:18px}
.status{display:flex;align-items:center;gap:12px;margin:12px 0}.dot{width:12px;height:12px;border-radius:50%;background:var(--gold)}.dot.connected{background:var(--green);box-shadow:0 0 10px var(--green)}.dot.disconnected{background:var(--red)}
.status-text{font-size:30px;font-weight:700}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
button,input,select{font:inherit}button{border:1px solid var(--border);border-radius:8px;background:#0d1117;color:var(--text);padding:9px 12px;cursor:pointer}
button.primary{background:var(--blue);border-color:var(--blue);color:#0d1117;font-weight:700}button.danger{border-color:rgba(248,81,73,.5);color:#ffb4ad}button:disabled{opacity:.55;cursor:wait}
input,select{background:#0d1117;border:1px solid var(--border);border-radius:8px;color:var(--text);padding:9px 10px;min-width:0}
.control-row{display:grid;grid-template-columns:1fr auto;gap:8px;margin-top:10px}.label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px}
.server-tools{display:grid;grid-template-columns:1fr auto auto;gap:8px;margin-bottom:10px}.servers{max-height:680px;overflow:auto;border:1px solid var(--border);border-radius:8px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:9px 10px;border-bottom:1px solid rgba(143,163,191,.14);vertical-align:top}th{position:sticky;top:0;background:#111827;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}
.pill{display:inline-flex;border:1px solid rgba(143,163,191,.25);border-radius:999px;padding:2px 7px;margin:0 4px 4px 0;color:var(--muted);font-size:11px}.load.good{color:var(--green)}.load.warn{color:var(--gold)}.load.bad{color:var(--red)}
pre{white-space:pre-wrap;word-break:break-word;color:var(--muted);font:12px ui-monospace,SFMono-Regular,Consolas,monospace;margin:12px 0 0}
@media(max-width:900px){.grid{grid-template-columns:1fr}.server-tools{grid-template-columns:1fr}.control-row{grid-template-columns:1fr}}
</style></head><body>
<div class="shell">
  <div class="top"><div><h1>Proton VPN</h1><div class="subtitle">Server list and tunnel controls for __HOST_IP__</div></div><a href="http://__HOST_IP__:8001/">Service directory</a></div>
  <div class="grid">
    <section class="card">
      <div class="label">Tunnel</div>
      <div class="status"><span id="dot" class="dot"></span><span id="status" class="status-text">Checking...</span></div>
      <div id="server" class="subtitle">Loading status</div>
      <div class="actions">
        <button class="primary" onclick="connect({mode:'fastest'})">Fastest</button>
        <button onclick="connect({mode:'country',country:'US'})">Fastest US</button>
        <button onclick="connect({mode:'random'})">Random</button>
        <button onclick="connect({mode:'p2p'})">P2P</button>
        <button onclick="connect({mode:'tor'})">Tor</button>
        <button onclick="connect({mode:'securecore'})">Secure Core</button>
        <button class="danger" onclick="disconnect()">Disconnect</button>
      </div>
      <div class="control-row"><input id="server-id" placeholder="Server ID, e.g. US-FREE#63"><button onclick="connectServer()">Connect ID</button></div>
      <div class="control-row"><input id="country" placeholder="Country code/name, e.g. US or Germany"><button onclick="connectCountry()">Connect Country</button></div>
      <div class="control-row"><input id="city" placeholder="City, e.g. Los Angeles"><button onclick="connectCity()">Connect City</button></div>
      <pre id="message"></pre>
    </section>
    <section class="card">
      <div class="label">Recommended Features</div>
      <div class="muted">Kill switch standard is recommended. NetShield and VPN Accelerator will enable automatically only if the current Proton plan supports them.</div>
      <div class="actions">
        <button onclick="setConfig('kill-switch','standard')">Kill switch standard</button>
        <button onclick="setConfig('kill-switch','off')">Kill switch off</button>
        <button onclick="setConfig('netshield','malware-ads-trackers')">NetShield recommended</button>
        <button onclick="setConfig('vpn-accelerator','on')">VPN Accelerator on</button>
      </div>
      <pre id="config"></pre>
    </section>
  </div>
  <section class="card" style="margin-top:16px">
    <div class="server-tools">
      <input id="search" placeholder="Search server, country, city, feature..." oninput="renderServers()">
      <select id="tier" onchange="renderServers()"><option value="">All tiers</option><option value="0">Free</option><option value="2">Plus</option></select>
      <button onclick="loadServers()">Refresh list</button>
    </div>
    <div class="muted" id="server-count">Loading servers...</div>
    <div class="servers"><table><thead><tr><th>Server</th><th>Location</th><th>Load</th><th>Features</th><th></th></tr></thead><tbody id="servers"></tbody></table></div>
  </section>
</div>
<script>
let servers=[]; let busy=false;
function setBusy(value){busy=value;document.querySelectorAll('button').forEach(b=>b.disabled=value)}
function loadClass(load){return load<60?'good':load<85?'warn':'bad'}
function esc(v){return String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}
function renderStatus(data){const status=String(data.status||'Unknown');const norm=status.toLowerCase();document.getElementById('dot').className='dot '+(norm==='connected'?'connected':norm==='disconnected'?'disconnected':'');document.getElementById('status').textContent=status;document.getElementById('server').textContent=data.details?.server||data.raw||'No active server'}
async function loadStatus(){try{const r=await fetch('/api/status',{cache:'no-store'});renderStatus(await r.json())}catch(e){document.getElementById('message').textContent='Status failed'}}
async function loadConfig(){const r=await fetch('/api/config',{cache:'no-store'});const d=await r.json();document.getElementById('config').textContent=d.raw||''}
async function setConfig(setting,value){setBusy(true);try{const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({setting,value})});const d=await r.json();document.getElementById('message').textContent=d.message||'';await loadConfig()}finally{setBusy(false)}}
async function connect(payload){setBusy(true);document.getElementById('message').textContent='Connecting...';try{const r=await fetch('/api/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=await r.json();document.getElementById('message').textContent=d.message||'';renderStatus(d)}finally{setBusy(false)}}
function connectServer(){const server=document.getElementById('server-id').value.trim();if(server)connect({mode:'server',server})}
function connectCountry(){const country=document.getElementById('country').value.trim();if(country)connect({mode:'country',country})}
function connectCity(){const city=document.getElementById('city').value.trim();if(city)connect({mode:'city',city})}
async function disconnect(){setBusy(true);document.getElementById('message').textContent='Disconnecting...';try{const r=await fetch('/api/disconnect',{method:'POST'});const d=await r.json();document.getElementById('message').textContent=d.message||'';renderStatus(d)}finally{setBusy(false)}}
async function loadServers(){const r=await fetch('/api/servers',{cache:'no-store'});const d=await r.json();servers=d.servers||[];renderServers()}
function renderServers(){const q=document.getElementById('search').value.trim().toLowerCase();const tier=document.getElementById('tier').value;const filtered=servers.filter(s=>{if(tier!==''&&String(s.tier)!==tier)return false;const hay=[s.name,s.exit_country,s.entry_country,s.city,s.region,s.domain,(s.features||[]).join(' ')].join(' ').toLowerCase();return !q||hay.includes(q)});document.getElementById('server-count').textContent=`${filtered.length} of ${servers.length} servers`;document.getElementById('servers').innerHTML=filtered.map(s=>`<tr><td><strong>${esc(s.name)}</strong><div class="muted">${esc(s.domain)}</div></td><td>${esc(s.city||'-')}, ${esc(s.exit_country)}</td><td class="load ${loadClass(Number(s.load||0))}">${s.load??'-'}%</td><td>${(s.features||[]).map(f=>`<span class="pill">${esc(f)}</span>`).join('')}</td><td><button data-server="${esc(s.name)}" onclick="connect({mode:'server',server:this.dataset.server})">Connect</button></td></tr>`).join('')}
loadStatus();loadConfig();loadServers();setInterval(loadStatus,15000);
</script></body></html>"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port, debug=False, threaded=True)
