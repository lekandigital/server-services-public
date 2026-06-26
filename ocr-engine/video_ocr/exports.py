"""Export file generation for video OCR results."""

from __future__ import annotations

import csv
import io
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def save_all_exports(
    result_dir: str,
    ocr_boxes: list,
    all_frame_boxes: list[list] | None = None,
    video_info: dict | None = None,
    job_options: dict | None = None,
    deduped_boxes: list | None = None,
) -> dict[str, str]:
    """Generate all export files for a video OCR job.

    Args:
        result_dir:      Directory to write export files.
        ocr_boxes:       Final merged/deduped OCRBox list.
        all_frame_boxes: Per-frame OCRBox lists (before deduplication).
        video_info:      Video metadata dict from ffprobe.
        job_options:     Job options dict.
        deduped_boxes:   Deduped-only boxes (may be same as ocr_boxes).

    Returns:
        Dict mapping format key -> absolute file path.
    """
    from ocr_backends.base import OCRBox

    rd = Path(result_dir)
    rd.mkdir(parents=True, exist_ok=True)
    video_info = video_info or {}
    job_options = job_options or {}
    all_frame_boxes = all_frame_boxes or []

    files: dict[str, str] = {}

    def _text(box) -> str:
        return box.text if isinstance(box, OCRBox) else box.get("text", "")

    def _conf(box) -> float:
        c = box.confidence if isinstance(box, OCRBox) else box.get("confidence")
        return float(c) if c is not None else 0.0

    def _ts(box) -> float:
        t = box.timestamp if isinstance(box, OCRBox) else box.get("timestamp")
        return float(t) if t is not None else 0.0

    def _fi(box) -> int:
        i = box.frame_index if isinstance(box, OCRBox) else box.get("frame_index")
        return int(i) if i is not None else 0

    def _backend(box) -> str:
        return box.backend if isinstance(box, OCRBox) else box.get("backend", "")

    def _variant(box) -> str:
        v = box.variant if isinstance(box, OCRBox) else box.get("variant")
        return str(v) if v else ""

    def _scale(box) -> float:
        s = box.scale if isinstance(box, OCRBox) else box.get("scale")
        return float(s) if s is not None else 1.0

    def _box(b) -> list:
        return b.box if isinstance(b, OCRBox) else b.get("box", [])

    # --- result.txt — all text, joined ---
    text_lines = [_text(b) for b in ocr_boxes if _text(b).strip()]
    txt_content = "\n".join(text_lines)
    p = rd / "result.txt"
    p.write_text(txt_content, encoding="utf-8")
    files["txt"] = str(p)

    # --- result_deduped.txt — aggressively deduped ---
    if deduped_boxes is not None:
        dedup_lines = [_text(b) for b in deduped_boxes if _text(b).strip()]
    else:
        dedup_lines = text_lines
    p = rd / "result_deduped.txt"
    p.write_text("\n".join(dedup_lines), encoding="utf-8")
    files["deduped_txt"] = str(p)

    # --- result_by_frame.txt — grouped by frame ---
    by_frame_parts = []
    if all_frame_boxes:
        for frame_idx, frame_boxes in enumerate(all_frame_boxes):
            if not frame_boxes:
                continue
            fi = _fi(frame_boxes[0]) if frame_boxes else frame_idx + 1
            ts = _ts(frame_boxes[0]) if frame_boxes else 0.0
            by_frame_parts.append(f"--- Frame {fi} (t={ts:.2f}s) ---")
            for b in frame_boxes:
                by_frame_parts.append(_text(b))
            by_frame_parts.append("")
    else:
        # Fallback: group by frame_index in merged boxes
        frames_map: dict[int, list] = {}
        for b in ocr_boxes:
            fi = _fi(b)
            frames_map.setdefault(fi, []).append(b)
        for fi in sorted(frames_map.keys()):
            ts = _ts(frames_map[fi][0]) if frames_map[fi] else 0.0
            by_frame_parts.append(f"--- Frame {fi} (t={ts:.2f}s) ---")
            for b in frames_map[fi]:
                by_frame_parts.append(_text(b))
            by_frame_parts.append("")

    p = rd / "result_by_frame.txt"
    p.write_text("\n".join(by_frame_parts), encoding="utf-8")
    files["by_frame_txt"] = str(p)

    # --- result_with_timestamps.txt ---
    ts_parts = []
    for b in ocr_boxes:
        text = _text(b)
        if text.strip():
            ts = _ts(b)
            ts_parts.append(f"[{_format_time(ts)}] {text}")
    p = rd / "result_with_timestamps.txt"
    p.write_text("\n".join(ts_parts), encoding="utf-8")
    files["timestamps_txt"] = str(p)

    # --- result.json — full structured data ---
    json_items = []
    for b in ocr_boxes:
        json_items.append({
            "text": _text(b),
            "confidence": round(_conf(b), 4),
            "box": _box(b),
            "backend": _backend(b),
            "frame_index": _fi(b),
            "timestamp": round(_ts(b), 3),
            "variant": _variant(b),
            "scale": _scale(b),
        })
    json_data = {
        "video_info": video_info,
        "options": job_options,
        "total_lines": len(json_items),
        "lines": json_items,
    }
    p = rd / "result.json"
    p.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")
    files["json"] = str(p)

    # --- result.csv ---
    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    writer.writerow([
        "frame_index", "timestamp", "text", "confidence",
        "backend", "variant", "scale", "box",
    ])
    for b in ocr_boxes:
        writer.writerow([
            _fi(b), round(_ts(b), 3), _text(b), round(_conf(b), 4),
            _backend(b), _variant(b), _scale(b), json.dumps(_box(b)),
        ])
    p = rd / "result.csv"
    p.write_text(csv_buf.getvalue(), encoding="utf-8")
    files["csv"] = str(p)

    # --- result.srt — SubRip subtitle format ---
    srt_content = _generate_srt(ocr_boxes)
    p = rd / "result.srt"
    p.write_text(srt_content, encoding="utf-8")
    files["srt"] = str(p)

    # --- result.vtt — WebVTT subtitle format ---
    vtt_content = _generate_vtt(ocr_boxes)
    p = rd / "result.vtt"
    p.write_text(vtt_content, encoding="utf-8")
    files["vtt"] = str(p)

    # --- result.md — Markdown formatted ---
    md_content = _generate_markdown(ocr_boxes, video_info, job_options)
    p = rd / "result.md"
    p.write_text(md_content, encoding="utf-8")
    files["md"] = str(p)

    # --- debug_report.json ---
    debug_data = {
        "video_info": video_info,
        "options": job_options,
        "total_lines": len(ocr_boxes),
        "total_frames_with_text": len(all_frame_boxes) if all_frame_boxes else 0,
        "backends_used": list(set(_backend(b) for b in ocr_boxes if _backend(b))),
        "confidence_stats": _confidence_stats(ocr_boxes),
        "per_frame_line_counts": [len(fb) for fb in all_frame_boxes] if all_frame_boxes else [],
    }
    p = rd / "debug_report.json"
    p.write_text(json.dumps(debug_data, ensure_ascii=False, indent=2), encoding="utf-8")
    files["debug_json"] = str(p)

    log.info("Saved %d export files to %s", len(files), result_dir)
    return files


def _format_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _format_srt_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS,mmm (SRT uses comma)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_vtt_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm (VTT uses period)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _generate_srt(ocr_boxes: list) -> str:
    """Generate SRT subtitle content from OCR boxes."""
    from ocr_backends.base import OCRBox

    # Group consecutive lines by timestamp proximity
    groups = _group_by_timestamp(ocr_boxes)

    parts = []
    for idx, (start_ts, end_ts, lines) in enumerate(groups, 1):
        parts.append(str(idx))
        parts.append(f"{_format_srt_time(start_ts)} --> {_format_srt_time(end_ts)}")
        parts.append("\n".join(lines))
        parts.append("")

    return "\n".join(parts)


def _generate_vtt(ocr_boxes: list) -> str:
    """Generate WebVTT subtitle content from OCR boxes."""
    groups = _group_by_timestamp(ocr_boxes)

    parts = ["WEBVTT", ""]
    for idx, (start_ts, end_ts, lines) in enumerate(groups, 1):
        parts.append(f"{_format_vtt_time(start_ts)} --> {_format_vtt_time(end_ts)}")
        parts.append("\n".join(lines))
        parts.append("")

    return "\n".join(parts)


def _group_by_timestamp(ocr_boxes: list, gap: float = 0.5) -> list[tuple[float, float, list[str]]]:
    """Group OCR boxes by timestamp proximity.

    Returns list of (start_ts, end_ts, [text lines]).
    """
    from ocr_backends.base import OCRBox

    if not ocr_boxes:
        return []

    def _ts(b):
        return (b.timestamp if isinstance(b, OCRBox) else b.get("timestamp")) or 0.0

    def _text(b):
        return b.text if isinstance(b, OCRBox) else b.get("text", "")

    # Sort by timestamp
    sorted_boxes = sorted(ocr_boxes, key=_ts)

    groups = []
    current_lines = []
    current_start = _ts(sorted_boxes[0])
    current_end = current_start

    for box in sorted_boxes:
        ts = _ts(box)
        text = _text(box).strip()
        if not text:
            continue

        if ts - current_end > gap and current_lines:
            # New group
            groups.append((current_start, current_end + gap, current_lines))
            current_lines = [text]
            current_start = ts
            current_end = ts
        else:
            current_lines.append(text)
            current_end = ts

    if current_lines:
        groups.append((current_start, current_end + gap, current_lines))

    return groups


def _generate_markdown(ocr_boxes: list, video_info: dict, job_options: dict) -> str:
    """Generate a Markdown-formatted report."""
    from ocr_backends.base import OCRBox

    parts = ["# Video OCR Results", ""]

    # Video info
    if video_info:
        parts.append("## Video Information")
        parts.append(f"- **Duration**: {video_info.get('duration', 0):.1f}s")
        parts.append(f"- **Resolution**: {video_info.get('width', 0)}×{video_info.get('height', 0)}")
        parts.append(f"- **FPS**: {video_info.get('fps', 0):.1f}")
        parts.append(f"- **Codec**: {video_info.get('codec', 'unknown')}")
        parts.append("")

    # Options
    if job_options:
        parts.append("## Processing Options")
        parts.append(f"- **Quality**: {job_options.get('quality_mode', 'standard')}")
        parts.append(f"- **Backend**: {job_options.get('backend', 'paddle')}")
        parts.append(f"- **Engine Mode**: {job_options.get('engine_mode', 'single')}")
        parts.append(f"- **Strategy**: {job_options.get('video_strategy', 'scrolling-page')}")
        parts.append("")

    # Stats
    stats = _confidence_stats(ocr_boxes)
    parts.append("## Statistics")
    parts.append(f"- **Total lines**: {stats['count']}")
    parts.append(f"- **Average confidence**: {stats['avg']:.1f}%")
    parts.append(f"- **Min confidence**: {stats['min']:.1f}%")
    parts.append(f"- **Max confidence**: {stats['max']:.1f}%")
    parts.append("")

    # Extracted text
    parts.append("## Extracted Text")
    parts.append("")
    parts.append("```")
    for b in ocr_boxes:
        text = b.text if isinstance(b, OCRBox) else b.get("text", "")
        if text.strip():
            parts.append(text)
    parts.append("```")
    parts.append("")

    return "\n".join(parts)


def _confidence_stats(ocr_boxes: list) -> dict:
    """Compute confidence statistics."""
    from ocr_backends.base import OCRBox

    confidences = []
    for b in ocr_boxes:
        c = b.confidence if isinstance(b, OCRBox) else b.get("confidence")
        if c is not None:
            confidences.append(float(c) * 100)

    if not confidences:
        return {"count": len(ocr_boxes), "avg": 0.0, "min": 0.0, "max": 0.0}

    return {
        "count": len(ocr_boxes),
        "avg": sum(confidences) / len(confidences),
        "min": min(confidences),
        "max": max(confidences),
    }
