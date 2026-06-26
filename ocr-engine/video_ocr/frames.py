"""Frame extraction from video files using ffmpeg/ffprobe."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def get_video_info(video_path: str) -> dict:
    """Use ffprobe to get video metadata.

    Returns dict with keys:
      duration (float), width (int), height (int), fps (float),
      codec (str), nb_frames (int|None)
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as exc:
        log.warning("ffprobe failed: %s", exc)
        return {
            "duration": 0.0,
            "width": 0,
            "height": 0,
            "fps": 0.0,
            "codec": "unknown",
            "nb_frames": None,
        }

    info = {
        "duration": 0.0,
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "codec": "unknown",
        "nb_frames": None,
    }

    # Parse format-level duration
    fmt = data.get("format", {})
    info["duration"] = float(fmt.get("duration", 0))

    # Find the video stream
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            info["width"] = int(stream.get("width", 0))
            info["height"] = int(stream.get("height", 0))
            info["codec"] = stream.get("codec_name", "unknown")

            # Parse frame rate (avg_frame_rate is "num/den" like "30/1")
            fps_str = stream.get("avg_frame_rate", "0/1")
            try:
                num, den = fps_str.split("/")
                info["fps"] = float(num) / float(den) if float(den) > 0 else 0.0
            except (ValueError, ZeroDivisionError):
                info["fps"] = 0.0

            # Number of frames
            nb = stream.get("nb_frames")
            if nb and nb != "N/A":
                try:
                    info["nb_frames"] = int(nb)
                except ValueError:
                    pass

            # Stream-level duration may be more accurate
            if not info["duration"]:
                info["duration"] = float(stream.get("duration", 0))

            break

    return info


def extract_frames(
    video_path: str,
    output_dir: str,
    fps: float = 4.0,
    max_width: int = 2560,
    max_frames: Optional[int] = None,
    scene_change: str = "off",
) -> list[dict]:
    """Extract frames from a video using ffmpeg.

    Args:
        video_path:   Path to the video file.
        output_dir:   Directory to save extracted frames.
        fps:          Frames per second to sample.
        max_width:    Maximum width; frames are scaled down if wider.
        max_frames:   Optional cap on total frames extracted.
        scene_change: "off", "light", or "aggressive" scene change detection.

    Returns:
        List of dicts: [{"path": str, "index": int, "timestamp": float}, ...]
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Build the ffmpeg video filter chain
    vf_parts = []

    # FPS filter
    vf_parts.append(f"fps={fps}")

    # Scale filter — maintain aspect ratio, only downscale
    vf_parts.append(f"scale='min({max_width},iw)':-2")

    # Scene change filter (optional)
    if scene_change == "light":
        vf_parts.append("select='gt(scene\\,0.3)'")
    elif scene_change == "aggressive":
        vf_parts.append("select='gt(scene\\,0.15)'")

    vf = ",".join(vf_parts)

    # Frame limit via -frames:v
    frame_pattern = str(output_path / "frame_%08d.png")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video_path),
        "-vf", vf,
        "-vsync", "vfr",
        "-q:v", "2",  # High-quality PNG encoding
    ]

    if max_frames:
        cmd.extend(["-frames:v", str(max_frames)])

    cmd.append(frame_pattern)

    log.info("Extracting frames: %s", " ".join(cmd))

    try:
        subprocess.run(cmd, timeout=1800, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        log.error("ffmpeg failed: %s", exc.stderr)
        raise RuntimeError(f"Frame extraction failed: {exc.stderr[:500]}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Frame extraction timed out (30 min limit)")

    # Collect extracted frame paths
    frames = []
    for frame_path in sorted(output_path.glob("frame_*.png")):
        idx = int(frame_path.stem.split("_")[1])
        timestamp = (idx - 1) / fps  # 1-indexed frame number to timestamp
        frames.append({
            "path": str(frame_path),
            "index": idx,
            "timestamp": round(timestamp, 3),
        })

    log.info("Extracted %d frames", len(frames))
    return frames
