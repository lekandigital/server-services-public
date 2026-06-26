#!/usr/bin/env python3
"""Proton VPN control portal for the server.

The portal is a pure HTTP API + web UI. VPN always-on is handled by the
separate proton-vpn-watchdog service, which writes state to WATCHDOG_STATE_FILE.
"""

import argparse
import json
import os
import subprocess
import time
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
# Written by proton-vpn-watchdog.py; read-only here.
WATCHDOG_STATE_FILE = Path(
    os.environ.get("VPN_WATCHDOG_STATE", "/tmp/vpn-watchdog.json")
)
# Created before a connect/rotate, removed after. Watchdog backs off while set.
PORTAL_LOCK_FILE = Path(
    os.environ.get("VPN_PORTAL_LOCK", "/tmp/vpn-portal.lock")
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


def portal_lock() -> None:
    try:
        PORTAL_LOCK_FILE.touch()
    except Exception:
        pass


def portal_unlock() -> None:
    try:
        PORTAL_LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def feature_names(bitmask):
    names = [name for bit, name in FEATURES.items() if bitmask & bit]
    return names or ["Standard"]


# Serverlist cache — avoid rereading disk on every /api/rotate call.
_SERVERLIST_CACHE: list = []
_SERVERLIST_TS: float = 0.0
_SERVERLIST_TTL = 300  # 5 minutes


def load_servers(force: bool = False):
    global _SERVERLIST_CACHE, _SERVERLIST_TS
    if not force and _SERVERLIST_CACHE and (time.time() - _SERVERLIST_TS) < _SERVERLIST_TTL:
        return _SERVERLIST_CACHE

    if not SERVERLIST_PATH.exists():
        run_proton(["protonvpn", "status"], timeout=30)
    try:
        payload = json.loads(SERVERLIST_PATH.read_text())
    except Exception:
        return _SERVERLIST_CACHE or []

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
                # score is Proton's own server quality metric (lower = better).
                # It factors in load, latency, and capacity — more reliable than
                # load alone, which can lag behind reality.
                "score": item.get("Score"),
                "status": "online" if item.get("Status") == 1 else "maintenance",
                "features": feature_names(int(item.get("Features") or 0)),
            }
        )
    servers.sort(key=lambda row: (row["exit_country"], row["city"], row["name"]))

    _SERVERLIST_CACHE = servers
    _SERVERLIST_TS = time.time()
    return servers


def pick_best_server(exclude_name: str = "", prefer_country: str = "US"):
    """Pick the best server for a VPN rotation.

    Uses Proton's own ``score`` field (lower = better) as the primary key;
    falls back to load for servers without a score. Prefers ``prefer_country``
    and excludes the currently-connected server so the rotation actually
    changes the outbound IP."""
    servers = load_servers()
    online = [
        s for s in servers
        if s["status"] == "online" and s["name"] != exclude_name
    ]

    country_pool = [s for s in online if s["exit_country"].upper() == prefer_country.upper()]
    # If preferred country has candidates, use them; otherwise fall back globally.
    pool = country_pool if country_pool else online
    if not pool:
        return None

    return min(pool, key=lambda s: (s.get("score") or 999.0, s.get("load") or 100))


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return Response(HTML_PAGE.replace("__HOST_IP__", HOST_IP), mimetype="text/html")


@app.route("/api/status")
def api_status():
    return jsonify(vpn_status())


@app.route("/api/servers")
def api_servers():
    force = request.args.get("refresh") == "1"
    return jsonify({"servers": load_servers(force=force)})


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

    portal_lock()
    try:
        code, output = run_proton(cmd, timeout=120)
    finally:
        portal_unlock()
    payload = vpn_status()
    payload.update({"ok": code == 0, "message": output})
    return jsonify(payload), 200 if code == 0 else 500


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    portal_lock()
    try:
        code, output = run_proton(["protonvpn", "disconnect"], timeout=60)
    finally:
        portal_unlock()
    payload = vpn_status()
    payload.update({"ok": code == 0, "message": output})
    return jsonify(payload), 200 if code == 0 else 500


_PLAN_ERROR_PHRASES = (
    "free plan",
    "upgrade",
    "not available on",
    "location selection",
)


def _is_plan_error(output: str) -> bool:
    lo = output.lower()
    return any(p in lo for p in _PLAN_ERROR_PHRASES)


@app.route("/api/rotate", methods=["POST"])
def api_rotate():
    """Switch to the best available server in a different location.

    Body (optional JSON):
      ``country``  — ISO-2 exit country to prefer (default ``"US"``).

    Strategy (Proton Unlimited):
      1. Pick the lowest-score server in the preferred country, excluding the
         currently-connected one, and connect by server ID.
      2. If the CLI rejects it with a plan error (Unlimited not yet active in
         the CLI session), fall back to ``protonvpn connect --random`` so the
         IP still changes.

    The response is only sent after the new tunnel is established."""
    data = request.get_json() or {}
    prefer_country = str(data.get("country", "US")).upper()

    current_status = vpn_status()
    current_server = current_status.get("details", {}).get("server", "")

    best = pick_best_server(exclude_name=current_server, prefer_country=prefer_country)

    portal_lock()
    try:
        run_proton(["protonvpn", "disconnect"], timeout=60)

        code, output = (-1, "") if best is None else run_proton(
            ["protonvpn", "connect", best["name"]], timeout=120
        )

        fallback_used = False
        if code != 0 or _is_plan_error(output):
            # Named-server connect failed (no candidate or plan restriction).
            # Stay in the preferred country first, then fastest, then random —
            # in all cases the IP changes, which is the point of a rotation.
            for fb_cmd in (
                ["protonvpn", "connect", "--country", prefer_country],
                ["protonvpn", "connect"],
                ["protonvpn", "connect", "--random"],
            ):
                code, output = run_proton(fb_cmd, timeout=120)
                if code == 0 and not _is_plan_error(output):
                    break
            fallback_used = True
    finally:
        portal_unlock()

    payload = vpn_status()
    payload.update({
        "ok": code == 0,
        "message": output,
        "rotated_to": best["name"] if (best and not fallback_used) else None,
        "rotated_to_score": best.get("score") if best else None,
        "rotated_to_load": best.get("load") if best else None,
        "previous_server": current_server,
        "fallback_used": fallback_used,
    })
    return jsonify(payload), 200 if code == 0 else 500


@app.route("/api/watchdog")
def api_watchdog():
    """Return always-on watchdog state written by proton-vpn-watchdog.service."""
    try:
        state = json.loads(WATCHDOG_STATE_FILE.read_text())
        state["state_file_found"] = True
    except FileNotFoundError:
        state = {"state_file_found": False, "note": "proton-vpn-watchdog.service not running or state not written yet"}
    except Exception as exc:
        state = {"state_file_found": False, "error": str(exc)}
    return jsonify(state)


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
button.primary{background:var(--blue);border-color:var(--blue);color:#0d1117;font-weight:700}button.danger{border-color:rgba(248,81,73,.5);color:#ffb4ad}button.rotate{border-color:rgba(210,153,34,.5);color:var(--gold)}button:disabled{opacity:.55;cursor:wait}
input,select{background:#0d1117;border:1px solid var(--border);border-radius:8px;color:var(--text);padding:9px 10px;min-width:0}
.control-row{display:grid;grid-template-columns:1fr auto;gap:8px;margin-top:10px}.label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px}
.server-tools{display:grid;grid-template-columns:1fr auto auto;gap:8px;margin-bottom:10px}.servers{max-height:680px;overflow:auto;border:1px solid var(--border);border-radius:8px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:9px 10px;border-bottom:1px solid rgba(143,163,191,.14);vertical-align:top}th{position:sticky;top:0;background:#111827;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}
.pill{display:inline-flex;border:1px solid rgba(143,163,191,.25);border-radius:999px;padding:2px 7px;margin:0 4px 4px 0;color:var(--muted);font-size:11px}.load.good{color:var(--green)}.load.warn{color:var(--gold)}.load.bad{color:var(--red)}
.watchdog-bar{display:flex;align-items:center;gap:16px;padding:10px 14px;background:var(--card);border:1px solid var(--border);border-radius:8px;margin-bottom:14px;font-size:12px;color:var(--muted)}
pre{white-space:pre-wrap;word-break:break-word;color:var(--muted);font:12px ui-monospace,SFMono-Regular,Consolas,monospace;margin:12px 0 0}
@media(max-width:900px){.grid{grid-template-columns:1fr}.server-tools{grid-template-columns:1fr}.control-row{grid-template-columns:1fr}}
</style></head><body>
<div class="shell">
  <div class="top"><div><h1>Proton VPN</h1><div class="subtitle">Always-on VPN portal for __HOST_IP__</div></div><a href="http://__HOST_IP__:8001/">Service directory</a></div>
  <div class="watchdog-bar"><span id="wd-dot" class="dot"></span><span>Watchdog:</span><span id="wd-reconnects">—</span><span id="wd-last">—</span></div>
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
        <button class="rotate" onclick="rotate()">Rotate IP</button>
        <button class="danger" onclick="disconnect()">Disconnect</button>
      </div>
      <div class="control-row"><input id="server-id" placeholder="Server ID, e.g. US-FREE#63"><button onclick="connectServer()">Connect ID</button></div>
      <div class="control-row"><input id="country" placeholder="Country code/name, e.g. US or Germany"><button onclick="connectCountry()">Connect Country</button></div>
      <div class="control-row"><input id="city" placeholder="City, e.g. Los Angeles"><button onclick="connectCity()">Connect City</button></div>
      <pre id="message"></pre>
    </section>
    <section class="card">
      <div class="label">Settings</div>
      <div class="muted">Kill switch standard is recommended. NetShield and VPN Accelerator activate based on your Proton plan.</div>
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
    <div class="servers"><table><thead><tr><th>Server</th><th>Location</th><th>Load</th><th>Score</th><th>Features</th><th></th></tr></thead><tbody id="servers"></tbody></table></div>
  </section>
</div>
<script>
let servers=[]; let busy=false;
function setBusy(v){busy=v;document.querySelectorAll('button').forEach(b=>b.disabled=v)}
function loadClass(load){return load<60?'good':load<85?'warn':'bad'}
function esc(v){return String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}
function timeAgo(ts){if(!ts)return'never';const s=Math.round(Date.now()/1000-ts);if(s<5)return'just now';if(s<60)return s+'s ago';if(s<3600)return Math.round(s/60)+'m ago';return Math.round(s/3600)+'h ago'}
function renderStatus(data){const st=String(data.status||'Unknown');const n=st.toLowerCase();document.getElementById('dot').className='dot '+(n==='connected'?'connected':n==='disconnected'?'disconnected':'');document.getElementById('status').textContent=st;document.getElementById('server').textContent=data.details?.server||data.raw||'No active server'}
async function loadStatus(){try{const r=await fetch('/api/status',{cache:'no-store'});renderStatus(await r.json())}catch(e){document.getElementById('message').textContent='Status check failed'}}
async function loadWatchdog(){try{const r=await fetch('/api/watchdog',{cache:'no-store'});const d=await r.json();const ok=d.state_file_found&&d.last_status==='Connected';document.getElementById('wd-dot').className='dot '+(ok?'connected':'disconnected');document.getElementById('wd-reconnects').textContent='Reconnects: '+(d.reconnects??'—');document.getElementById('wd-last').textContent='Last check: '+timeAgo(d.last_check_ts)+' · '+( d.last_status||'unknown')}catch(e){}}
async function loadConfig(){const r=await fetch('/api/config',{cache:'no-store'});const d=await r.json();document.getElementById('config').textContent=d.raw||''}
async function setConfig(s,v){setBusy(true);try{const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({setting:s,value:v})});const d=await r.json();document.getElementById('message').textContent=d.message||'';await loadConfig()}finally{setBusy(false)}}
async function connect(payload){setBusy(true);document.getElementById('message').textContent='Connecting...';try{const r=await fetch('/api/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=await r.json();document.getElementById('message').textContent=d.message||'';renderStatus(d)}finally{setBusy(false)}}
async function rotate(){setBusy(true);document.getElementById('message').textContent='Rotating to a new server…';try{const r=await fetch('/api/rotate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({country:'US'})});const d=await r.json();document.getElementById('message').textContent=d.ok?`Rotated: ${d.previous_server} → ${d.rotated_to} (score ${d.rotated_to_score?.toFixed(2)??'?'}, load ${d.rotated_to_load}%)`:d.message||'Rotate failed';renderStatus(d)}finally{setBusy(false)}}
function connectServer(){const s=document.getElementById('server-id').value.trim();if(s)connect({mode:'server',server:s})}
function connectCountry(){const c=document.getElementById('country').value.trim();if(c)connect({mode:'country',country:c})}
function connectCity(){const c=document.getElementById('city').value.trim();if(c)connect({mode:'city',city:c})}
async function disconnect(){setBusy(true);document.getElementById('message').textContent='Disconnecting...';try{const r=await fetch('/api/disconnect',{method:'POST'});const d=await r.json();document.getElementById('message').textContent=d.message||'';renderStatus(d)}finally{setBusy(false)}}
async function loadServers(){const r=await fetch('/api/servers?refresh=1',{cache:'no-store'});const d=await r.json();servers=d.servers||[];renderServers()}
function renderServers(){const q=document.getElementById('search').value.trim().toLowerCase();const tier=document.getElementById('tier').value;const filtered=servers.filter(s=>{if(tier!==''&&String(s.tier)!==tier)return false;const hay=[s.name,s.exit_country,s.entry_country,s.city,s.region,s.domain,(s.features||[]).join(' ')].join(' ').toLowerCase();return !q||hay.includes(q)});document.getElementById('server-count').textContent=`${filtered.length} of ${servers.length} servers`;document.getElementById('servers').innerHTML=filtered.map(s=>`<tr><td><strong>${esc(s.name)}</strong><div class="muted">${esc(s.domain)}</div></td><td>${esc(s.city||'-')}, ${esc(s.exit_country)}</td><td class="load ${loadClass(Number(s.load||0))}">${s.load??'-'}%</td><td class="muted">${s.score!=null?Number(s.score).toFixed(2):'-'}</td><td>${(s.features||[]).map(f=>`<span class="pill">${esc(f)}</span>`).join('')}</td><td><button data-server="${esc(s.name)}" onclick="connect({mode:'server',server:this.dataset.server})">Connect</button></td></tr>`).join('')}
loadStatus();loadConfig();loadServers();loadWatchdog();
setInterval(loadStatus,15000);setInterval(loadWatchdog,15000);
</script></body></html>"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port, debug=False, threaded=True)
