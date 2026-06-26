#!/usr/bin/env bash
# Subtitle pipeline audit.
set +e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AUDIT_DIR="${AUDIT_DIR:-$ROOT/diagnostics/cast-manager-audit/latest}"
mkdir -p "$AUDIT_DIR"

BASE="${CAST_MANAGER_URL:-}"
if [ -z "$BASE" ]; then
  curl -sS -m 3 http://127.0.0.1:8004/api/cast/status >/dev/null 2>&1 && BASE="http://127.0.0.1:8004" || BASE="http://REDACTED_SERVER_IP:8004"
fi

JSON_OUT="$AUDIT_DIR/subtitles.json"
MD_OUT="$AUDIT_DIR/subtitles-report.md"

python3 <<'PY'
import json, urllib.request, urllib.parse, ssl, os

BASE = os.environ.get("CAST_MANAGER_URL") or "http://REDACTED_SERVER_IP:8004"
AUDIT_DIR = os.environ.get("AUDIT_DIR", "diagnostics/cast-manager-audit/latest")
ctx = ssl.create_default_context()

def api(path, method="GET", body=None, timeout=60):
    url = BASE + path if path.startswith("/") else path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
        headers={"Content-Type":"application/json"} if body else {})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.status, json.loads(r.read().decode())

def search(q):
    st, d = api(f"/api/search?q={urllib.parse.quote(q)}")
    return d.get("results",[])

# Find videos with sidecar subs
videos = []
for ext in ["srt","sub","mkv","mp4"]:
    for r in search(f".{ext}"):
        p = r.get("path","")
        if r.get("is_directory"): continue
        if p.lower().endswith((".mkv",".mp4")):
            videos.append(p)

seen = set()
tests = []
for vp in videos[:12]:
    if vp in seen: continue
    seen.add(vp)
    st, subs = api("/api/subtitles", "POST", {"filePath": vp})
    items = subs.get("subtitles") or []
    analyze_st, analyze = api("/api/media/analyze", "POST",
        {"filePath": vp, "target": "chromecast", "autoTranscode": "auto"}, timeout=90)
    embedded = (analyze.get("analysis") or {}).get("subtitleStreams") or []
    entry = {
        "videoPath": vp,
        "sidecarCount": len([i for i in items if i.get("kind")=="sidecar"]),
        "embeddedCount": len([i for i in items if i.get("kind")=="embedded"]),
        "subtitles": items,
        "embeddedStreams": embedded,
        "vttTests": [],
    }
    for item in items[:3]:
        sid = item.get("id")
        if not sid: continue
        vtt_url = f"{BASE}/api/subtitles/{sid}.vtt"
        try:
            req = urllib.request.Request(vtt_url)
            with urllib.request.urlopen(req, timeout=45, context=ctx) as r:
                body = r.read(2000).decode("utf-8","replace")
                entry["vttTests"].append({
                    "id": sid, "label": item.get("label"),
                    "status": r.status, "contentType": r.headers.get("Content-Type"),
                    "validWebVtt": body.startswith("WEBVTT"),
                    "preview": body[:300],
                })
        except Exception as e:
            entry["vttTests"].append({"id": sid, "error": str(e)})
    # prepare endpoint
    if items:
        try:
            st2, prep = api("/api/subtitles/prepare", "POST",
                {"filePath": vp, "subtitleId": items[0]["id"]}, timeout=60)
            entry["prepare"] = {"status": st2, "response": prep}
        except Exception as e:
            entry["prepare"] = {"error": str(e)}
    tests.append(entry)

# Direct sidecar files
sidecars = []
for ext in ["srt","sub","idx"]:
    for r in search(f".{ext}"):
        if not r.get("is_directory"):
            sidecars.append(r.get("path"))

report = {
    "baseUrl": BASE,
    "videoTests": tests,
    "sidecarFilesFound": sidecars[:30],
    "burnInNote": "CAST_SUBTITLE_BURN_IN_FALLBACK may be required for SUB/IDX on Chromecast",
}
os.makedirs(AUDIT_DIR, exist_ok=True)
with open(os.path.join(AUDIT_DIR,"subtitles.json"),"w") as f:
    json.dump(report,f,indent=2)

lines = ["# Subtitles Audit", f"Base: {BASE}", ""]
for t in tests:
    lines.append(f"## `{t['videoPath']}`")
    lines.append(f"- Sidecars: {t['sidecarCount']}, Embedded: {t['embeddedCount']}")
    for v in t.get("vttTests",[]):
        if v.get("error"):
            lines.append(f"- VTT {v.get('id')}: ERROR {v['error']}")
        else:
            lines.append(f"- VTT {v.get('label')}: {v['status']} valid={v.get('validWebVtt')}")
    if t.get("prepare",{}).get("error"):
        lines.append(f"- Prepare: ERROR {t['prepare']['error']}")
    lines.append("")
with open(os.path.join(AUDIT_DIR,"subtitles-report.md"),"w") as f:
    f.write("\n".join(lines))
print("Saved subtitles reports")
PY
