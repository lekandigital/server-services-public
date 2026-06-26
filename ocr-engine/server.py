#!/usr/bin/env python3
"""OCR Engine — Multi-backend GPU-accelerated OCR service.

Supports images, PDFs, and video files with selectable OCR backends,
quality modes, video strategies, and ensemble engine modes.
"""

import argparse
import os
import sys
import json
import uuid
import time
import gc
import sqlite3
import subprocess
import threading
import traceback
import csv
import io
import re
import logging
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from PIL import Image
from pdf2image import convert_from_path
from werkzeug.utils import secure_filename

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("ocr-engine")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.resolve()
UPLOAD_DIR = BASE_DIR / "uploads"
RESULT_DIR = BASE_DIR / "results"
STATIC_DIR = BASE_DIR / "static"
DB_PATH = BASE_DIR / "jobs.db"

UPLOAD_DIR.mkdir(exist_ok=True)
RESULT_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

# Image + PDF + Video extensions
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "bmp", "tiff", "gif"}
PDF_EXTENSIONS = {"pdf"}
VIDEO_EXTENSIONS = {
    "mov", "mp4", "m4v", "mkv", "webm", "avi",
    "mpeg", "mpg", "wmv", "flv", "ts", "m2ts",
}
ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS | VIDEO_EXTENSIONS

MAX_CONTENT_LENGTH = 2 * 1024 * 1024 * 1024  # 2 GB for video support

SUPPORTED_LANGS = {
    "en": "English", "ch": "Chinese (Simplified)", "chinese_cht": "Chinese (Traditional)",
    "fr": "French", "german": "German", "ja": "Japanese", "ko": "Korean",
    "ar": "Arabic", "hi": "Hindi", "ta": "Tamil", "te": "Telugu",
    "ka": "Kannada", "mr": "Marathi", "ne": "Nepali", "rs_latin": "Serbian (Latin)",
    "rs_cyrillic": "Serbian (Cyrillic)", "oc": "Occitan", "rsc": "Russian",
    "bg": "Bulgarian", "uk": "Ukrainian", "be": "Belarusian",
    "es": "Spanish", "pt": "Portuguese", "it": "Italian", "nl": "Dutch",
    "no": "Norwegian", "da": "Danish", "fi": "Finnish", "sv": "Swedish",
    "hu": "Hungarian", "pl": "Polish", "ro": "Romanian", "cs": "Czech",
    "sk": "Slovak", "sl": "Slovenian", "hr": "Croatian", "et": "Estonian",
    "lv": "Latvian", "lt": "Lithuanian", "tr": "Turkish", "vi": "Vietnamese",
    "th": "Thai", "id": "Indonesian", "ms": "Malay", "tl": "Tagalog",
    "latin": "Latin", "cyrillic": "Cyrillic", "devanagari": "Devanagari",
}

# ---------------------------------------------------------------------------
# Import backend registry
# ---------------------------------------------------------------------------
sys.path.insert(0, str(BASE_DIR))
from ocr_backends import (
    OCR_BACKENDS,
    VIDEO_OCR_QUALITY_MODES,
    VIDEO_OCR_STRATEGIES,
    OCR_ENGINE_MODES,
    PREPROCESS_VARIANTS,
    get_backend,
    check_all_backends,
)
from ocr_backends.base import OCRBox

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
_db_lock = threading.Lock()


def get_db():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_column(conn, table: str, column: str, col_type: str, default=None):
    """Safely add a column to an existing table if it doesn't exist."""
    try:
        cursor = conn.execute(f"PRAGMA table_info({table})")
        existing_cols = {row[1] for row in cursor.fetchall()}
        if column not in existing_cols:
            default_clause = f" DEFAULT {default!r}" if default is not None else ""
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}{default_clause}")
            log.info("Migrated: added column %s.%s", table, column)
    except Exception as exc:
        log.warning("Column migration failed for %s.%s: %s", table, column, exc)


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            filename TEXT,
            filetype TEXT,
            filesize INTEGER,
            lang TEXT DEFAULT 'en',
            status TEXT DEFAULT 'queued',
            progress REAL DEFAULT 0,
            progress_msg TEXT DEFAULT '',
            total_pages INTEGER DEFAULT 1,
            current_page INTEGER DEFAULT 0,
            words_found INTEGER DEFAULT 0,
            avg_confidence REAL DEFAULT 0,
            elapsed REAL DEFAULT 0,
            output_format TEXT DEFAULT 'txt',
            preserve_layout INTEGER DEFAULT 1,
            result_text TEXT,
            result_json TEXT,
            error_msg TEXT,
            created_at TEXT,
            completed_at TEXT
        )
    """)

    # Migrate: add new columns for video/multi-backend support
    migrations = [
        ("original_filename", "TEXT", ""),
        ("options_json", "TEXT", "{}"),
        ("backend", "TEXT", "paddle"),
        ("engine_mode", "TEXT", "single"),
        ("quality_mode", "TEXT", "standard"),
        ("video_strategy", "TEXT", ""),
        ("frame_count", "INTEGER", 0),
        ("raw_frame_count", "INTEGER", 0),
        ("video_duration", "REAL", 0),
        ("sample_fps", "REAL", 0),
        ("deduped_lines", "INTEGER", 0),
        ("result_files_json", "TEXT", "{}"),
    ]
    for col_name, col_type, default in migrations:
        ensure_column(conn, "jobs", col_name, col_type, default)

    conn.commit()
    conn.close()


def update_job(job_id, **kwargs):
    with _db_lock:
        conn = get_db()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [job_id]
        conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", vals)
        conn.commit()
        conn.close()


def get_job(job_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def count_active_jobs():
    conn = get_db()
    total = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'processing')"
    ).fetchone()[0]
    conn.close()
    return total


# ---------------------------------------------------------------------------
# GPU detection
# ---------------------------------------------------------------------------
def check_gpu():
    try:
        import paddle
        return paddle.device.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0
    except Exception:
        return False


def get_paddle_version():
    try:
        import paddleocr
        return paddleocr.__version__
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Active job tracking (for model unloading)
# ---------------------------------------------------------------------------
_active_jobs = 0
_active_jobs_lock = threading.Lock()


def begin_model_use():
    global _active_jobs
    with _active_jobs_lock:
        _active_jobs += 1


def end_model_use():
    global _active_jobs
    with _active_jobs_lock:
        _active_jobs = max(0, _active_jobs - 1)
        return _active_jobs


# ---------------------------------------------------------------------------
# File type detection
# ---------------------------------------------------------------------------
def detect_filetype(extension: str) -> str:
    """Return 'image', 'pdf', or 'video' based on file extension."""
    ext = extension.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in PDF_EXTENSIONS:
        return "pdf"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return "unknown"


# ---------------------------------------------------------------------------
# Image/PDF OCR processing (refactored to use backend adapters)
# ---------------------------------------------------------------------------
def process_image_pdf_job(job_id):
    """Run OCR on an image or PDF using the selected backend."""
    job = get_job(job_id)
    if not job:
        return

    try:
        begin_model_use()
        update_job(job_id, status="processing", progress=0.05,
                   progress_msg="Initializing OCR engine...")
        start_time = time.time()

        filepath = UPLOAD_DIR / job["filename"]
        lang = job.get("lang", "en")
        preserve_layout = bool(job.get("preserve_layout", 1))

        # Determine backend
        backend_key = job.get("backend", "paddle") or "paddle"
        try:
            backend = get_backend(backend_key)
            avail, err = backend.is_available()
            if not avail:
                raise RuntimeError(err)
        except Exception:
            log.warning("Backend '%s' unavailable, falling back to paddle", backend_key)
            backend_key = "paddle"
            backend = get_backend("paddle")

        backend.load({"lang": lang})

        is_pdf = job["filetype"] == "pdf"
        all_text_parts = []
        all_json_items = []
        total_words = 0
        confidence_sum = 0.0
        confidence_count = 0

        if is_pdf:
            update_job(job_id, status="processing", progress=0.1,
                       progress_msg="Converting PDF pages...")
            images = convert_from_path(str(filepath), dpi=300)
            total_pages = len(images)
            pages_to_process = list(range(total_pages))
            update_job(job_id, total_pages=total_pages)

            for idx, page_idx in enumerate(pages_to_process):
                page_num = page_idx + 1
                progress = 0.1 + 0.85 * (idx / len(pages_to_process))
                update_job(
                    job_id,
                    progress=progress,
                    current_page=page_num,
                    progress_msg=f"Processing page {page_num}/{total_pages}...",
                )

                img = images[page_idx]
                img_path = UPLOAD_DIR / f"{job_id}_page_{page_num}.png"
                img.save(str(img_path), "PNG")

                ocr_boxes = backend.ocr_image(str(img_path), {"lang": lang})

                if preserve_layout and hasattr(backend, "boxes_to_layout_text"):
                    page_text = backend.boxes_to_layout_text(ocr_boxes)
                else:
                    page_text = "\n".join(b.text for b in ocr_boxes if b.text)

                all_text_parts.append(f"--- Page {page_num} ---\n{page_text}")

                for b in ocr_boxes:
                    item = {
                        "page": page_num,
                        "text": b.text,
                        "confidence": round(b.confidence, 4) if b.confidence else 0.0,
                        "box": b.box or [],
                    }
                    all_json_items.append(item)
                    total_words += len(b.text.split())
                    if b.confidence:
                        confidence_sum += b.confidence
                        confidence_count += 1

                try:
                    img_path.unlink()
                except OSError:
                    pass
        else:
            update_job(job_id, progress=0.2,
                       progress_msg="Extracting text from image...",
                       total_pages=1, current_page=1)

            ocr_boxes = backend.ocr_image(str(filepath), {"lang": lang})

            if preserve_layout and hasattr(backend, "boxes_to_layout_text"):
                text = backend.boxes_to_layout_text(ocr_boxes)
            else:
                text = "\n".join(b.text for b in ocr_boxes if b.text)

            all_text_parts.append(text)

            for b in ocr_boxes:
                item = {
                    "page": 1,
                    "text": b.text,
                    "confidence": round(b.confidence, 4) if b.confidence else 0.0,
                    "box": b.box or [],
                }
                all_json_items.append(item)
                total_words += len(b.text.split())
                if b.confidence:
                    confidence_sum += b.confidence
                    confidence_count += 1

        elapsed = time.time() - start_time
        avg_conf = (confidence_sum / confidence_count * 100) if confidence_count > 0 else 0
        combined_text = "\n\n".join(all_text_parts)

        # Save results
        result_dir = RESULT_DIR / job_id
        result_dir.mkdir(exist_ok=True)

        (result_dir / "result.txt").write_text(combined_text, encoding="utf-8")
        (result_dir / "result.json").write_text(
            json.dumps(all_json_items, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        csv_buf = io.StringIO()
        writer = csv.writer(csv_buf)
        writer.writerow(["page", "text", "confidence", "box"])
        for item in all_json_items:
            writer.writerow([item["page"], item["text"], item["confidence"],
                             json.dumps(item["box"])])
        (result_dir / "result.csv").write_text(csv_buf.getvalue(), encoding="utf-8")

        update_job(
            job_id,
            status="completed",
            progress=1.0,
            progress_msg="Complete",
            words_found=total_words,
            avg_confidence=round(avg_conf, 2),
            elapsed=round(elapsed, 2),
            result_text=combined_text,
            result_json=json.dumps(all_json_items, ensure_ascii=False),
            completed_at=datetime.utcnow().isoformat(),
            backend=backend_key,
        )

    except Exception as e:
        update_job(
            job_id,
            status="error",
            progress_msg=f"Error: {str(e)}",
            error_msg=traceback.format_exc(),
            elapsed=round(time.time() - start_time, 2) if "start_time" in dir() else 0,
        )
    finally:
        remaining = end_model_use()
        if remaining == 0:
            try:
                backend = get_backend(backend_key)
                backend.unload()
            except Exception:
                pass
            gc.collect()


# ---------------------------------------------------------------------------
# Video OCR processing
# ---------------------------------------------------------------------------
def process_video_job(job_id):
    """Run video OCR pipeline using the selected backend(s) and options."""
    job = get_job(job_id)
    if not job:
        return

    start_time = time.time()
    try:
        begin_model_use()
        update_job(job_id, status="processing", progress=0.02,
                   progress_msg="Starting video OCR pipeline...")

        filepath = UPLOAD_DIR / job["filename"]

        # Parse options from job
        options_str = job.get("options_json", "{}")
        try:
            options = json.loads(options_str) if options_str else {}
        except json.JSONDecodeError:
            options = {}

        options["lang"] = job.get("lang", "en")

        # Progress callback
        def progress_cb(pct, msg):
            update_job(job_id, progress=pct, progress_msg=msg)

        # Run the pipeline
        from video_ocr.pipeline import run_video_pipeline

        result_dir = str(RESULT_DIR / job_id)
        result = run_video_pipeline(
            job_id=job_id,
            video_path=str(filepath),
            options=options,
            result_dir=result_dir,
            progress_callback=progress_cb,
        )

        elapsed = time.time() - start_time

        # Also save combined result.json for backward compat
        result_text = result.get("result_text", "")
        result_files = result.get("result_files", {})

        # Build a backward-compatible JSON result
        json_result_path = Path(result_dir) / "result.json"
        if json_result_path.exists():
            result_json_str = json_result_path.read_text(encoding="utf-8")
        else:
            result_json_str = "[]"

        update_job(
            job_id,
            status="completed",
            progress=1.0,
            progress_msg="Complete",
            words_found=result.get("total_words", 0),
            avg_confidence=result.get("avg_confidence", 0),
            elapsed=round(elapsed, 2),
            result_text=result_text,
            result_json=result_json_str,
            completed_at=datetime.utcnow().isoformat(),
            backend=options.get("backend", "paddle"),
            engine_mode=options.get("engine_mode", "single"),
            quality_mode=options.get("quality_mode", "standard"),
            video_strategy=options.get("video_strategy", ""),
            frame_count=result.get("frame_count", 0),
            raw_frame_count=result.get("raw_frame_count", 0),
            video_duration=result.get("video_duration", 0),
            sample_fps=result.get("sample_fps", 0),
            deduped_lines=result.get("deduped_lines", 0),
            result_files_json=json.dumps(result_files),
        )

    except Exception as e:
        update_job(
            job_id,
            status="error",
            progress_msg=f"Error: {str(e)}",
            error_msg=traceback.format_exc(),
            elapsed=round(time.time() - start_time, 2),
        )
    finally:
        remaining = end_model_use()
        if remaining == 0:
            gc.collect()


# ---------------------------------------------------------------------------
# Job router
# ---------------------------------------------------------------------------
def process_job(job_id):
    """Route job to the appropriate processor based on file type."""
    job = get_job(job_id)
    if not job:
        return

    filetype = job.get("filetype", "image")
    if filetype == "video":
        process_video_job(job_id)
    else:
        process_image_pdf_job(job_id)


# ---------------------------------------------------------------------------
# Flask App
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder=str(STATIC_DIR))
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
CORS(app)


@app.route("/")
def index():
    return send_from_directory(str(STATIC_DIR), "index.html")


@app.route("/health")
def health():
    # Check backend availability
    backend_status = {}
    for key in OCR_BACKENDS:
        try:
            b = get_backend(key)
            avail, err = b.is_available()
            backend_status[key] = {"available": avail, "error": err}
        except Exception as exc:
            backend_status[key] = {"available": False, "error": str(exc)}

    return jsonify({
        "status": "ok",
        "gpu": check_gpu(),
        "version": get_paddle_version(),
        "models_loaded": 0,
        "active_jobs": count_active_jobs(),
        "supported_langs": SUPPORTED_LANGS,
        "backends": backend_status,
    })


@app.route("/backends")
def backends():
    """Return full backend registry with live availability checks."""
    results = check_all_backends()
    return jsonify({
        "backends": results,
        "quality_modes": VIDEO_OCR_QUALITY_MODES,
        "video_strategies": VIDEO_OCR_STRATEGIES,
        "engine_modes": OCR_ENGINE_MODES,
        "preprocess_variants": PREPROCESS_VARIANTS,
    })


def launch_worker(job_id):
    subprocess.Popen(
        [sys.executable, str(BASE_DIR / "server.py"), "--job-id", job_id],
        cwd=str(BASE_DIR),
        start_new_session=True,
    )


@app.route("/ocr", methods=["POST"])
def ocr_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({
            "error": f"Unsupported format: {ext}. Allowed: images, PDFs, and videos."
        }), 400

    filetype = detect_filetype(ext)

    # Parse all options from form data
    lang = request.form.get("lang", "en")
    output_format = request.form.get("output_format", "txt")
    preserve_layout = request.form.get("preserve_layout", "true").lower() in ("true", "1", "yes")
    backend_key = request.form.get("backend", "paddle")
    engine_mode = request.form.get("engine_mode", "single")
    quality_mode = request.form.get("quality_mode", "standard")
    video_strategy = request.form.get("video_strategy", "scrolling-page")

    # Validate backend
    if backend_key not in OCR_BACKENDS:
        backend_key = "paddle"

    # Build options dict for video jobs
    options = {
        "backend": backend_key,
        "engine_mode": engine_mode,
        "quality_mode": quality_mode,
        "video_strategy": video_strategy,
        "lang": lang,
        "preserve_layout": preserve_layout,
    }

    # Video-specific options
    if filetype == "video":
        for key in [
            "video_fps", "max_width", "max_frames",
            "dedupe_frames", "dedupe_lines", "keep_debug_frames",
            "preprocess_variants", "scales", "fallback_backends",
            "secondary_backends",
        ]:
            val = request.form.get(key)
            if val is not None:
                options[key] = val

    # Save file
    job_id = str(uuid.uuid4())
    original_filename = f.filename
    safe_name = f"{job_id}_{secure_filename(f.filename)}"
    save_path = UPLOAD_DIR / safe_name
    f.save(str(save_path))
    filesize = save_path.stat().st_size

    with _db_lock:
        conn = get_db()
        conn.execute(
            """INSERT INTO jobs (id, filename, filetype, filesize, lang, status,
               output_format, preserve_layout, created_at, original_filename,
               options_json, backend, engine_mode, quality_mode, video_strategy)
               VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, safe_name, filetype, filesize, lang,
             output_format, int(preserve_layout), datetime.utcnow().isoformat(),
             original_filename, json.dumps(options), backend_key,
             engine_mode, quality_mode,
             video_strategy if filetype == "video" else ""),
        )
        conn.commit()
        conn.close()

    try:
        launch_worker(job_id)
    except Exception as e:
        update_job(
            job_id,
            status="error",
            progress_msg=f"Worker launch failed: {str(e)}",
            error_msg=traceback.format_exc(),
        )
        return jsonify({"error": "Failed to launch OCR worker"}), 500

    return jsonify({"job_id": job_id, "status": "queued", "filetype": filetype}), 202


@app.route("/status/<job_id>")
def job_status(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "job_id": job["id"],
        "status": job["status"],
        "progress": job["progress"],
        "progress_msg": job["progress_msg"],
        "total_pages": job["total_pages"],
        "current_page": job["current_page"],
        "elapsed": job["elapsed"],
        "filetype": job.get("filetype", "image"),
    })


@app.route("/result/<job_id>")
def job_result(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job["status"] != "completed":
        return jsonify({"error": "Job not completed", "status": job["status"]}), 400

    result_json = json.loads(job["result_json"]) if job["result_json"] else []

    # Parse result_files_json
    try:
        result_files = json.loads(job.get("result_files_json", "{}") or "{}")
    except (json.JSONDecodeError, TypeError):
        result_files = {}

    response = {
        "job_id": job["id"],
        "filename": job["filename"],
        "filetype": job.get("filetype", "image"),
        "lang": job["lang"],
        "status": "completed",
        "text": job["result_text"],
        "boxes": result_json if isinstance(result_json, list) else [],
        "words_found": job["words_found"],
        "avg_confidence": job["avg_confidence"],
        "elapsed": job["elapsed"],
        "total_pages": job["total_pages"],
        "created_at": job["created_at"],
        "completed_at": job["completed_at"],
        # Video-specific fields
        "backend": job.get("backend", "paddle"),
        "engine_mode": job.get("engine_mode", "single"),
        "quality_mode": job.get("quality_mode", "standard"),
        "video_strategy": job.get("video_strategy", ""),
        "frame_count": job.get("frame_count", 0),
        "raw_frame_count": job.get("raw_frame_count", 0),
        "video_duration": job.get("video_duration", 0),
        "sample_fps": job.get("sample_fps", 0),
        "deduped_lines": job.get("deduped_lines", 0),
        "available_exports": list(result_files.keys()) if result_files else ["txt", "json", "csv"],
    }

    return jsonify(response)


@app.route("/history")
def history():
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 10))
    sort_by = request.args.get("sort", "created_at")
    order = request.args.get("order", "desc")

    allowed_sorts = {
        "created_at", "filename", "words_found", "avg_confidence",
        "filetype", "lang", "backend",
    }
    if sort_by not in allowed_sorts:
        sort_by = "created_at"
    order_sql = "DESC" if order == "desc" else "ASC"

    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    rows = conn.execute(
        f"SELECT id, filename, filetype, filesize, lang, status, words_found, avg_confidence, "
        f"total_pages, elapsed, created_at, completed_at, backend, quality_mode, "
        f"frame_count, video_duration "
        f"FROM jobs ORDER BY {sort_by} {order_sql} "
        f"LIMIT ? OFFSET ?",
        (per_page, (page - 1) * per_page),
    ).fetchall()
    conn.close()

    jobs = []
    for r in rows:
        d = dict(r)
        fname = d["filename"]
        if "_" in fname:
            parts = fname.split("_", 1)
            d["display_name"] = parts[1] if len(parts) > 1 else fname
        else:
            d["display_name"] = fname
        jobs.append(d)

    return jsonify({
        "jobs": jobs,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
    })


@app.route("/download/<job_id>/<fmt>")
def download(job_id, fmt):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job["status"] != "completed":
        return jsonify({"error": "Job not completed"}), 400

    result_dir = RESULT_DIR / job_id

    # Map format keys to filenames and MIME types
    format_map = {
        "txt": ("result.txt", "text/plain", "ocr_result.txt"),
        "deduped_txt": ("result_deduped.txt", "text/plain", "ocr_result_deduped.txt"),
        "by_frame_txt": ("result_by_frame.txt", "text/plain", "ocr_result_by_frame.txt"),
        "timestamps_txt": ("result_with_timestamps.txt", "text/plain", "ocr_result_timestamps.txt"),
        "json": ("result.json", "application/json", "ocr_result.json"),
        "csv": ("result.csv", "text/csv", "ocr_result.csv"),
        "srt": ("result.srt", "text/plain", "ocr_result.srt"),
        "vtt": ("result.vtt", "text/vtt", "ocr_result.vtt"),
        "md": ("result.md", "text/markdown", "ocr_result.md"),
        "debug_json": ("debug_report.json", "application/json", "ocr_debug_report.json"),
        "searchable_pdf": (None, None, None),
    }

    if fmt not in format_map:
        return jsonify({"error": f"Unknown format: {fmt}"}), 400

    filename, mimetype, download_name = format_map[fmt]

    if filename is None:
        return jsonify({"error": "Searchable PDF export not yet implemented"}), 501

    path = result_dir / filename
    if path.exists():
        return send_file(str(path), mimetype=mimetype, as_attachment=True,
                         download_name=download_name)

    return jsonify({"error": f"Format '{fmt}' not available for this job"}), 404


@app.route("/delete/<job_id>", methods=["DELETE"])
def delete_job(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    with _db_lock:
        conn = get_db()
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()

    import shutil
    result_dir = RESULT_DIR / job_id
    if result_dir.exists():
        shutil.rmtree(result_dir)
    filepath = UPLOAD_DIR / job["filename"]
    if filepath.exists():
        filepath.unlink()

    return jsonify({"status": "deleted"})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", help="Run a single OCR job and exit")
    args = parser.parse_args()

    init_db()
    if args.job_id:
        process_job(args.job_id)
        sys.exit(0)

    print(f"🔍 OCR Engine starting on port 8006...")
    print(f"   GPU available: {check_gpu()}")
    print(f"   PaddleOCR version: {get_paddle_version()}")
    print(f"   Supported file types: images, PDFs, videos")
    print(f"   Registered backends: {', '.join(OCR_BACKENDS.keys())}")
    app.run(host="0.0.0.0", port=8006, debug=False, threaded=True)
