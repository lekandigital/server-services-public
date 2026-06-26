"""Main video OCR pipeline orchestrator."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)


def run_video_pipeline(
    job_id: str,
    video_path: str,
    options: dict,
    result_dir: str,
    progress_callback: Optional[Callable] = None,
) -> dict:
    """Run the full video OCR pipeline.

    Args:
        job_id:            Unique job identifier.
        video_path:        Path to the uploaded video file.
        options:           Dict of job options (backend, quality_mode, etc.).
        result_dir:        Directory to write results.
        progress_callback: Callable(progress: float, msg: str) for status updates.

    Returns:
        Dict with result summary.
    """
    from ocr_backends import (
        get_backend,
        VIDEO_OCR_QUALITY_MODES,
        VIDEO_OCR_STRATEGIES,
        OCR_ENGINE_MODES,
    )
    from ocr_backends.base import OCRBox
    from .frames import extract_frames, get_video_info
    from .preprocessing import generate_variants
    from .dedupe import dedupe_frames, dedupe_lines
    from .consensus import merge_results
    from .exports import save_all_exports

    def _progress(pct: float, msg: str):
        if progress_callback:
            progress_callback(pct, msg)

    start_time = time.time()
    _progress(0.02, "Analyzing video...")

    # ── Resolve options ──────────────────────────────────────────────────
    quality_mode = options.get("quality_mode", "standard")
    quality_cfg = VIDEO_OCR_QUALITY_MODES.get(quality_mode, VIDEO_OCR_QUALITY_MODES["standard"])

    video_strategy = options.get("video_strategy", "scrolling-page")
    strategy_cfg = VIDEO_OCR_STRATEGIES.get(video_strategy, VIDEO_OCR_STRATEGIES["scrolling-page"])

    engine_mode = options.get("engine_mode", quality_cfg.get("engine_mode", "single"))
    primary_backend_key = options.get("backend", quality_cfg.get("primary_backend", "paddle"))

    fps = float(options.get("video_fps", quality_cfg["fps"]))
    max_width = int(options.get("max_width", quality_cfg["max_width"]))
    max_frames = options.get("max_frames")
    if max_frames:
        max_frames = int(max_frames)

    scales = options.get("scales", quality_cfg.get("scales", [1.0]))
    if isinstance(scales, str):
        scales = [float(s.strip()) for s in scales.split(",")]

    preprocess_variants = options.get(
        "preprocess_variants",
        quality_cfg.get("preprocess_variants", ["native"]),
    )
    if isinstance(preprocess_variants, str):
        preprocess_variants = [v.strip() for v in preprocess_variants.split(",")]

    do_dedupe_frames = options.get("dedupe_frames", quality_cfg.get("frame_dedupe", True))
    if isinstance(do_dedupe_frames, str):
        do_dedupe_frames = do_dedupe_frames.lower() in ("true", "1", "yes")

    do_dedupe_lines = options.get("dedupe_lines", quality_cfg.get("line_dedupe", True))
    if isinstance(do_dedupe_lines, str):
        do_dedupe_lines = do_dedupe_lines.lower() in ("true", "1", "yes")

    # Strategy overrides
    if strategy_cfg.get("dedupe_lines") is False:
        do_dedupe_lines = False
    if strategy_cfg.get("dedupe_frames") is False:
        do_dedupe_frames = False

    keep_debug = options.get("keep_debug_frames", False)
    if isinstance(keep_debug, str):
        keep_debug = keep_debug.lower() in ("true", "1", "yes")

    lang = options.get("lang", "en")

    # Determine secondary/fallback backends
    fallback_backends = options.get(
        "fallback_backends",
        quality_cfg.get("fallback_backends", []),
    )
    if isinstance(fallback_backends, str):
        fallback_backends = [b.strip() for b in fallback_backends.split(",") if b.strip()]

    secondary_backends = options.get(
        "secondary_backends",
        quality_cfg.get("secondary_backends", []),
    )
    if isinstance(secondary_backends, str):
        secondary_backends = [b.strip() for b in secondary_backends.split(",") if b.strip()]

    # ── Step 1: Get video info ───────────────────────────────────────────
    _progress(0.05, "Getting video metadata...")
    video_info = get_video_info(video_path)
    log.info("Video info: %s", video_info)

    # ── Step 2: Extract frames ───────────────────────────────────────────
    _progress(0.08, f"Extracting frames at {fps} fps...")
    frames_dir = str(Path(result_dir) / "frames")
    frames = extract_frames(
        video_path, frames_dir,
        fps=fps, max_width=max_width, max_frames=max_frames,
    )
    raw_frame_count = len(frames)
    log.info("Extracted %d raw frames", raw_frame_count)

    if not frames:
        _progress(1.0, "No frames extracted")
        return {
            "raw_frame_count": 0,
            "frame_count": 0,
            "video_duration": video_info.get("duration", 0),
            "total_lines": 0,
            "deduped_lines": 0,
        }

    # ── Step 3: Frame deduplication ──────────────────────────────────────
    if do_dedupe_frames:
        _progress(0.12, "Deduplicating frames...")
        frames = dedupe_frames(frames, threshold=12)
    frame_count = len(frames)
    log.info("Frames after dedupe: %d", frame_count)

    # ── Step 4: Load backend(s) ──────────────────────────────────────────
    _progress(0.15, f"Loading OCR backend: {primary_backend_key}...")
    primary = get_backend(primary_backend_key)
    avail, err = primary.is_available()
    if not avail:
        raise RuntimeError(
            f"Backend '{primary_backend_key}' is not available: {err}"
        )
    primary.load({"lang": lang})

    # Resolve additional backends for multi-backend modes
    extra_backends = []
    if engine_mode in ("cascade", "consensus", "maximum-recall"):
        backend_list = fallback_backends if engine_mode == "cascade" else secondary_backends
        for bk in backend_list:
            try:
                b = get_backend(bk)
                b_avail, b_err = b.is_available()
                if b_avail:
                    b.load({"lang": lang})
                    extra_backends.append((bk, b))
                else:
                    log.warning("Backend '%s' unavailable: %s", bk, b_err)
            except Exception as exc:
                log.warning("Failed to load backend '%s': %s", bk, exc)

    all_backend_keys = [primary_backend_key] + [bk for bk, _ in extra_backends]
    log.info("Active backends: %s (mode=%s)", all_backend_keys, engine_mode)

    # ── Step 5: OCR each frame ───────────────────────────────────────────
    all_frame_boxes: list[list[OCRBox]] = []
    all_boxes: list[OCRBox] = []
    total_frames = len(frames)

    for fidx, frame_info in enumerate(frames):
        frame_path = frame_info["path"]
        frame_index = frame_info["index"]
        frame_ts = frame_info["timestamp"]

        pct = 0.20 + 0.60 * (fidx / total_frames)
        _progress(pct, f"OCR frame {fidx + 1}/{total_frames}...")

        # Generate preprocessing variants
        variants = generate_variants(
            frame_path, preprocess_variants, scales,
            output_dir=str(Path(result_dir) / "variants"),
        )

        # OCR each variant with each backend
        frame_results: dict[str, list[OCRBox]] = {primary_backend_key: []}
        for bk, _ in extra_backends:
            frame_results[bk] = []

        for var_info in variants:
            var_path = var_info["path"]
            var_name = var_info["variant"]
            var_scale = var_info["scale"]

            # Primary backend
            try:
                boxes = primary.ocr_image(var_path, {"lang": lang})
                for b in boxes:
                    b.frame_index = frame_index
                    b.timestamp = frame_ts
                    b.variant = var_name
                    b.scale = var_scale
                frame_results[primary_backend_key].extend(boxes)
            except Exception as exc:
                log.warning("Primary backend failed on variant %s: %s", var_name, exc)

            # Extra backends
            for bk, backend in extra_backends:
                try:
                    boxes = backend.ocr_image(var_path, {"lang": lang})
                    for b in boxes:
                        b.frame_index = frame_index
                        b.timestamp = frame_ts
                        b.variant = var_name
                        b.scale = var_scale
                    frame_results[bk].extend(boxes)
                except Exception as exc:
                    log.warning("Backend '%s' failed on variant %s: %s", bk, var_name, exc)

        # Merge frame results across backends
        merged_frame = merge_results(frame_results, mode=engine_mode)
        all_frame_boxes.append(merged_frame)
        all_boxes.extend(merged_frame)

    _progress(0.82, "Processing complete, deduplicating lines...")

    # ── Step 6: Line deduplication ───────────────────────────────────────
    if do_dedupe_lines:
        deduped = dedupe_lines(all_boxes, threshold=85.0)
    else:
        deduped = list(all_boxes)

    deduped_count = len(deduped)
    log.info("Lines after dedupe: %d (from %d)", deduped_count, len(all_boxes))

    # ── Step 7: Generate exports ─────────────────────────────────────────
    _progress(0.88, "Generating export files...")
    export_files = save_all_exports(
        result_dir=result_dir,
        ocr_boxes=all_boxes,
        all_frame_boxes=all_frame_boxes,
        video_info=video_info,
        job_options={
            "quality_mode": quality_mode,
            "video_strategy": video_strategy,
            "engine_mode": engine_mode,
            "backend": primary_backend_key,
            "secondary_backends": [bk for bk, _ in extra_backends],
            "fps": fps,
            "max_width": max_width,
            "scales": scales,
            "preprocess_variants": preprocess_variants,
            "dedupe_frames": do_dedupe_frames,
            "dedupe_lines": do_dedupe_lines,
            "lang": lang,
        },
        deduped_boxes=deduped,
    )

    # ── Step 8: Cleanup ──────────────────────────────────────────────────
    if not keep_debug:
        _progress(0.95, "Cleaning up temporary files...")
        frames_path = Path(result_dir) / "frames"
        if frames_path.exists():
            shutil.rmtree(str(frames_path), ignore_errors=True)
        variants_path = Path(result_dir) / "variants"
        if variants_path.exists():
            shutil.rmtree(str(variants_path), ignore_errors=True)

    elapsed = time.time() - start_time

    # Compute stats
    total_words = sum(len(_text(b).split()) for b in deduped)
    confidence_vals = [
        (b.confidence if isinstance(b, OCRBox) else b.get("confidence")) or 0
        for b in deduped
    ]
    avg_confidence = (
        sum(confidence_vals) / len(confidence_vals) * 100
        if confidence_vals else 0.0
    )

    # Read the main result text
    result_txt_path = Path(result_dir) / "result.txt"
    result_text = result_txt_path.read_text(encoding="utf-8") if result_txt_path.exists() else ""

    _progress(1.0, "Complete")

    return {
        "raw_frame_count": raw_frame_count,
        "frame_count": frame_count,
        "video_duration": video_info.get("duration", 0),
        "sample_fps": fps,
        "total_lines": len(all_boxes),
        "deduped_lines": deduped_count,
        "total_words": total_words,
        "avg_confidence": round(avg_confidence, 2),
        "elapsed": round(elapsed, 2),
        "backends_used": all_backend_keys,
        "result_text": result_text,
        "result_files": export_files,
    }


def _text(box) -> str:
    from ocr_backends.base import OCRBox
    return box.text if isinstance(box, OCRBox) else box.get("text", "")
