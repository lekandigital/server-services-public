#!/usr/bin/env bash
# Browser streaming / HTTP range audit for representative media files.
set +e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AUDIT_DIR="${AUDIT_DIR:-$ROOT/diagnostics/cast-manager-audit/latest}"
mkdir -p "$AUDIT_DIR"

BASE="${CAST_MANAGER_URL:-}"
if [ -z "$BASE" ]; then
  curl -sS -m 3 http://127.0.0.1:8004/api/cast/status >/dev/null 2>&1 && BASE="http://127.0.0.1:8004" || BASE="http://REDACTED_SERVER_IP:8004"
fi

JSON_OUT="$AUDIT_DIR/streaming-range.json"
MD_OUT="$AUDIT_DIR/streaming-range-report.md"

python3 <<'PY'
import json, urllib.request, urllib.parse, ssl, re, os

BASE = os.environ.get("CAST_MANAGER_URL") or ""
if not BASE:
    import subprocess
    try:
        subprocess.check_output(["curl","-sS","-m","3","http://127.0.0.1:8004/api/cast/status"], stderr=subprocess.DEVNULL)
        BASE = "http://127.0.0.1:8004"
    except Exception:
        BASE = "http://REDACTED_SERVER_IP:8004"

AUDIT_DIR = os.environ.get("AUDIT_DIR", "diagnostics/cast-manager-audit/latest")
JSON_OUT = os.path.join(AUDIT_DIR, "streaming-range.json")
MD_OUT = os.path.join(AUDIT_DIR, "streaming-range-report.md")
ctx = ssl.create_default_context()

def get_json(url, body=None, timeout=60):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method="POST" if body else "GET",
        headers={"Content-Type":"application/json"} if body else {})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode())

def head_or_get(url, range_hdr=None, method="HEAD", timeout=30):
    headers = {}
    if range_hdr:
        headers["Range"] = range_hdr
    req = urllib.request.Request(url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return {
                "status": r.status,
                "headers": {k.lower(): v for k,v in r.headers.items()},
                "bodyLen": len(r.read(1024*1024)) if method=="GET" else 0,
            }
    except urllib.error.HTTPError as e:
        body = e.read(500).decode("utf-8","replace")
        return {
            "status": e.code,
            "headers": {k.lower(): v for k,v in e.headers.items()},
            "errorBody": body[:300],
            "bodyLen": 0,
        }

stats = get_json(f"{BASE}/api/storage/stats", timeout=90)
largest = stats.get("largestFiles", [])
candidates = {"mp4": None, "mkv_small": None, "mkv_large": None, "mpeg": None}

for f in largest:
    p = f.get("path","")
    ext = os.path.splitext(p)[1].lower()
    sz = f.get("size",0)
    if ext == ".mp4" and not candidates["mp4"]:
        candidates["mp4"] = p
    if ext == ".mkv":
        if not candidates["mkv_large"] or sz > (get_json if False else 0):
            candidates["mkv_large"] = p
        if not candidates["mkv_small"] or sz < 500*1024*1024:
            if candidates["mkv_small"] is None or sz < get_size(candidates, "mkv_small", largest):
                candidates["mkv_small"] = p
    if ext in (".mpeg",".mpg") and not candidates["mpeg"]:
        candidates["mpeg"] = p

def get_size(cands, key, files):
    p = cands[key]
    for f in files:
        if f.get("path")==p: return f.get("size",0)
    return 0

# fill mp4/mkv_small from search if needed
for ext, key in [("mp4","mp4"),("mkv","mkv_small")]:
    if candidates[key]: continue
    try:
        r = get_json(f"{BASE}/api/search?q=.{ext}")
        for item in r.get("results",[]):
            if not item.get("is_directory"):
                candidates[key] = item["path"]; break
    except Exception: pass

def probe_file(label, path):
    if not path:
        return {"label": label, "skipped": True, "reason": "no file found"}
    q = urllib.parse.quote(path, safe="")
    info = {}
    try:
        info = get_json(f"{BASE}/api/media/info?path={q}", timeout=45)
    except Exception as e:
        info = {"error": str(e)}
    stream_url = f"{BASE}/api/files/stream?path={q}&raw=1"
    tests = []
    # HEAD
    h = head_or_get(stream_url, method="HEAD")
    tests.append({"name":"HEAD","range":None, **h})
    size = int(h.get("headers",{}).get("content-length",0) or info.get("size",0) or 0)
    ranges = [
        ("bytes=0-", "start"),
        ("bytes=10000000-", "10MB offset") if size > 20000000 else ("bytes=1000-", "1KB offset"),
    ]
    if size > 0:
        mid = max(0, size//2 - 5*1024*1024)
        end = max(0, size - 10*1024*1024)
        ranges += [
            (f"bytes={mid}-", "near-middle"),
            (f"bytes={end}-", "near-end"),
        ]
    for rng, name in ranges:
        g = head_or_get(stream_url, range_hdr=rng, method="GET", timeout=45)
        hdrs = g.get("headers",{})
        cr = hdrs.get("content-range","")
        cl = hdrs.get("content-length","")
        ar = hdrs.get("accept-ranges","")
        ct = hdrs.get("content-type","")
        chunk_ok = True
        if g.get("status") == 206:
            m = re.match(r"bytes (\d+)-(\d+)/(\d+|\*)", cr)
            if m:
                start, end_b, total = int(m.group(1)), int(m.group(2)), m.group(3)
                expected = end_b - start + 1
                if cl and int(cl) != expected:
                    chunk_ok = False
        tests.append({
            "name": name, "range": rng, "status": g.get("status"),
            "contentRange": cr, "contentLength": cl, "acceptRanges": ar,
            "contentType": ct, "bodyLen": g.get("bodyLen"), "chunkMathOk": chunk_ok,
        })
    ten_mb_pattern = any(
        t.get("range","").startswith("bytes=") and t.get("status")==206
        and t.get("contentLength") and int(t.get("contentLength") or 0) <= 10*1024*1024+100
        for t in tests if "offset" in t.get("name","")
    )
    return {
        "label": label, "path": path, "ffprobeDuration": info.get("duration"),
        "fileSize": info.get("size") or size,
        "streamUrl": stream_url,
        "tests": tests,
        "tenMbChunkLikely": ten_mb_pattern,
        "uiRisk": "HIGH — 10MB range chunks may cause scrubber stutter on large files" if ten_mb_pattern and (info.get("size") or size) > 1e9 else (
            "MEDIUM — range works but transcoding path may differ" if any(t.get("status")==206 for t in tests) else "HIGH — no 206 range support"
        ),
    }

results = []
for label, key in [("MP4","mp4"),("MKV small/normal","mkv_small"),("MKV largest","mkv_large"),("video/mpeg","mpeg")]:
    results.append(probe_file(label, candidates.get(key)))

out = {"baseUrl": BASE, "candidates": candidates, "files": results}
os.makedirs(AUDIT_DIR, exist_ok=True)
with open(JSON_OUT,"w") as f: json.dump(out,f,indent=2)

lines = ["# Browser Streaming / Range Audit", f"Base: {BASE}", ""]
for r in results:
    lines.append(f"## {r['label']}")
    if r.get("skipped"):
        lines.append(f"SKIPPED: {r.get('reason')}"); lines.append(""); continue
    lines.append(f"- Path: `{r['path']}`")
    lines.append(f"- Duration: {r.get('ffprobeDuration')}s")
    lines.append(f"- UI risk: **{r.get('uiRisk')}**")
    lines.append(f"- 10MB chunk pattern: {r.get('tenMbChunkLikely')}")
    lines.append("")
    lines.append("| Test | Range | Status | Content-Range | Length | Type |")
    lines.append("|------|-------|--------|---------------|--------|------|")
    for t in r.get("tests",[]):
        lines.append(f"| {t.get('name')} | {t.get('range','')} | {t.get('status')} | {str(t.get('contentRange',''))[:40]} | {t.get('contentLength','')} | {t.get('contentType','')} |")
    lines.append("")
with open(MD_OUT,"w") as f: f.write("\n".join(lines))
print(f"Saved: {JSON_OUT}")
print(f"Saved: {MD_OUT}")
PY
