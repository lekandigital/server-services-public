#!/usr/bin/env python3
"""Audit Cast Manager HTTP API endpoints."""
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("CAST_MANAGER_URL", "http://REDACTED_SERVER_IP:8004")
AUDIT_DIR = os.environ.get("AUDIT_DIR", "diagnostics/cast-manager-audit/latest")
JSON_OUT = os.path.join(AUDIT_DIR, "api-endpoints.json")
MD_OUT = os.path.join(AUDIT_DIR, "api-endpoints.md")
ctx = ssl.create_default_context()


def fetch(method, url, body=None, timeout=45):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            raw = r.read(12000).decode("utf-8", "replace")
            return r.status, r.headers.get("Content-Type", ""), raw
    except urllib.error.HTTPError as e:
        raw = e.read(8000).decode("utf-8", "replace")
        return e.code, e.headers.get("Content-Type", ""), raw
    except Exception as e:
        return 0, "", str(e)


def get_json(url, timeout=30):
    st, ct, raw = fetch("GET", url, timeout=timeout)
    try:
        return json.loads(raw), st, ct, raw
    except Exception:
        return None, st, ct, raw


def head(url, timeout=30):
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, r.headers.get("Content-Type", ""), ""
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read(500).decode("utf-8", "replace")
    except Exception as e:
        return 0, "", str(e)


def main():
    os.makedirs(AUDIT_DIR, exist_ok=True)
    stats, _, _, _ = get_json(f"{BASE}/api/storage/stats", timeout=90)
    fp = None
    for f in (stats or {}).get("largestFiles", []):
        p = f.get("path", "")
        if p.lower().endswith((".mkv", ".mp4")):
            fp = p
            break
    if not fp:
        fp = "/tmp/cast-manager-test-media/known_good_h264_aac.mp4"
    qfp = urllib.parse.quote(fp, safe="")

    endpoints = [
        ("GET", f"{BASE}/", None, "App shell / home"),
        ("GET", f"{BASE}/file-manager", None, "File manager alias"),
        ("GET", f"{BASE}/api/files", None, "File browser listing"),
        ("GET", f"{BASE}/api/files/recent", None, "Recent files list"),
        ("POST", f"{BASE}/api/files/recent", {"path": fp, "action": "play_video"}, "Recent tracking (frontend POST)"),
        ("POST", f"{BASE}/api/files/duration", {"path": fp}, "Duration lookup"),
        ("POST", f"{BASE}/api/files/info", {"path": fp}, "File metadata"),
        ("GET", f"{BASE}/api/media/info?path={qfp}", None, "Media probe"),
        ("POST", f"{BASE}/api/media/analyze", {"filePath": fp, "target": "chromecast"}, "Compatibility analysis"),
        ("POST", f"{BASE}/api/thumbnail", {"filePath": fp, "type": "video"}, "Video thumbnail"),
        ("GET", f"{BASE}/api/cast/status", None, "Cast status / now playing"),
        ("GET", f"{BASE}/api/devices", None, "Legacy device list"),
        ("GET", f"{BASE}/api/cast/devices", None, "Cast device discovery"),
        ("POST", f"{BASE}/api/cast/controls", {"action": "pause"}, "Playback controls pause"),
        ("GET", f"{BASE}/api/torrents", None, "Torrent list"),
        ("GET", f"{BASE}/api/disk", None, "Disk usage"),
        ("GET", f"{BASE}/api/files/starred", None, "Starred files"),
        ("GET", f"{BASE}/api/files/starred-folders", None, "Starred folders"),
        ("GET", f"{BASE}/api/files/trash", None, "Trash"),
        ("GET", f"{BASE}/api/shares", None, "Shares list"),
        ("POST", f"{BASE}/api/share", {"path": fp, "expiresInHours": 24}, "Create share"),
        ("GET", f"{BASE}/api/storage/stats", None, "Storage summary"),
        ("GET", f"{BASE}/api/storage/dirs", None, "Directory usage"),
        ("GET", f"{BASE}/api/activity", None, "Activity log"),
        ("GET", f"{BASE}/api/search?q=mkv", None, "Search"),
        ("GET", f"{BASE}/api/tags", None, "Tags"),
        ("GET", f"{BASE}/api/stream/tokens", None, "Stream tokens"),
        ("GET", f"{BASE}/api/qrcode?url={urllib.parse.quote(BASE)}", None, "QR code"),
        ("GET", f"{BASE}/api/cast/diagnostics", None, "Cast diagnostics"),
        ("GET", f"{BASE}/api/cast/doctor", None, "Cast doctor"),
        ("GET", f"{BASE}/api/receiver/status", None, "Receiver status"),
        ("POST", f"{BASE}/api/subtitles", {"filePath": fp}, "Subtitle discovery"),
        ("GET", f"{BASE}/api/files/stream?path={qfp}&raw=1", None, "Browser stream raw GET"),
        ("HEAD", f"{BASE}/api/files/stream?path={qfp}&raw=1", None, "Browser stream HEAD"),
    ]

    results = []
    for method, url, body, feature in endpoints:
        if method == "HEAD":
            st, ct, raw = head(url, timeout=15)
        else:
            st, ct, raw = fetch(method, url, body, timeout=90 if "thumbnail" in url else 45)
        is_json = "json" in (ct or "").lower()
        is_html = any(x in raw for x in ("<!DOCTYPE", "<html", "Cannot POST", "Cannot GET", "Bad Request"))
        parsed = None
        if is_json:
            try:
                parsed = json.loads(raw)
            except Exception:
                pass
        results.append({
            "method": method, "url": url, "requestBody": body,
            "status": st, "contentType": ct, "isJson": is_json,
            "isHtmlError": is_html and not is_json,
            "likelyFeature": feature, "bodySnippet": raw[:400], "parsed": parsed,
        })

    fetch("POST", f"{BASE}/api/cast/controls", {"action": "play"})

    out = {"baseUrl": BASE, "sampleFile": fp, "endpoints": results}
    with open(JSON_OUT, "w") as f:
        json.dump(out, f, indent=2)

    lines = [
        "# Cast Manager API Endpoint Audit", f"Base URL: {BASE}", f"Sample file: `{fp}`", "",
        "| Method | Status | JSON | HTML err | Feature | Notes |",
        "|--------|--------|------|----------|---------|-------|",
    ]
    for e in results:
        notes = (e.get("bodySnippet") or "")[:70].replace("|", "/").replace("\n", " ")
        lines.append(
            f"| {e['method']} | {e['status']} | {'yes' if e['isJson'] else 'no'} | "
            f"{'YES' if e['isHtmlError'] else 'no'} | {e['likelyFeature']} | {notes} |"
        )
    with open(MD_OUT, "w") as f:
        f.write("\n".join(lines))
    print(f"Saved: {JSON_OUT}")
    print(f"Saved: {MD_OUT}")


if __name__ == "__main__":
    main()
