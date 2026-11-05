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
import re
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


VPN_IF = os.environ.get("VPN_IF", "proton0")
HANDSHAKE_MAX_AGE = 180  # seconds; WireGuard rekeys well inside this

# The tunnel is stable when left alone: NetworkManager holds the WireGuard
# profiles with autoconnect=yes and restores proton0 on its own within ~60s.
# Reconnecting eagerly is worse than waiting -- rapidly re-activating stale
# ProtonVPN profiles got them reaped (NM: reason 'connection-removed', 94 in
# 10 minutes) and the watchdog then chased the outage it had created. So:
# require the tunnel to be down for several consecutive checks, and rate-limit
# reconnect attempts hard.
DOWN_CHECKS_BEFORE_RECONNECT = int(os.environ.get("VPN_DOWN_CHECKS", "3"))
MIN_RECONNECT_INTERVAL = int(os.environ.get("VPN_MIN_RECONNECT_INTERVAL", "180"))


def tunnel_up() -> bool:
    """True if proton0 exists and WireGuard handshook recently."""
    try:
        if subprocess.run(["ip", "link", "show", VPN_IF],
                          capture_output=True, timeout=5).returncode != 0:
            return False
        r = subprocess.run(["sudo", "wg", "show", VPN_IF, "latest-handshakes"],
                           capture_output=True, text=True, timeout=5, check=False)
        stamps = [int(l.split()[1]) for l in r.stdout.splitlines() if len(l.split()) > 1]
        return bool(stamps) and (time.time() - max(stamps)) < HANDSHAKE_MAX_AGE
    except Exception:
        return False


def egress_ok() -> bool:
    """True if a TLS handshake actually completes (catches a tunnel that is
    handshaking but silently not forwarding -- the locked-keyring failure).

    A bare TCP connect is not enough: with an expired Proton certificate the
    node still completes the TCP handshake, then drops the stream (curl:
    'unexpected eof while reading'). Seen 2026-09-11 -- WireGuard handshakes
    and a TCP-only check both passed while DNS and all HTTPS were dead."""
    import socket
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for host in ("1.1.1.1", "9.9.9.9"):
        try:
            with socket.create_connection((host, 443), timeout=6) as sock:
                with ctx.wrap_socket(sock, server_hostname=host):
                    return True
        except OSError:
            continue
    return False


def is_connected() -> tuple[bool, str]:
    """Return (connected, server_name), decided from the DATA PLANE.

    Deliberately does not run `protonvpn status`. On this box every Proton CLI
    invocation reconciles NetworkManager state and deletes the ProtonVPN
    profile currently bound to proton0 -- NM logs the teardown as
    reason 'connection-removed'. Polling status every 20s therefore tore the
    tunnel down every 20s, and the watchdog then "recovered" from an outage it
    had caused itself. Verified: with the watchdog stopped, proton0 stayed up
    30/30 samples over 90s; with it running it dropped every ~20-30s.

    The CLI is now only touched when we genuinely need to reconnect.
    """
    if tunnel_up() and egress_ok():
        return True, nm_active_profile() or "unknown"
    return False, ""


def is_portal_locked() -> bool:
    """Return True while the portal is actively managing a connection."""
    if not LOCK_FILE.exists():
        return False
    try:
        return (time.time() - LOCK_FILE.stat().st_mtime) < LOCK_TTL
    except Exception:
        return False


def nm_proton_uuid() -> str:
    """UUID of the most recently used ProtonVPN WireGuard profile.

    Must return a UUID, not a name. Duplicate leftover profiles all share names
    like 'ProtonVPN US-NY#169'; `nmcli connection up <name>` then activates a
    random one, NM logs reason 'new-activation', and proton0 flaps every few
    seconds. That is what was killing torrents (trackers: Connection failed).
    """
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "UUID,TYPE,TIMESTAMP,NAME", "connection", "show"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception:
        return ""
    best, best_ts = "", -1
    for line in r.stdout.splitlines():
        # uuid:type:timestamp:name — UUID has no colons; name may.
        parts = line.split(":", 3)
        if len(parts) != 4:
            continue
        uuid, ctype, ts, name = parts
        if ctype != "wireguard" or not name.startswith("ProtonVPN"):
            continue
        try:
            ts_i = int(ts)
        except ValueError:
            continue
        if ts_i > best_ts:
            best, best_ts = uuid, ts_i
    return best


def nm_active_uuid() -> str:
    """UUID of the NM connection currently bound to proton0."""
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "UUID,DEVICE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception:
        return ""
    for line in r.stdout.splitlines():
        uuid, _, dev = line.rpartition(":")
        if dev == VPN_IF:
            return uuid
    return ""


def nm_prune_stale_profiles() -> None:
    """Keep one NM profile per ProtonVPN *name*; drop colliding copies.

    The Proton app legitimately creates one profile per server. The failure
    mode is duplicate UUIDs that share a name (e.g. twelve 'US-NY#169's).
    `nmcli connection up <name>` then picks at random, NM logs reason
    'new-activation', and proton0 flaps. Distinct server names are left alone.
    Autoconnect stays on only for the profile currently bound to proton0.
    """
    keep_active = nm_active_uuid()
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "UUID,TYPE,TIMESTAMP,NAME", "connection", "show"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except Exception:
        return
    by_name: dict[str, list[tuple[int, str]]] = {}
    for line in r.stdout.splitlines():
        parts = line.split(":", 3)
        if len(parts) != 4:
            continue
        uuid, ctype, ts, name = parts
        if ctype != "wireguard" or not name.startswith("ProtonVPN"):
            continue
        try:
            ts_i = int(ts)
        except ValueError:
            ts_i = 0
        by_name.setdefault(name, []).append((ts_i, uuid))

    deleted = 0
    for name, items in by_name.items():
        items.sort(reverse=True)
        uuids = [u for _, u in items]
        winner = keep_active if keep_active in uuids else uuids[0]
        for uuid in uuids:
            if uuid == winner:
                continue
            subprocess.run(
                ["nmcli", "connection", "delete", uuid],
                capture_output=True, text=True, timeout=20, check=False,
            )
            deleted += 1
        auto = "yes" if winner == keep_active else "no"
        subprocess.run(
            ["nmcli", "connection", "modify", winner, "connection.autoconnect", auto],
            capture_output=True, text=True, timeout=15, check=False,
        )
    if deleted:
        print(f"[watchdog] pruned {deleted} duplicate ProtonVPN NM profiles; active={keep_active}", flush=True)


def nm_active_profile() -> str:
    """Name of the NM connection currently bound to proton0."""
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception:
        return ""
    for line in r.stdout.splitlines():
        name, _, dev = line.rpartition(":")
        if dev == VPN_IF:
            return name
    return ""


def nm_reconnect() -> bool:
    """Bring the tunnel up via NetworkManager using a profile UUID.

    Never call the Proton CLI here. `protonvpn connect` / `status` on this box
    both (1) fail and (2) leave extra NM profiles with the same name, which then
    steal proton0 from each other (NM reason 'new-activation').
    """
    profile = nm_active_uuid() or nm_proton_uuid()
    if not profile:
        return False
    print(f"[watchdog] bringing up NM profile {profile}", flush=True)
    try:
        subprocess.run(["nmcli", "connection", "up", "uuid", profile],
                       capture_output=True, text=True, timeout=90, check=False)
    except Exception as exc:
        print(f"[watchdog] nmcli up failed: {exc}", flush=True)
        return False
    for _ in range(15):  # let the handshake land
        if tunnel_up():
            nm_prune_stale_profiles()
            return True
        time.sleep(2)
    return False


# Last resort when re-activating the NM profile brings the handshake back but
# not traffic. Re-activating reuses the same credentials, so it cannot recover
# from an expired Proton certificate (vpn-cli.log: 'ExpiredCertificate') --
# only the CLI fetches a fresh one. The CLI leaves orphaned killswitch
# profiles behind, so it is rate-limited well beyond MIN_RECONNECT_INTERVAL.
CLI_FALLBACK_INTERVAL = int(os.environ.get("VPN_CLI_FALLBACK_INTERVAL", "900"))
_last_cli_fallback_ts = 0.0


def proton_cli_reconnect() -> bool:
    global _last_cli_fallback_ts
    since = time.time() - _last_cli_fallback_ts
    if since < CLI_FALLBACK_INTERVAL:
        print(f"[watchdog] CLI fallback used {int(since)}s ago — skipping", flush=True)
        return False
    _last_cli_fallback_ts = time.time()
    print("[watchdog] NM profile up but no egress — reconnecting via Proton CLI", flush=True)
    code, out = run_proton("connect", "--country", "US", timeout=120)
    print(f"[watchdog] protonvpn connect rc={code}: {out[-200:]}", flush=True)
    for _ in range(10):
        if tunnel_up() and egress_ok():
            return True
        time.sleep(3)
    return False


def reconnect() -> tuple[bool, str]:
    """Bring proton0 back, preferring NetworkManager over the Proton CLI.

    CLI connect spawns duplicate NM profiles, so re-activate the existing
    WireGuard profile by UUID first; fall back to the CLI only when that
    restores the handshake but not traffic. Returns (success, method)."""
    success = False
    method = ""
    if nm_reconnect() and egress_ok():
        success, method = True, "networkmanager"
    elif proton_cli_reconnect():
        success, method = True, "proton-cli"

    if success:
        # Sync the kill-switch to the NEW fwmark Proton just picked. Edits the
        # chain in place -- unlike restarting transmission-vpn-lock.service,
        # which flushes it (a brief window with uid 131 unconfined) and bounces
        # transmission-daemon along with it.
        print("[watchdog] syncing transmission kill-switch to new VPN fwmark", flush=True)
        subprocess.run(["sudo", "/usr/local/sbin/transmission-vpn-fwmark-sync.sh"], check=False)

    return success, method


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


def get_active_torrents_speed() -> tuple[int, float, int]:
    """Returns (active torrents, total download speed KB/s, torrents actually transferring).

    The third value excludes torrents sitting in Idle. A torrent with no
    reachable seeders is Idle at 0 KB/s forever, and treating that as evidence
    of a slow VPN made the watchdog rotate a perfectly healthy tunnel on a
    loop. A genuine VPN outage is caught by is_connected() instead."""
    try:
        r = subprocess.run(
            ["/usr/bin/transmission-remote", "-l"],
            capture_output=True, text=True, timeout=15, check=True
        )
        lines = r.stdout.splitlines()
    except Exception as e:
        print(f"[watchdog] failed to run transmission-remote: {e}", flush=True)
        return 0, 0.0, 0

    if len(lines) < 3:
        return 0, 0.0, 0

    active_count = 0
    total_speed = 0.0
    transferring_count = 0

    for line in lines[1:-1]:
        line = line.strip()
        if not line:
            continue
        match = re.match(
            r"^\s*(\d+)\*?\s+([\d.]+%|n\/a)\s+(.+?)\s{2,}(.+?)\s{2,}([\d.]+)\s+([\d.]+)\s+([\d.]+|None)\s+(.+?)\s{2,}(.+)$",
            line
        )
        if match:
            done_str = match.group(2)
            down_speed = float(match.group(6))
            status = match.group(8).strip().lower()
            
            progress = 0.0
            if done_str.endswith("%"):
                try:
                    progress = float(done_str[:-1]) / 100.0
                except ValueError:
                    pass
            
            is_downloading = (
                progress < 1.0 and 
                any(s in status for s in ["download", "up & down", "verify", "check", "idle"])
            )
            if is_downloading:
                active_count += 1
                total_speed += down_speed
                if "download" in status or "up & down" in status:
                    transferring_count += 1

    return active_count, total_speed, transferring_count


state: dict = {
    "reconnects": 0,
    "last_check_ts": None,
    "last_reconnect_ts": None,
    "last_status": "starting",
}

speed_history = []
last_speed_reconnect_ts = 0
consecutive_down = 0
last_reconnect_attempt_ts = 0.0
SPEED_RECONNECT_COOLDOWN = 600  # 10 minutes
SPEED_THRESHOLD = 400.0  # KB/s
WINDOW_SIZE = max(1, 60 // INTERVAL)

print(f"[watchdog] starting (interval={INTERVAL}s, state={STATE_FILE}, threshold={SPEED_THRESHOLD}KB/s)", flush=True)
# Collapse 200+ leftover ProtonVPN copies before they steal proton0 again.
nm_prune_stale_profiles()

while True:
    try:
        connected, current_server = is_connected()
        state["last_check_ts"] = time.time()
        state["last_status"] = "Connected" if connected else "Disconnected"
        state["current_server"] = current_server

        if not connected:
            consecutive_down += 1
            if is_portal_locked():
                print("[watchdog] portal lock active — skipping this cycle", flush=True)
                write_state(state)
                time.sleep(INTERVAL)
                continue

            # Give NetworkManager's own autoconnect a chance first.
            if consecutive_down < DOWN_CHECKS_BEFORE_RECONNECT:
                print(
                    f"[watchdog] VPN down ({consecutive_down}/"
                    f"{DOWN_CHECKS_BEFORE_RECONNECT}) — waiting for NM autoconnect",
                    flush=True,
                )
                write_state(state)
                time.sleep(INTERVAL)
                continue

            since = time.time() - last_reconnect_attempt_ts
            if since < MIN_RECONNECT_INTERVAL:
                print(
                    f"[watchdog] VPN down but last attempt was {int(since)}s ago — "
                    f"holding off until {MIN_RECONNECT_INTERVAL}s",
                    flush=True,
                )
                write_state(state)
                time.sleep(INTERVAL)
                continue

            print("[watchdog] VPN is down — reconnecting", flush=True)
            last_reconnect_attempt_ts = time.time()
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
                speed_history.clear()
            else:
                print("[watchdog] reconnect failed — will retry next cycle", flush=True)
        else:
            consecutive_down = 0
            # Do not rotate on "slow torrents". Reconnect is what spawned
            # duplicate NM profiles and flapped proton0 (reason 'new-activation'),
            # which is why trackers sat on Connection failed. Stay on this tunnel
            # unless the data plane is actually down.

        write_state(state)
    except Exception as exc:
        print(f"[watchdog] unhandled error: {exc}", flush=True)

    time.sleep(INTERVAL)
