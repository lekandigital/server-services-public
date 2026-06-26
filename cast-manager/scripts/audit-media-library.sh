#!/usr/bin/env bash
# Media library inventory via Cast Manager API (remote DOWNLOAD_DIR).
set +e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AUDIT_DIR="${AUDIT_DIR:-$ROOT/diagnostics/cast-manager-audit/latest}"
mkdir -p "$AUDIT_DIR"

BASE="${CAST_MANAGER_URL:-}"
if [ -z "$BASE" ]; then
  curl -sS -m 3 http://127.0.0.1:8004/api/cast/status >/dev/null 2>&1 && BASE="http://127.0.0.1:8004" || BASE="http://REDACTED_SERVER_IP:8004"
fi

JSON_OUT="$AUDIT_DIR/media-inventory.json"
MD_OUT="$AUDIT_DIR/media-inventory.md"

python3 <<PY
import json, urllib.request, urllib.parse, ssl
from collections import Counter, defaultdict

BASE = "$BASE"
OUT_JSON = "$JSON_OUT"
OUT_MD = "$MD_OUT"
ctx = ssl.create_default_context()

def fetch(url, method="GET", body=None, timeout=60):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
        headers={"Content-Type": "application/json"} if body else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)

def get_json(url, **kw):
    st, raw = fetch(url, **kw)
    try:
        return json.loads(raw), st
    except:
        return {"error": raw, "status": st}, st

# Storage stats (includes largest files)
stats, _ = get_json(f"{BASE}/api/storage/stats", timeout=90)
largest = stats.get("largestFiles", [])

# Reindex + sample search for extension counts
ext_counts = Counter()
mime_counts = Counter()
cast_type_counts = Counter()
all_files = []
images, texts, subtitles, videos = [], [], [], []

VIDEO_EXT = {'.mkv','.mp4','.avi','.mov','.webm','.m4v','.mpeg','.mpg','.ts'}
IMAGE_EXT = {'.jpg','.jpeg','.png','.gif','.webp','.bmp'}
TEXT_EXT = {'.txt','.nfo','.md','.log'}
SUB_EXT = {'.srt','.sub','.idx','.ass','.vtt'}

def infer_cast_type(ext, name=''):
    ext = (ext or '').lower()
    if ext in VIDEO_EXT: return 'video'
    if ext in {'.mp3','.flac','.m4a','.aac','.ogg','.wav','.opus'}: return 'audio'
    if ext in IMAGE_EXT: return 'image'
    if ext in TEXT_EXT: return 'text'
    if ext in SUB_EXT: return 'subtitle'
    return 'other'

# Search common extensions
for ext in ['mkv','mp4','mpeg','mpg','jpg','png','txt','nfo','srt','sub','idx','mp3']:
    data, _ = get_json(f"{BASE}/api/search?q=.{ext}", timeout=20)
    for r in data.get('results', []):
        if r.get('is_directory'): continue
        p = r.get('path','')
        e = (r.get('extension') or '').lower()
        if not e and '.' in r.get('name',''):
            e = '.' + r['name'].rsplit('.',1)[-1].lower()
        ext_counts[e] += 1
        ct = infer_cast_type(e)
        cast_type_counts[ct] += 1
        entry = {
            'path': p, 'name': r.get('name'), 'extension': e,
            'size': r.get('size', 0), 'castType': ct,
        }
        all_files.append(entry)
        if ct == 'video': videos.append(entry)
        elif ct == 'image': images.append(entry)
        elif ct == 'text': texts.append(entry)
        elif ct == 'subtitle': subtitles.append(entry)

# Dedupe by path
seen = set()
unique = []
for f in all_files:
    if f['path'] in seen: continue
    seen.add(f['path'])
    unique.append(f)

# Probe top videos (up to 8) + largest
probe_paths = []
for f in largest[:5]:
    if f.get('path'): probe_paths.append(f['path'])
for v in sorted(videos, key=lambda x: -x.get('size',0))[:5]:
    if v['path'] not in probe_paths:
        probe_paths.append(v['path'])

video_details = []
for p in probe_paths[:10]:
    q = urllib.parse.quote(p, safe='')
    info, st = get_json(f"{BASE}/api/media/info?path={q}", timeout=45)
    analyze, _ = get_json(f"{BASE}/api/media/analyze", method="POST",
        body={"filePath": p, "target": "chromecast", "autoTranscode": "auto"}, timeout=60)
    subs, _ = get_json(f"{BASE}/api/subtitles", method="POST", body={"filePath": p}, timeout=30)
    sz = next((x.get('size',0) for x in largest if x.get('path')==p), 0)
    if not sz:
        sz = next((x.get('size',0) for x in unique if x.get('path')==p), 0)
    video_details.append({
        'path': p,
        'size': sz,
        'duration': info.get('duration'),
        'container': (analyze.get('analysis') or {}).get('container') or path_ext(p),
        'videoCodec': info.get('videoCodec') or (analyze.get('analysis') or {}).get('videoCodec'),
        'audioCodec': info.get('audioCodec') or (analyze.get('analysis') or {}).get('audioCodec'),
        'resolution': info.get('resolution'),
        'bitrate': (analyze.get('analysis') or {}).get('bitrate'),
        'subtitleStreams': len((analyze.get('analysis') or {}).get('subtitleStreams') or []),
        'sidecarSubtitles': len(subs.get('subtitles') or []),
        'mediaInfoStatus': st,
        'playbackMode': analyze.get('playbackMode'),
        'reasons': analyze.get('reasons') or [],
    })

def path_ext(p):
    import os
    return os.path.splitext(p)[1].lstrip('.')

# Subtitle pairing heuristic
subtitle_pairs = []
for s in subtitles:
    base = s['name'].rsplit('.',1)[0] if s.get('name') else ''
    pair = next((v['path'] for v in videos if base and base in v.get('name','')), None)
    subtitle_pairs.append({'subtitle': s['path'], 'likelyVideo': pair})

report = {
    'baseUrl': BASE,
    'storageStats': stats,
    'totalSampledFiles': len(unique),
    'countsByExtension': dict(ext_counts.most_common()),
    'countsByCastType': dict(cast_type_counts),
    'largestFiles': largest,
    'videoDetails': video_details,
    'imagesSample': images[:20],
    'textsSample': texts[:20],
    'subtitlesSample': subtitles[:20],
    'subtitlePairs': subtitle_pairs,
    'note': 'Counts from /api/search per extension; not exhaustive filesystem scan.',
}

with open(OUT_JSON, 'w') as f:
    json.dump(report, f, indent=2)

lines = [
    '# Cast Manager Media Library Inventory',
    f'Base URL: {BASE}',
    '',
    '## Storage overview',
    f"- Total space: {stats.get('totalSpace')}",
    f"- Used: {stats.get('usedSpace')}",
    f"- Free: {stats.get('freeSpace')}",
    '',
    '## Counts by extension (search-sampled)',
]
for k,v in ext_counts.most_common():
    lines.append(f'- `{k}`: {v}')
lines += ['', '## Counts by Cast Manager type']
for k,v in cast_type_counts.most_common():
    lines.append(f'- {k}: {v}')
lines += ['', '## Largest files']
for f in largest[:15]:
    gb = (f.get('size',0) or 0) / (1024**3)
    lines.append(f"- {gb:.2f} GB — `{f.get('path')}`")
lines += ['', '## Video probe details']
for v in video_details:
    lines.append(f"### `{v['path']}`")
    lines.append(f"- Size: {v['size']} bytes")
    lines.append(f"- Duration: {v.get('duration')}s")
    lines.append(f"- Container: {v.get('container')}")
    lines.append(f"- Video: {v.get('videoCodec')} @ {v.get('resolution')}")
    lines.append(f"- Audio: {v.get('audioCodec')}")
    lines.append(f"- Playback mode: {v.get('playbackMode')}")
    if v.get('reasons'):
        lines.append(f"- Reasons: {', '.join(v['reasons'][:3])}")
    lines.append('')

with open(OUT_MD, 'w') as f:
    f.write('\n'.join(lines))
print(f"Saved: {OUT_JSON}")
print(f"Saved: {OUT_MD}")
PY
