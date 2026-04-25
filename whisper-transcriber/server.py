#!/usr/bin/env python3
"""Faster Whisper Web UI — Flask backend with GPU transcription."""

import argparse
import os
import sys
import uuid
import time
import json
import gc
import sqlite3
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory, send_file, abort
from flask_cors import CORS

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
DB_PATH = BASE_DIR / "jobs.db"

app = Flask(__name__, static_folder=str(BASE_DIR / "static"))
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024  # 4 GB

ALLOWED_EXTENSIONS = {"mp3", "mp4", "wav", "m4a", "ogg", "flac", "mov", "mkv", "webm"}
VIDEO_EXTENSIONS = {"mp4", "mov", "mkv", "webm"}

# Model cache (lazy loaded)
_models: dict = {}
_models_lock = threading.Lock()
_active_jobs = 0
_active_jobs_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            filename TEXT,
            original_filename TEXT,
            status TEXT DEFAULT 'pending',
            progress REAL DEFAULT 0,
            model_size TEXT DEFAULT 'large-v3',
            language TEXT DEFAULT 'auto',
            detected_language TEXT,
            language_confidence REAL,
            compute_type TEXT DEFAULT 'float16',
            word_timestamps INTEGER DEFAULT 1,
            output_format TEXT DEFAULT 'srt',
            beam_size INTEGER DEFAULT 5,
            duration REAL,
            transcription_time REAL,
            result_text TEXT,
            result_segments TEXT,
            error TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT
        )
    """)
    conn.commit()
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "status_text" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN status_text TEXT DEFAULT ''")
    if "cancel_requested" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER DEFAULT 0")
    conn.commit()
    conn.close()


def update_job(job_id, **kwargs):
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
        "SELECT COUNT(*) FROM jobs WHERE status IN ('pending', 'processing')"
    ).fetchone()[0]
    conn.close()
    return total


def cancel_requested(job_id):
    job = get_job(job_id)
    return bool(job and job.get("cancel_requested"))

# ---------------------------------------------------------------------------
# GPU detection
# ---------------------------------------------------------------------------

def detect_gpu():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return True, r.stdout.strip()
    except Exception:
        pass
    return False, None


GPU_AVAILABLE, GPU_NAME = detect_gpu()

# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def get_model(model_size, compute_type):
    key = f"{model_size}_{compute_type}"
    with _models_lock:
        if key not in _models:
            from faster_whisper import WhisperModel
            device = "cuda" if GPU_AVAILABLE else "cpu"
            ct = compute_type if GPU_AVAILABLE else "int8"
            _models[key] = WhisperModel(model_size, device=device, compute_type=ct)
        return _models[key]


def begin_model_use():
    global _active_jobs
    with _active_jobs_lock:
        _active_jobs += 1


def end_model_use():
    global _active_jobs
    with _active_jobs_lock:
        _active_jobs = max(0, _active_jobs - 1)
        return _active_jobs


def unload_models():
    with _models_lock:
        if not _models:
            return
        _models.clear()
    gc.collect()
    if GPU_AVAILABLE:
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Audio extraction from video
# ---------------------------------------------------------------------------

def extract_audio(input_path: str) -> str:
    """Extract audio from video file, returns path to wav."""
    audio_path = input_path + ".wav"
    subprocess.run([
        "ffmpeg", "-y", "-i", input_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        audio_path
    ], capture_output=True, check=True)
    return audio_path

# ---------------------------------------------------------------------------
# Transcription worker
# ---------------------------------------------------------------------------

def transcribe_worker(job_id):
    job = get_job(job_id)
    if not job:
        return

    upload_path = str(UPLOAD_DIR / job["filename"])
    audio_path = upload_path
    temp_audio = None

    try:
        begin_model_use()
        update_job(job_id, status="processing", progress=0, status_text="Preparing...", cancel_requested=0)

        # Extract audio from video if needed
        ext = Path(job["original_filename"]).suffix.lower().lstrip(".")
        if ext in VIDEO_EXTENSIONS:
            update_job(job_id, status_text="Extracting audio from video...")
            audio_path = extract_audio(upload_path)
            temp_audio = audio_path

        # Check cancel
        if cancel_requested(job_id):
            update_job(job_id, status="cancelled", status_text="Cancelled", completed_at=datetime.utcnow().isoformat())
            return

        # Load model
        update_job(job_id, progress=5, status_text="Loading model (may download on first use)...")

        model = get_model(job["model_size"], job["compute_type"])

        # Check cancel
        if cancel_requested(job_id):
            update_job(job_id, status="cancelled", status_text="Cancelled", completed_at=datetime.utcnow().isoformat())
            return

        # Transcribe
        update_job(job_id, progress=10, status_text="Transcribing...")

        lang = None if job["language"] == "auto" else job["language"]
        word_ts = bool(job["word_timestamps"])
        start_time = time.time()

        segments_iter, info = model.transcribe(
            audio_path,
            language=lang,
            beam_size=job["beam_size"],
            word_timestamps=word_ts,
            vad_filter=True,
        )

        detected_lang = info.language
        lang_prob = info.language_probability
        duration = info.duration

        update_job(job_id,
                   detected_language=detected_lang,
                   language_confidence=round(lang_prob, 3),
                   duration=round(duration, 2))
        update_job(job_id, status_text=f"Detected language: {detected_lang} ({lang_prob:.0%})")

        # Collect segments
        all_segments = []
        full_text_parts = []
        seg_count = 0

        for segment in segments_iter:
            # Check cancel
            if cancel_requested(job_id):
                update_job(job_id, status="cancelled", status_text="Cancelled", completed_at=datetime.utcnow().isoformat())
                return

            seg_count += 1
            seg_data = {
                "id": seg_count,
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "text": segment.text.strip(),
            }
            if word_ts and segment.words:
                seg_data["words"] = [
                    {"word": w.word, "start": round(w.start, 3),
                     "end": round(w.end, 3), "probability": round(w.probability, 3)}
                    for w in segment.words
                ]
            all_segments.append(seg_data)
            full_text_parts.append(segment.text.strip())

            # Update progress (10-95% range for transcription)
            if duration > 0:
                pct = min(95, 10 + (segment.end / duration) * 85)
            else:
                pct = min(95, 10 + seg_count * 2)

            update_job(job_id, progress=round(pct, 1), status_text=f"Transcribing segment {seg_count}...")

        elapsed = time.time() - start_time
        full_text = "\n".join(full_text_parts)

        update_job(job_id,
                   status="completed",
                   progress=100,
                   result_text=full_text,
                   result_segments=json.dumps(all_segments),
                   transcription_time=round(elapsed, 2),
                   status_text="Complete",
                   cancel_requested=0,
                   completed_at=datetime.utcnow().isoformat())

    except Exception as e:
        update_job(
            job_id,
            status="error",
            error=str(e),
            status_text=f"Error: {e}",
            completed_at=datetime.utcnow().isoformat(),
        )
    finally:
        # Clean up temp audio
        if temp_audio and os.path.exists(temp_audio):
            os.remove(temp_audio)
        if end_model_use() == 0:
            unload_models()

# ---------------------------------------------------------------------------
# Format converters
# ---------------------------------------------------------------------------

def format_timestamp_srt(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_timestamp_vtt(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def segments_to_srt(segments):
    lines = []
    for seg in segments:
        lines.append(str(seg["id"]))
        lines.append(f"{format_timestamp_srt(seg['start'])} --> {format_timestamp_srt(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")
    return "\n".join(lines)


def segments_to_vtt(segments):
    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{format_timestamp_vtt(seg['start'])} --> {format_timestamp_vtt(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "gpu": GPU_AVAILABLE,
        "gpu_name": GPU_NAME,
        "model": "large-v3",
        "models_loaded": len(_models),
        "active_jobs": count_active_jobs(),
    })


def launch_worker(job_id):
    subprocess.Popen(
        [sys.executable, str(BASE_DIR / "server.py"), "--job-id", job_id],
        cwd=str(BASE_DIR),
        start_new_session=True,
    )


@app.route("/transcribe", methods=["POST"])
def transcribe():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No file selected"}), 400

    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"Unsupported format: .{ext}"}), 400

    job_id = str(uuid.uuid4())
    safe_name = f"{job_id}.{ext}"
    save_path = UPLOAD_DIR / safe_name
    f.save(str(save_path))

    # Parse options
    model_size = request.form.get("model_size", "large-v3")
    language = request.form.get("language", "auto")
    compute_type = request.form.get("compute_type", "float16")
    word_timestamps = request.form.get("word_timestamps", "true").lower() == "true"
    output_format = request.form.get("output_format", "srt")
    beam_size = int(request.form.get("beam_size", "5"))
    beam_size = max(1, min(10, beam_size))

    if model_size not in ("tiny", "base", "small", "medium", "large-v3"):
        model_size = "large-v3"
    if compute_type not in ("float16", "int8"):
        compute_type = "float16"
    if output_format not in ("txt", "srt", "vtt", "json"):
        output_format = "srt"

    # Create DB record
    conn = get_db()
    conn.execute("""
        INSERT INTO jobs (id, filename, original_filename, model_size, language,
                          compute_type, word_timestamps, output_format, beam_size)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (job_id, safe_name, f.filename, model_size, language,
          compute_type, int(word_timestamps), output_format, beam_size))
    conn.commit()
    conn.close()

    try:
        launch_worker(job_id)
    except Exception as e:
        update_job(job_id, status="error", error=str(e), status_text=f"Worker launch failed: {e}")
        return jsonify({"error": "Failed to launch transcription worker"}), 500

    return jsonify({"job_id": job_id}), 202


@app.route("/status/<job_id>")
def status(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    return jsonify({
        "job_id": job_id,
        "status": job["status"],
        "progress": job["progress"],
        "status_text": job.get("status_text", ""),
        "detected_language": job["detected_language"],
        "language_confidence": job["language_confidence"],
        "duration": job["duration"],
        "error": job["error"],
    })


@app.route("/cancel/<job_id>", methods=["POST"])
def cancel(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    update_job(job_id, cancel_requested=1, status_text="Cancelling...")
    return jsonify({"ok": True})


@app.route("/result/<job_id>")
def result(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job["status"] != "completed":
        return jsonify({"error": "Job not completed", "status": job["status"]}), 400

    segments = json.loads(job["result_segments"]) if job["result_segments"] else []

    return jsonify({
        "job_id": job_id,
        "filename": job["original_filename"],
        "status": "completed",
        "detected_language": job["detected_language"],
        "language_confidence": job["language_confidence"],
        "duration": job["duration"],
        "transcription_time": job["transcription_time"],
        "model_size": job["model_size"],
        "text": job["result_text"],
        "segments": segments,
        "word_timestamps": bool(job["word_timestamps"]),
    })


@app.route("/download/<job_id>/<fmt>")
def download(job_id, fmt):
    if fmt not in ("txt", "srt", "vtt", "json"):
        return jsonify({"error": "Invalid format"}), 400

    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job["status"] != "completed":
        return jsonify({"error": "Job not completed"}), 400

    segments = json.loads(job["result_segments"]) if job["result_segments"] else []
    basename = Path(job["original_filename"]).stem

    if fmt == "txt":
        content = job["result_text"] or ""
        mimetype = "text/plain"
    elif fmt == "srt":
        content = segments_to_srt(segments)
        mimetype = "text/plain"
    elif fmt == "vtt":
        content = segments_to_vtt(segments)
        mimetype = "text/vtt"
    elif fmt == "json":
        content = json.dumps({
            "filename": job["original_filename"],
            "language": job["detected_language"],
            "duration": job["duration"],
            "segments": segments
        }, indent=2)
        mimetype = "application/json"

    import io
    buf = io.BytesIO(content.encode("utf-8"))
    return send_file(buf, mimetype=mimetype,
                     as_attachment=True,
                     download_name=f"{basename}.{fmt}")


@app.route("/history")
def history():
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 10))
    offset = (page - 1) * per_page

    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    rows = conn.execute(
        "SELECT id, original_filename, status, model_size, detected_language, "
        "duration, created_at, progress, error FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (per_page, offset)
    ).fetchall()
    conn.close()

    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "jobs": [dict(r) for r in rows],
    })


@app.route("/delete/<job_id>", methods=["DELETE"])
def delete_job(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    # Remove uploaded file
    fpath = UPLOAD_DIR / job["filename"]
    if fpath.exists():
        fpath.unlink()

    conn = get_db()
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", help="Run a single transcription job and exit")
    args = parser.parse_args()

    init_db()
    if args.job_id:
        transcribe_worker(args.job_id)
        sys.exit(0)

    print(f"GPU: {GPU_AVAILABLE} ({GPU_NAME})")
    print(f"Serving on http://0.0.0.0:8005")
    app.run(host="0.0.0.0", port=8005, debug=False)
