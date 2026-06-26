#!/usr/bin/env bash
# Thumbnail and preview endpoint audit.
set +e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AUDIT_DIR="${AUDIT_DIR:-$ROOT/diagnostics/cast-manager-audit/latest}"
mkdir -p "$AUDIT_DIR"

BASE="${CAST_MANAGER_URL:-}"
if [ -z "$BASE" ]; then
  curl -sS -m 3 http://127.0.0.1:8004/api/cast/status >/dev/null 2>&1 && BASE="http://127.0.0.1:8004" || BASE="http://REDACTED_SERVER_IP:8004"
fi

JSON_OUT="$AUDIT_DIR/thumbnails-previews.json"
MD_OUT="$AUDIT_DIR/thumbnails-previews.md"

python3 <<'PY'
import json, urllib.request, urllib.parse, ssl, os, base64

BASE = os.environ.get("CAST_MANAGER_URL") or "http://REDACTED_SERVER_IP:8004"
AUDIT_DIR = os.environ.get("AUDIT_DIR", "diagnostics/cast-manager-audit/latest")
ctx = ssl.create_default_context()

def api(path, method="GET", body=None, timeout=90):
    url = path if path.startswith("http") else BASE + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
        headers={"Content-Type":"application/json"} if body else {})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        ct = r.headers.get("Content-Type","")
        raw = r.read()
        try:
            return r.status, ct, json.loads(raw.decode())
        except Exception:
            return r.status, ct, {"rawLen": len(raw), "rawPrefix": raw[:200]}

def find_ext(ext):
    try:
        _,_,d = api(f"/api/search?q=.{ext}")
        for r in d.get("results",[]):
            if not r.get("is_directory"):
                return r["path"]
    except Exception:
        pass
    return None

samples = {
    "mkv_video_thumb": find_ext("mkv"),
    "mp4_video_thumb": find_ext("mp4"),
    "mpeg_video_thumb": find_ext("mpeg") or find_ext("mpg"),
    "jpg_image": find_ext("jpg") or find_ext("jpeg"),
    "png_image": find_ext("png"),
    "txt_text": find_ext("txt"),
    "nfo_text": find_ext("nfo"),
    "srt_subtitle": find_ext("srt"),
    "sub_subtitle": find_ext("sub"),
    "idx_subtitle": find_ext("idx"),
}

results = []

# Video thumbnails
for label, path, typ in [
    ("mkv","mkv_video_thumb","video"), ("mp4","mp4_video_thumb","video"),
    ("mpeg","mpeg_video_thumb","video"),
]:
    p = samples.get(f"{label}_video_thumb") or samples.get(f"{label}_image")
    entry = {"type": f"video_thumbnail_{label}", "path": p}
    if not p:
        entry["status"] = "skipped"; entry["error"] = "no sample file"
        results.append(entry); continue
    st, ct, body = api("/api/thumbnail", "POST", {"filePath": p, "type": typ})
    entry.update({"endpoint": "POST /api/thumbnail", "status": st, "contentType": ct, "response": body})
    thumb = (body or {}).get("thumbnail")
    if thumb:
        serve = thumb if thumb.startswith("http") else BASE + thumb
        try:
            req = urllib.request.Request(serve, method="HEAD")
            with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                entry["serveStatus"] = r.status
                entry["serveContentType"] = r.headers.get("Content-Type")
                entry["serveLength"] = r.headers.get("Content-Length")
        except Exception as e:
            entry["serveError"] = str(e)
    else:
        entry["thumbnailFailed"] = True
    results.append(entry)

# Image preview via stream/read
for label, key in [("jpg","jpg_image"),("png","png_image")]:
    p = samples.get(key)
    entry = {"type": f"image_preview_{label}", "path": p}
    if not p:
        entry["status"] = "skipped"; results.append(entry); continue
    q = urllib.parse.quote(p, safe="")
    st, ct, body = api(f"/api/files/stream?path={q}", timeout=30)
    entry.update({"endpoint": "GET /api/files/stream", "status": st, "contentType": ct})
    results.append(entry)

# Text / NFO via read
for label, key in [("txt","txt_text"),("nfo","nfo_text")]:
    p = samples.get(key)
    entry = {"type": f"text_preview_{label}", "path": p}
    if not p:
        entry["status"] = "skipped"; results.append(entry); continue
    q = urllib.parse.quote(p, safe="")
    try:
        req = urllib.request.Request(f"{BASE}/api/files/read?path={q}")
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            raw = r.read(4000).decode("utf-8","replace")
            entry.update({"endpoint":"GET /api/files/read","status":r.status,"previewLen":len(raw),"preview":raw[:500]})
    except Exception as e:
        entry["error"] = str(e)
    results.append(entry)

# Subtitles listing
for label, key in [("srt","srt_subtitle"),("sub","sub_subtitle"),("idx","idx_subtitle")]:
    p = samples.get(key)
    entry = {"type": f"subtitle_file_{label}", "path": p}
    if not p:
        entry["status"] = "skipped"; results.append(entry); continue
    # find paired video in same dir
    import os
    parent = os.path.dirname(p)
    base = os.path.splitext(os.path.basename(p))[0]
    video = None
    try:
        _,_,d = api(f"/api/search?q={os.path.basename(parent)}")
        for r in d.get("results",[]):
            if r.get("path","").startswith(parent) and r.get("path","").lower().endswith((".mkv",".mp4")):
                if base.split(".")[0] in r.get("name",""):
                    video = r["path"]; break
    except Exception: pass
    entry["pairedVideo"] = video
    if video:
        st, ct, body = api("/api/subtitles", "POST", {"filePath": video})
        entry.update({"endpoint":"POST /api/subtitles (paired video)","status":st,"subtitles":body})
    else:
        entry["note"] = "No paired video found for sidecar test"
    results.append(entry)

os.makedirs(AUDIT_DIR, exist_ok=True)
with open(os.path.join(AUDIT_DIR,"thumbnails-previews.json"),"w") as f:
    json.dump({"baseUrl":BASE,"samples":samples,"results":results},f,indent=2)

lines = ["# Thumbnails & Previews Audit", f"Base: {BASE}", ""]
for r in results:
    lines.append(f"## {r['type']}")
    lines.append(f"- Path: `{r.get('path','n/a')}`")
    if r.get("endpoint"): lines.append(f"- Endpoint: {r['endpoint']}")
    if r.get("status"): lines.append(f"- Status: {r['status']}")
    if r.get("thumbnailFailed"): lines.append("- **FAIL**: thumbnail null")
    if r.get("serveError"): lines.append(f"- Serve error: {r['serveError']}")
    if r.get("serveStatus"): lines.append(f"- Serve: {r['serveStatus']} {r.get('serveContentType')} ({r.get('serveLength')} bytes)")
    if r.get("error"): lines.append(f"- Error: {r['error']}")
    lines.append("")
with open(os.path.join(AUDIT_DIR,"thumbnails-previews.md"),"w") as f:
    f.write("\n".join(lines))
print("Saved thumbnails-previews reports")
PY
