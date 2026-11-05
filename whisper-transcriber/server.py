#!/usr/bin/env python3
"""Faster Whisper Web UI — Flask backend with GPU transcription."""

import argparse
import os
import re
import sys
import uuid
import time
import json
import gc
import io
import fcntl
import sqlite3
import subprocess
import threading
import zipfile
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
WORKER_LOCK = BASE_DIR / "worker.lock"   # held by the one process working through the queue

app = Flask(__name__, static_folder=str(BASE_DIR / "static"))
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024  # 4 GB

ALLOWED_EXTENSIONS = {"mp3", "mp4", "wav", "m4a", "ogg", "flac", "mov", "mkv", "webm"}
VIDEO_EXTENSIONS = {"mp4", "mov", "mkv", "webm"}
OUTPUT_FORMATS = ("txt", "srt", "vtt", "json")
BATCH_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# Model cache (lazy loaded)
_models: dict = {}
_models_lock = threading.Lock()
_gpu_claimed = False

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
    if "batch_id" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN batch_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_batch ON jobs(batch_id)")
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


def cancel_jobs(column, value):
    """Cancel queued jobs outright and ask the worker to stop the one it is running.
    Both updates commit together, so the worker can't pick a job up in between."""
    conn = get_db()
    conn.execute(
        f"UPDATE jobs SET status = 'cancelled', status_text = 'Cancelled', completed_at = ? "
        f"WHERE {column} = ? AND status = 'pending'",
        (datetime.utcnow().isoformat(), value))
    conn.execute(
        f"UPDATE jobs SET cancel_requested = 1, status_text = 'Cancelling...' "
        f"WHERE {column} = ? AND status = 'processing'",
        (value,))
    conn.commit()
    conn.close()


def next_pending_job():
    conn = get_db()
    row = conn.execute("SELECT id FROM jobs WHERE status = 'pending' ORDER BY rowid LIMIT 1").fetchone()
    conn.close()
    return row["id"] if row else None


def claim_job(job_id):
    """Move a queued job to processing; False if it was cancelled or deleted meanwhile."""
    conn = get_db()
    cur = conn.execute(
        "UPDATE jobs SET status = 'processing', progress = 0, status_text = 'Preparing...', cancel_requested = 0 "
        "WHERE id = ? AND status = 'pending'",
        (job_id,))
    claimed = cur.rowcount == 1
    conn.commit()
    conn.close()
    return claimed


def fail_orphaned_jobs():
    """Whoever holds the queue lock is the only worker, so any other 'processing' job lost its
    worker (service restart, reboot, crash). Failing it rather than requeueing keeps a file that
    crashes the worker from blocking the rest of the queue."""
    conn = get_db()
    conn.execute(
        "UPDATE jobs SET status = 'error', status_text = 'Interrupted', completed_at = ?, "
        "error = 'Interrupted: the worker stopped before finishing (service restart or crash). Upload it again to retry.' "
        "WHERE status = 'processing'",
        (datetime.utcnow().isoformat(),))
    conn.commit()
    conn.close()

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
GPU_CLAIM = "/home/REDACTED_USER/.local/bin/gpu-claim"      # model-lab GPU gate (see ~/model-lab-labeler/gpu_gate.py)
GPU_RELEASE = "/home/REDACTED_USER/.local/bin/gpu-release"

# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def get_model(model_size, compute_type):
    key = f"{model_size}_{compute_type}"
    with _models_lock:
        if key not in _models:
            from faster_whisper import WhisperModel
            # The queue worker keeps its model across jobs; drop a different size before loading the next
            _models.clear()
            gc.collect()
            device = "cuda" if GPU_AVAILABLE else "cpu"
            ct = compute_type if GPU_AVAILABLE else "int8"
            _models[key] = WhisperModel(model_size, device=device, compute_type=ct)
        return _models[key]


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
    global _gpu_claimed
    if not claim_job(job_id):
        return  # cancelled or deleted while it was queued
    job = get_job(job_id)
    if not job:
        return

    upload_path = str(UPLOAD_DIR / job["filename"])
    audio_path = upload_path
    temp_audio = None

    try:
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

        # Take the GPU from the model-lab labeller first (gpu-claim pauses it, unloads Qwen and waits until VRAM is free);
        # without this the model load can hit CUDA out of memory while Qwen holds ~23 GB (2026-10-06).
        # Claimed once per queue run; run_queue releases it after the last job.
        if GPU_AVAILABLE and not _gpu_claimed and os.path.exists(GPU_CLAIM):
            update_job(job_id, progress=3, status_text="Waiting for the GPU (pausing model labelling)...")
            subprocess.run([GPU_CLAIM, "faster-whisper queue"], capture_output=True, timeout=300)
            _gpu_claimed = True

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


def run_queue():
    """Work through pending jobs oldest-first in this one process, so a batch claims the GPU and
    loads the model once instead of once per file. A second worker finding the lock taken just exits."""
    global _gpu_claimed
    while True:
        lock = open(WORKER_LOCK, "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return  # the lock holder looks at the queue again after letting go, so our job won't be missed
        try:
            fail_orphaned_jobs()
            while (job_id := next_pending_job()):
                transcribe_worker(job_id)
        finally:
            unload_models()
            if _gpu_claimed and os.path.exists(GPU_RELEASE):
                subprocess.run([GPU_RELEASE], capture_output=True, timeout=30)
                _gpu_claimed = False
            lock.close()
        # A job queued just before we let go had its own worker bow out above, so check once more
        if not next_pending_job():
            return

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


def render_transcript(job, fmt):
    """Returns (content, mimetype) for a completed job."""
    segments = json.loads(job["result_segments"]) if job["result_segments"] else []
    if fmt == "txt":
        return job["result_text"] or "", "text/plain"
    if fmt == "srt":
        return segments_to_srt(segments), "text/plain"
    if fmt == "vtt":
        return segments_to_vtt(segments), "text/vtt"
    return json.dumps({
        "filename": job["original_filename"],
        "language": job["detected_language"],
        "duration": job["duration"],
        "segments": segments
    }, indent=2), "application/json"

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


def launch_worker():
    """Start a queue worker; if one is already running it exits straight away and the running one picks the job up."""
    subprocess.Popen(
        [sys.executable, str(BASE_DIR / "server.py"), "--worker"],
        cwd=str(BASE_DIR),
        start_new_session=True,
    )


@app.route("/transcribe", methods=["POST"])
def transcribe():
    # Several "file" fields queue together as one batch (curl -F file=@a.m4a -F file=@b.m4a).
    # The UI sends one file per request with the batch_id from the first response.
    files = [f for f in request.files.getlist("file") if f.filename]
    if not files:
        return jsonify({"error": "No file provided"}), 400

    exts = []
    for f in files:
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            return jsonify({"error": f"Unsupported format: .{ext} ({f.filename})"}), 400
        exts.append(ext)

    batch_id = request.form.get("batch_id", "")
    if not BATCH_ID_RE.match(batch_id):
        batch_id = uuid.uuid4().hex

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
    if output_format not in OUTPUT_FORMATS:
        output_format = "srt"

    job_ids = []
    for f, ext in zip(files, exts):
        job_id = str(uuid.uuid4())
        f.save(str(UPLOAD_DIR / f"{job_id}.{ext}"))
        job_ids.append((job_id, f"{job_id}.{ext}", f.filename))

    # Create DB records (queued as 'pending')
    conn = get_db()
    conn.executemany("""
        INSERT INTO jobs (id, filename, original_filename, model_size, language,
                          compute_type, word_timestamps, output_format, beam_size, batch_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [(job_id, safe_name, original, model_size, language,
           compute_type, int(word_timestamps), output_format, beam_size, batch_id)
          for job_id, safe_name, original in job_ids])
    conn.commit()
    conn.close()

    try:
        launch_worker()
    except Exception as e:
        for job_id, _, _ in job_ids:
            update_job(job_id, status="error", error=str(e), status_text=f"Worker launch failed: {e}")
        return jsonify({"error": "Failed to launch transcription worker"}), 500

    ids = [job_id for job_id, _, _ in job_ids]
    return jsonify({"job_id": ids[0], "job_ids": ids, "batch_id": batch_id}), 202


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
    cancel_jobs("id", job_id)
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
    if fmt not in OUTPUT_FORMATS:
        return jsonify({"error": "Invalid format"}), 400

    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job["status"] != "completed":
        return jsonify({"error": "Job not completed"}), 400

    content, mimetype = render_transcript(job, fmt)
    basename = Path(job["original_filename"]).stem
    buf = io.BytesIO(content.encode("utf-8"))
    return send_file(buf, mimetype=mimetype,
                     as_attachment=True,
                     download_name=f"{basename}.{fmt}")


@app.route("/batch/<batch_id>")
def batch_status(batch_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT rowid AS seq, id, original_filename, status, progress, status_text, detected_language, "
        "duration, transcription_time, error, created_at, completed_at "
        "FROM jobs WHERE batch_id = ? ORDER BY rowid",
        (batch_id,)
    ).fetchall()
    if not rows:
        conn.close()
        return jsonify({"error": "Batch not found"}), 404

    # Files from other uploads still in the queue ahead of this batch's first unfinished file
    open_seqs = [r["seq"] for r in rows if r["status"] in ("pending", "processing")]
    ahead = 0
    if open_seqs:
        ahead = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN ('pending', 'processing') AND rowid < ? "
            "AND (batch_id IS NULL OR batch_id != ?)",
            (min(open_seqs), batch_id)
        ).fetchone()[0]
    conn.close()

    jobs = [{k: r[k] for k in r.keys() if k != "seq"} for r in rows]
    return jsonify({"batch_id": batch_id, "jobs": jobs, "queued_ahead": ahead})


@app.route("/batch/<batch_id>/cancel", methods=["POST"])
def cancel_batch(batch_id):
    cancel_jobs("batch_id", batch_id)
    return jsonify({"ok": True})


@app.route("/batch/<batch_id>/download/<fmt>")
def download_batch(batch_id, fmt):
    if fmt not in OUTPUT_FORMATS:
        return jsonify({"error": "Invalid format"}), 400

    conn = get_db()
    jobs = conn.execute(
        "SELECT * FROM jobs WHERE batch_id = ? AND status = 'completed' ORDER BY rowid", (batch_id,)
    ).fetchall()
    conn.close()
    if not jobs:
        return jsonify({"error": "No completed transcripts in this batch"}), 404

    buf = io.BytesIO()
    used = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for job in jobs:
            content, _ = render_transcript(job, fmt)
            stem = Path(job["original_filename"]).stem or job["id"]
            name, n = f"{stem}.{fmt}", 2
            while name in used:
                name, n = f"{stem} ({n}).{fmt}", n + 1
            used.add(name)
            zf.writestr(name, content)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip",
                     as_attachment=True,
                     download_name=f"transcripts-{batch_id[:8]}-{fmt}.zip")


@app.route("/history")
def history():
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 10))
    offset = (page - 1) * per_page

    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    rows = conn.execute(
        "SELECT id, original_filename, status, model_size, detected_language, "
        "duration, created_at, progress, error, batch_id, "
        "(SELECT COUNT(*) FROM jobs b WHERE b.batch_id = jobs.batch_id) AS batch_size "
        "FROM jobs ORDER BY rowid DESC LIMIT ? OFFSET ?",
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
    parser.add_argument("--worker", action="store_true", help="Work through the queued jobs and exit")
    args = parser.parse_args()

    init_db()
    if args.worker:
        run_queue()
        sys.exit(0)

    # Pick the queue back up after a restart or reboot
    if next_pending_job():
        launch_worker()

    print(f"GPU: {GPU_AVAILABLE} ({GPU_NAME})")
    print(f"Serving on http://0.0.0.0:8005")
    app.run(host="0.0.0.0", port=8005, debug=False)
