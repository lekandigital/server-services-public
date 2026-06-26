#!/usr/bin/env bash
# Create tiny synthetic test media for cast reliability tests.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${CAST_TEST_MEDIA_DIR:-/tmp/cast-manager-test-media}"
DUR="${CAST_TEST_DURATION:-30}"

mkdir -p "$OUT"
echo "Creating test media in $OUT (duration ${DUR}s)"

ffmpeg -hide_banner -y \
  -f lavfi -i "testsrc=duration=${DUR}:size=640x360:rate=24" \
  -f lavfi -i "sine=frequency=440:duration=${DUR}" \
  -c:v libx264 -pix_fmt yuv420p -profile:v baseline -level 3.0 \
  -c:a aac -b:a 128k -ac 2 \
  "$OUT/known_good_h264_aac.mp4"

cat > "$OUT/known_good_h264_aac.vtt" <<'VTT'
WEBVTT

00:00:01.000 --> 00:00:05.000
Cast test subtitle — visible cue

00:00:06.000 --> 00:00:10.000
Second test subtitle line
VTT

cat > "$OUT/known_good_h264_aac.srt" <<'SRT'
1
00:00:01,000 --> 00:00:05,000
Cast test subtitle — visible cue

2
00:00:06,000 --> 00:00:10,000
Second test subtitle line
SRT

# H.264 + AC3 in MKV (triggers audio transcode path)
if ffmpeg -hide_banner -encoders 2>/dev/null | grep -q ac3; then
  ffmpeg -hide_banner -y \
    -f lavfi -i "testsrc=duration=${DUR}:size=640x360:rate=24" \
    -f lavfi -i "sine=frequency=220:duration=${DUR}" \
    -c:v libx264 -pix_fmt yuv420p -c:a ac3 -b:a 192k \
    "$OUT/h264_ac3_in_mkv.mkv" 2>/dev/null || echo "skip: ac3 encode failed"
else
  echo "skip: ac3 encoder unavailable"
fi

# Embedded subtitle MKV
ffmpeg -hide_banner -y \
  -f lavfi -i "testsrc=duration=${DUR}:size=640x360:rate=24" \
  -f lavfi -i "sine=frequency=330:duration=${DUR}" \
  -f lavfi -i "srt=$OUT/known_good_h264_aac.srt" \
  -map 0:v -map 1:a -map 2:s \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -c:s srt \
  "$OUT/embedded_subtitle_test.mkv" 2>/dev/null || echo "skip: embedded subtitle mkv failed"

# HEVC if encoder available
if ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libx265; then
  ffmpeg -hide_banner -y \
    -f lavfi -i "testsrc=duration=15:size=640x360:rate=24" \
    -f lavfi -i "sine=frequency=440:duration=15" \
    -c:v libx265 -pix_fmt yuv420p -c:a aac \
    "$OUT/hevc_test.mkv" 2>/dev/null || echo "skip: hevc failed"
else
  echo "skip: libx265 unavailable"
fi

ls -lh "$OUT"
echo "Done. Manifest:"
printf '%s\n' "$OUT"/* > "$OUT/manifest.txt"
cat "$OUT/manifest.txt"
