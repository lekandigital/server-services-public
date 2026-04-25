#!/usr/bin/env python3
"""PaddleOCR Web API Server — GPU-accelerated OCR service."""

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
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from PIL import Image
from pdf2image import convert_from_path
from werkzeug.utils import secure_filename

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

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "bmp", "tiff", "gif", "pdf"}
MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100 MB

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
# Database helpers
# ---------------------------------------------------------------------------
_db_lock = threading.Lock()


def get_db():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


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
# OCR Engine (lazy-loaded)
# ---------------------------------------------------------------------------
_ocr_instances = {}
_ocr_lock = threading.Lock()
_models_ready = False
_models_downloading = False
_active_jobs = 0
_active_jobs_lock = threading.Lock()


def get_ocr(lang="en", use_angle_cls=True, det_db_thresh=0.3):
    global _models_ready, _models_downloading
    from paddleocr import PaddleOCR

    key = (lang, use_angle_cls, det_db_thresh)
    with _ocr_lock:
        if key not in _ocr_instances:
            _models_downloading = True
            _ocr_instances[key] = PaddleOCR(
                use_angle_cls=use_angle_cls,
                lang=lang,
                det_db_thresh=det_db_thresh,
            )
            _models_downloading = False
            _models_ready = True
        return _ocr_instances[key]


def begin_model_use():
    global _active_jobs
    with _active_jobs_lock:
        _active_jobs += 1


def end_model_use():
    global _active_jobs
    with _active_jobs_lock:
        _active_jobs = max(0, _active_jobs - 1)
        return _active_jobs


def unload_ocr_instances():
    global _models_ready, _models_downloading
    with _ocr_lock:
        if not _ocr_instances:
            _models_ready = False
            _models_downloading = False
            return
        _ocr_instances.clear()
        _models_ready = False
        _models_downloading = False
    gc.collect()
    try:
        import paddle
        paddle.device.cuda.empty_cache()
    except Exception:
        pass


def check_gpu():
    try:
        import paddle
        return paddle.device.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0
    except Exception:
        return False


def get_paddle_version():
    try:
        import paddleocr
        return paddleocr.__version__  # type: ignore
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# OCR Processing
# ---------------------------------------------------------------------------
def parse_page_range(page_range_str, total_pages):
    """Parse page range like '1-3,5' into a list of 0-indexed page numbers."""
    if not page_range_str or page_range_str.strip().lower() in ("all", ""):
        return list(range(total_pages))
    pages = set()
    for part in page_range_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            start = max(1, int(start))
            end = min(total_pages, int(end))
            pages.update(range(start - 1, end))
        else:
            p = int(part) - 1
            if 0 <= p < total_pages:
                pages.add(p)
    return sorted(pages)


def boxes_to_layout_text(result):
    """Convert OCR result with bounding boxes into layout-preserved text."""
    if not result or not result[0]:
        return ""
    lines = []
    items = []
    for line in result[0]:
        box = line[0]
        text = line[1][0]
        confidence = line[1][1]
        y_center = (box[0][1] + box[2][1]) / 2
        x_left = box[0][0]
        items.append((y_center, x_left, text, confidence))

    if not items:
        return ""

    # Sort by y then x
    items.sort(key=lambda it: (it[0], it[1]))

    # Group into lines by y proximity
    line_groups = []
    current_group = [items[0]]
    for item in items[1:]:
        if abs(item[0] - current_group[-1][0]) < 15:
            current_group.append(item)
        else:
            line_groups.append(current_group)
            current_group = [item]
    line_groups.append(current_group)

    for group in line_groups:
        group.sort(key=lambda it: it[1])
        line_text = "   ".join(it[2] for it in group)
        lines.append(line_text)

    return "\n".join(lines)


def simple_text(result):
    """Extract plain text from OCR result."""
    if not result or not result[0]:
        return ""
    return "\n".join(line[1][0] for line in result[0])


def result_to_json(result, page_num=0):
    """Convert OCR result into structured JSON."""
    if not result or not result[0]:
        return []
    items = []
    for line in result[0]:
        box = line[0]
        text = line[1][0]
        conf = float(line[1][1])
        items.append({
            "page": page_num,
            "text": text,
            "confidence": round(conf, 4),
            "box": [[float(p[0]), float(p[1])] for p in box],
        })
    return items


def process_job(job_id):
    """Run OCR in a background thread."""
    job = get_job(job_id)
    if not job:
        return

    try:
        begin_model_use()
        update_job(job_id, status="processing", progress=0.05, progress_msg="Initializing OCR engine...")
        start_time = time.time()

        filepath = UPLOAD_DIR / job["filename"]
        lang = job.get("lang", "en")
        preserve_layout = bool(job.get("preserve_layout", 1))
        output_format = job.get("output_format", "txt")

        ocr = get_ocr(lang=lang, use_angle_cls=True)

        is_pdf = job["filetype"] == "pdf"
        all_text_parts = []
        all_json_items = []
        total_words = 0
        confidence_sum = 0
        confidence_count = 0

        if is_pdf:
            update_job(job_id, status="processing", progress=0.1, progress_msg="Converting PDF pages...")
            images = convert_from_path(str(filepath), dpi=300)
            total_pages = len(images)

            # Parse page range
            page_range_str = ""  # Could be stored in job in future
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

                result = ocr.ocr(str(img_path))

                if preserve_layout:
                    page_text = boxes_to_layout_text(result)
                else:
                    page_text = simple_text(result)

                all_text_parts.append(f"--- Page {page_num} ---\n{page_text}")
                page_json = result_to_json(result, page_num)
                all_json_items.extend(page_json)

                words = len(page_text.split())
                total_words += words
                for item in page_json:
                    confidence_sum += item["confidence"]
                    confidence_count += 1

                # Clean up temp page image
                try:
                    img_path.unlink()
                except OSError:
                    pass
        else:
            update_job(job_id, progress=0.2, progress_msg="Extracting text from image...", total_pages=1, current_page=1)
            result = ocr.ocr(str(filepath))

            if preserve_layout:
                text = boxes_to_layout_text(result)
            else:
                text = simple_text(result)

            all_text_parts.append(text)
            page_json = result_to_json(result, page_num=1)
            all_json_items.extend(page_json)

            total_words = len(text.split())
            for item in page_json:
                confidence_sum += item["confidence"]
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

        # CSV
        csv_buf = io.StringIO()
        writer = csv.writer(csv_buf)
        writer.writerow(["page", "text", "confidence", "box"])
        for item in all_json_items:
            writer.writerow([item["page"], item["text"], item["confidence"], json.dumps(item["box"])])
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
        if end_model_use() == 0:
            unload_ocr_instances()


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
    return jsonify({
        "status": "ok",
        "gpu": check_gpu(),
        "version": get_paddle_version(),
        "models_ready": _models_ready,
        "models_downloading": _models_downloading,
        "models_loaded": len(_ocr_instances),
        "active_jobs": count_active_jobs(),
        "supported_langs": SUPPORTED_LANGS,
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
        return jsonify({"error": f"Unsupported format: {ext}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"}), 400

    # Options
    lang = request.form.get("lang", "en")
    output_format = request.form.get("output_format", "txt")
    preserve_layout = request.form.get("preserve_layout", "true").lower() in ("true", "1", "yes")
    det_db_thresh = float(request.form.get("det_db_thresh", "0.3"))
    det_db_thresh = max(0.1, min(0.9, det_db_thresh))

    job_id = str(uuid.uuid4())
    safe_name = f"{job_id}_{secure_filename(f.filename)}"
    save_path = UPLOAD_DIR / safe_name
    f.save(str(save_path))

    filesize = save_path.stat().st_size

    with _db_lock:
        conn = get_db()
        conn.execute(
            """INSERT INTO jobs (id, filename, filetype, filesize, lang, status, output_format,
               preserve_layout, created_at) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)""",
            (job_id, safe_name, "pdf" if ext == "pdf" else "image", filesize,
             lang, output_format, int(preserve_layout), datetime.utcnow().isoformat()),
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

    return jsonify({"job_id": job_id, "status": "queued"}), 202


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
    })


@app.route("/result/<job_id>")
def job_result(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job["status"] != "completed":
        return jsonify({"error": "Job not completed", "status": job["status"]}), 400

    result_json = json.loads(job["result_json"]) if job["result_json"] else []
    return jsonify({
        "job_id": job["id"],
        "filename": job["filename"],
        "filetype": job["filetype"],
        "lang": job["lang"],
        "status": "completed",
        "text": job["result_text"],
        "boxes": result_json,
        "words_found": job["words_found"],
        "avg_confidence": job["avg_confidence"],
        "elapsed": job["elapsed"],
        "total_pages": job["total_pages"],
        "created_at": job["created_at"],
        "completed_at": job["completed_at"],
    })


@app.route("/history")
def history():
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 10))
    sort_by = request.args.get("sort", "created_at")
    order = request.args.get("order", "desc")

    allowed_sorts = {"created_at", "filename", "words_found", "avg_confidence", "filetype", "lang"}
    if sort_by not in allowed_sorts:
        sort_by = "created_at"
    order_sql = "DESC" if order == "desc" else "ASC"

    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    rows = conn.execute(
        f"SELECT id, filename, filetype, filesize, lang, status, words_found, avg_confidence, "
        f"total_pages, elapsed, created_at, completed_at FROM jobs ORDER BY {sort_by} {order_sql} "
        f"LIMIT ? OFFSET ?",
        (per_page, (page - 1) * per_page),
    ).fetchall()
    conn.close()

    jobs = []
    for r in rows:
        d = dict(r)
        # Extract original filename from safe_name
        fname = d["filename"]
        if "_" in fname:
            parts = fname.split("_", 1)
            if len(parts) > 1:
                d["display_name"] = parts[1]
            else:
                d["display_name"] = fname
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

    if fmt == "txt":
        path = result_dir / "result.txt"
        if path.exists():
            return send_file(str(path), mimetype="text/plain", as_attachment=True, download_name="ocr_result.txt")
    elif fmt == "json":
        path = result_dir / "result.json"
        if path.exists():
            return send_file(str(path), mimetype="application/json", as_attachment=True, download_name="ocr_result.json")
    elif fmt == "csv":
        path = result_dir / "result.csv"
        if path.exists():
            return send_file(str(path), mimetype="text/csv", as_attachment=True, download_name="ocr_result.csv")
    elif fmt == "searchable_pdf":
        return jsonify({"error": "Searchable PDF export not yet implemented"}), 501

    return jsonify({"error": f"Format '{fmt}' not available"}), 404


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

    # Clean up files
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

    print(f"PaddleOCR Server starting on port 8006...")
    print(f"GPU available: {check_gpu()}")
    print(f"PaddleOCR version: {get_paddle_version()}")
    app.run(host="0.0.0.0", port=8006, debug=False, threaded=True)
