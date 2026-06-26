#!/usr/bin/env python3
"""Proton VPN always-on watchdog.

Runs as its own systemd service (proton-vpn-watchdog.service), completely
independent of the web portal. Every INTERVAL seconds it checks whether the
VPN is connected; if not, it reconnects to the fastest available server.

State is written to WATCHDOG_STATE_FILE as JSON so the portal's /api/watchdog
endpoint can surface it without the two services needing to communicate.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

INTERVAL = int(os.environ.get("VPN_WATCHDOG_INTERVAL", "20"))
STATE_FILE = Path(os.environ.get("VPN_WATCHDOG_STATE", "/tmp/vpn-watchdog.json"))
# Portal writes this file before a connect/rotate and removes it after.
# Watchdog skips reconnect while this lock is present (max LOCK_TTL seconds).
LOCK_FILE = Path(os.environ.get("VPN_PORTAL_LOCK", "/tmp/vpn-portal.lock"))
LOCK_TTL = 180


def proton_env() -> dict:
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


def run_proton(*args: str, timeout: int = 30) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["protonvpn", *args],
            capture_output=True, text=True, timeout=timeout,
            check=False, env=proton_env(),
        )
        out = "\n".join(p.strip() for p in (r.stdout, r.stderr) if p.strip())
        return r.returncode, out
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except Exception as exc:
        return 1, str(exc)


_PLAN_ERROR_PHRASES = ("free plan", "upgrade", "not available on", "location selection")


def is_connected() -> tuple[bool, str]:
    """Return (connected, server_name)."""
    _, out = run_proton("status", timeout=15)
    connected = "Connected" in out
    server = ""
    for line in out.splitlines():
        if line.strip().lower().startswith("server:"):
            server = line.split(":", 1)[1].strip()
            break
    return connected, server


def is_portal_locked() -> bool:
    """Return True while the portal is actively managing a connection."""
    if not LOCK_FILE.exists():
        return False
    try:
        return (time.time() - LOCK_FILE.stat().st_mtime) < LOCK_TTL
    except Exception:
        return False


def reconnect() -> tuple[bool, str]:
    """Disconnect and reconnect to the best available server.

    Prefers ``--country US`` (Tier-2 Premium with Proton Unlimited), retrying
    once on transient failures. Only falls back to globally fastest when the
    plan definitely blocks location selection. Returns (success, method_used)."""
    run_proton("disconnect", timeout=30)

    # Retry once — first attempt can race with the disconnect settling.
    for attempt in range(2):
        code, out = run_proton("connect", "--country", "US", timeout=90)
        if code == 0 and not any(p in out.lower() for p in _PLAN_ERROR_PHRASES):
            return True, "country=US"
        if any(p in out.lower() for p in _PLAN_ERROR_PHRASES):
            break  # plan restriction — retrying --country won't help
        if attempt == 0:
            time.sleep(2)

    # Last resort: globally fastest (may not be US)
    code, out = run_proton("connect", timeout=90)
    return code == 0, "fastest-fallback"


def write_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state))
    except Exception as exc:
        print(f"[watchdog] failed to write state: {exc}", flush=True)


def handle_term(*_) -> None:
    print("[watchdog] received SIGTERM, exiting", flush=True)
    sys.exit(0)


signal.signal(signal.SIGTERM, handle_term)
signal.signal(signal.SIGINT, handle_term)

state: dict = {
    "reconnects": 0,
    "last_check_ts": None,
    "last_reconnect_ts": None,
    "last_status": "starting",
}

print(f"[watchdog] starting (interval={INTERVAL}s, state={STATE_FILE})", flush=True)

while True:
    try:
        connected, current_server = is_connected()
        state["last_check_ts"] = time.time()
        state["last_status"] = "Connected" if connected else "Disconnected"
        state["current_server"] = current_server

        if not connected:
            if is_portal_locked():
                print("[watchdog] portal lock active — skipping this cycle", flush=True)
                write_state(state)
                time.sleep(INTERVAL)
                continue
            print("[watchdog] VPN is down — reconnecting", flush=True)
            ok, method = reconnect()
            if ok:
                state["reconnects"] += 1
                state["last_reconnect_ts"] = time.time()
                state["last_status"] = "Connected"
                _, state["current_server"] = is_connected()
                print(
                    f"[watchdog] reconnected via {method} to {state['current_server']}"
                    f" (total: {state['reconnects']})",
                    flush=True,
                )
            else:
                print("[watchdog] reconnect failed — will retry next cycle", flush=True)

        write_state(state)
    except Exception as exc:
        print(f"[watchdog] unhandled error: {exc}", flush=True)

    time.sleep(INTERVAL)
