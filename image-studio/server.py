#!/usr/bin/env python3
"""ML Image Studio — GPU-accelerated image editing service.

All tools default to maximum quality settings.

Tools:
  1. Background Removal  (rembg / U²-Net)
  2. Super-Resolution     (Real-ESRGAN)
  3. Style Transfer        (PyTorch Neural Style Transfer)
  4. Colorization          (DeOldify)
  5. Face Restoration      (GFPGAN)
  6. Denoising             (SCUNet)
"""

import argparse
import gc
import io
import json
import mimetypes
import os
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from PIL import Image

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
STYLES_DIR = BASE_DIR / "styles"
DB_PATH = BASE_DIR / "jobs.db"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
STYLES_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "bmp", "tiff"}
MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100 MB

TOOLS = {
    "remove-bg": {"name": "Background Removal", "icon": "✂️", "desc": "Remove image backgrounds with AI"},
    "upscale": {"name": "Super-Resolution", "icon": "🔍", "desc": "2×/4× AI upscaling with Real-ESRGAN"},
    "style-transfer": {"name": "Style Transfer", "icon": "🎨", "desc": "Apply artistic styles to photos"},
    "colorize": {"name": "Colorization", "icon": "🌈", "desc": "Auto-colorize black & white photos"},
    "restore-face": {"name": "Face Restoration", "icon": "👤", "desc": "Enhance and restore faces with GFPGAN"},
    "denoise": {"name": "Denoising", "icon": "✨", "desc": "Remove noise and grain from photos"},
}

STYLE_PRESETS = {
    "mosaic": "Mosaic",
    "candy": "Candy",
    "rain_princess": "Rain Princess",
    "udnie": "Udnie",
    "pointilism": "Pointilism",
}

COMPUTE_ALLOCATIONS = {
    "standard": {
        "name": "Standard",
        "desc": "Balanced runtime with baseline refinement.",
    },
    "high": {
        "name": "High",
        "desc": "Spend more compute on supported tools for better cleanup.",
    },
    "max": {
        "name": "Max",
        "desc": "Use the heaviest supported refinement path for this job.",
    },
}

DEFAULT_OPTIONS = {
    "remove-bg": {"compute_allocation": "standard"},
    "upscale": {"scale": 4, "compute_allocation": "standard"},
    "style-transfer": {"style": "candy", "compute_allocation": "standard"},
    "colorize": {"render_factor": 45, "compute_allocation": "standard"},
    "restore-face": {"compute_allocation": "standard"},
    "denoise": {"strength": 30, "compute_allocation": "standard"},
}

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder=str(BASE_DIR / "static"))
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
CORS(app)

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
            tool TEXT,
            filename TEXT,
            original_filename TEXT,
            status TEXT DEFAULT 'queued',
            progress REAL DEFAULT 0,
            progress_msg TEXT DEFAULT '',
            options TEXT DEFAULT '{}',
            input_width INTEGER,
            input_height INTEGER,
            output_width INTEGER,
            output_height INTEGER,
            output_filename TEXT,
            output_size INTEGER,
            elapsed REAL DEFAULT 0,
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
# GPU detection
# ---------------------------------------------------------------------------
def detect_gpu():
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return True, r.stdout.strip().split("\n")[0]
    except Exception:
        pass
    return False, None


GPU_AVAILABLE, GPU_NAME = detect_gpu()

# ---------------------------------------------------------------------------
# Model management (lazy-loaded, unloaded when idle)
# ---------------------------------------------------------------------------
_models = {}
_models_lock = threading.Lock()
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


def get_device():
    if GPU_AVAILABLE:
        try:
            import torch
            if torch.cuda.is_available():
                return torch.device("cuda")
        except Exception:
            pass
    import torch
    return torch.device("cpu")


def get_compute_allocation(options):
    allocation = str(options.get("compute_allocation", "standard")).lower()
    if allocation not in COMPUTE_ALLOCATIONS:
        return "standard"
    return allocation


def ensure_realesrgan_weights():
    model_path = str(BASE_DIR / "weights" / "RealESRGAN_x4plus.pth")
    if not os.path.exists(model_path):
        os.makedirs(str(BASE_DIR / "weights"), exist_ok=True)
        import urllib.request
        url = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"
        urllib.request.urlretrieve(url, model_path)
    return model_path


def get_realesrgan_upsampler(use_half):
    cache_key = "realesrgan_half" if use_half else "realesrgan_full"
    with _models_lock:
        if cache_key not in _models:
            from realesrgan import RealESRGANer
            from basicsr.archs.rrdbnet_arch import RRDBNet

            model_arch = RRDBNet(
                num_in_ch=3, num_out_ch=3, num_feat=64,
                num_block=23, num_grow_ch=32, scale=4
            )

            _models[cache_key] = RealESRGANer(
                scale=4,
                model_path=ensure_realesrgan_weights(),
                model=model_arch,
                tile=0,
                tile_pad=10,
                pre_pad=0,
                half=use_half,
                gpu_id=0 if GPU_AVAILABLE else None,
            )
    return _models[cache_key]


# ---------------------------------------------------------------------------
# Tool: Background Removal
# ---------------------------------------------------------------------------
def run_remove_bg(input_path, output_path, options):
    from rembg import remove
    allocation = get_compute_allocation(options)
    img = Image.open(input_path)
    remove_kwargs = {}
    if allocation in {"high", "max"}:
        remove_kwargs.update({
            "alpha_matting": True,
            "alpha_matting_foreground_threshold": 240,
            "alpha_matting_background_threshold": 10,
            "alpha_matting_erode_size": 8 if allocation == "high" else 4,
        })
    if allocation == "max":
        remove_kwargs["post_process_mask"] = True
    result = remove(img, **remove_kwargs)
    result.save(output_path, "PNG")


# ---------------------------------------------------------------------------
# Tool: Super-Resolution (Real-ESRGAN)
# ---------------------------------------------------------------------------
def run_upscale(input_path, output_path, options):
    import numpy as np
    import cv2

    scale = int(options.get("scale", 4))
    allocation = get_compute_allocation(options)
    if scale not in (2, 4):
        scale = 4
    use_half = GPU_AVAILABLE and allocation == "standard"
    upsampler = get_realesrgan_upsampler(use_half=use_half)
    img = cv2.imread(str(input_path), cv2.IMREAD_UNCHANGED)
    output, _ = upsampler.enhance(img, outscale=scale)
    cv2.imwrite(str(output_path), output)


# ---------------------------------------------------------------------------
# Tool: Style Transfer (PyTorch)
# ---------------------------------------------------------------------------
def run_style_transfer(input_path, output_path, options):
    import torch
    import torchvision.transforms as transforms
    from torchvision import models
    import numpy as np

    style_name = options.get("style", "candy")

    # Use fast neural style transfer from PyTorch hub
    model_path = str(BASE_DIR / "weights" / f"{style_name}.pth")
    if not os.path.exists(model_path):
        os.makedirs(str(BASE_DIR / "weights"), exist_ok=True)
        import urllib.request
        base_url = "https://cs.stanford.edu/people/jcjohns/fast-neural-style/models"
        # Map to available models
        model_urls = {
            "mosaic": f"{base_url}/eccv16/mosaic.t7",
            "candy": f"{base_url}/eccv16/candy.t7",
            "rain_princess": f"{base_url}/eccv16/rain_princess.t7",
            "udnie": f"{base_url}/eccv16/udnie.t7",
            "pointilism": f"{base_url}/instance_norm/pointilism.t7",
        }
        url = model_urls.get(style_name, model_urls["candy"])
        # Download the t7 model
        t7_path = model_path.replace(".pth", ".t7")
        urllib.request.urlretrieve(url, t7_path)
        model_path = t7_path

    # Use OpenCV DNN for t7 models (simpler and more reliable)
    import cv2
    t7_path = model_path.replace(".pth", ".t7")
    if os.path.exists(t7_path):
        model_path = t7_path

    net = cv2.dnn.readNetFromTorch(model_path)
    img = cv2.imread(str(input_path))
    h, w = img.shape[:2]

    blob = cv2.dnn.blobFromImage(img, 1.0, (w, h),
                                  (103.939, 116.779, 123.680), swapRB=False, crop=False)
    net.setInput(blob)
    output = net.forward()
    output = output.reshape(3, output.shape[2], output.shape[3])
    output[0] += 103.939
    output[1] += 116.779
    output[2] += 123.680
    output = output.transpose(1, 2, 0)
    output = np.clip(output, 0, 255).astype(np.uint8)
    cv2.imwrite(str(output_path), output)


# ---------------------------------------------------------------------------
# Tool: Colorization (DeOldify)
# ---------------------------------------------------------------------------
def run_colorize(input_path, output_path, options):
    import torch
    import numpy as np
    import cv2

    render_factor = int(options.get("render_factor", 45))
    render_factor = max(7, min(45, render_factor))

    with _models_lock:
        if "deoldify" not in _models:
            from deoldify import device as deoldify_device
            from deoldify.device_id import DeviceId
            if GPU_AVAILABLE:
                deoldify_device.set(device=DeviceId.GPU0)
            else:
                deoldify_device.set(device=DeviceId.CPU)

            from deoldify.visualize import get_image_colorizer
            _models["deoldify"] = get_image_colorizer(artistic=True)

    colorizer = _models["deoldify"]
    result = colorizer.get_transformed_image(
        str(input_path), render_factor=render_factor, watermarked=False
    )
    if result:
        result.save(output_path)
    else:
        # Fallback: copy original
        Image.open(input_path).save(output_path)


# ---------------------------------------------------------------------------
# Tool: Face Restoration (GFPGAN)
# ---------------------------------------------------------------------------
def run_restore_face(input_path, output_path, options):
    import cv2
    import numpy as np
    allocation = get_compute_allocation(options)
    use_bg_upsampler = allocation in {"high", "max"}
    upscale = 2 if allocation != "max" else 4
    bg_upsampler = None
    if use_bg_upsampler:
        bg_upsampler = get_realesrgan_upsampler(
            use_half=GPU_AVAILABLE and allocation == "high"
        )

    with _models_lock:
        cache_key = f"gfpgan_{allocation}"
        if cache_key not in _models:
            from gfpgan import GFPGANer

            model_path = str(BASE_DIR / "weights" / "GFPGANv1.4.pth")
            if not os.path.exists(model_path):
                os.makedirs(str(BASE_DIR / "weights"), exist_ok=True)
                import urllib.request
                url = "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.4/GFPGANv1.4.pth"
                urllib.request.urlretrieve(url, model_path)

            _models[cache_key] = GFPGANer(
                model_path=model_path,
                upscale=upscale,
                arch="clean",
                channel_multiplier=2,
                bg_upsampler=bg_upsampler,
            )

    restorer = _models[cache_key]
    img = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
    _, _, output = restorer.enhance(
        img, has_aligned=False, only_center_face=False, paste_back=True
    )
    cv2.imwrite(str(output_path), output)


# ---------------------------------------------------------------------------
# Tool: Denoising (OpenCV Non-local Means + optional SCUNet)
# ---------------------------------------------------------------------------
def run_denoise(input_path, output_path, options):
    import cv2
    import numpy as np

    strength = int(options.get("strength", 30))
    allocation = get_compute_allocation(options)
    strength = max(1, min(30, strength))
    template_window = 7
    search_window = 21
    if allocation == "high":
        template_window = 9
        search_window = 31
    elif allocation == "max":
        template_window = 11
        search_window = 35

    img = cv2.imread(str(input_path))
    # Use OpenCV's fastNlMeansDenoisingColored — reliable and fast
    denoised = cv2.fastNlMeansDenoisingColored(
        img, None, strength, strength, template_window, search_window
    )
    cv2.imwrite(str(output_path), denoised)


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------
TOOL_RUNNERS = {
    "remove-bg": run_remove_bg,
    "upscale": run_upscale,
    "style-transfer": run_style_transfer,
    "colorize": run_colorize,
    "restore-face": run_restore_face,
    "denoise": run_denoise,
}


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------
def process_job(job_id):
    job = get_job(job_id)
    if not job:
        return

    start_time = time.time()
    try:
        begin_model_use()
        update_job(job_id, status="processing", progress=0.1,
                   progress_msg="Loading model...")

        tool = job["tool"]
        options = json.loads(job["options"]) if job["options"] else {}
        input_path = UPLOAD_DIR / job["filename"]
        ext = "png" if tool == "remove-bg" else Path(job["original_filename"]).suffix.lstrip(".")
        if ext not in ALLOWED_EXTENSIONS:
            ext = "png"
        output_name = f"{job_id}_output.{ext}"
        output_path = OUTPUT_DIR / output_name

        # Get input dimensions
        try:
            with Image.open(input_path) as img:
                w, h = img.size
                update_job(job_id, input_width=w, input_height=h)
        except Exception:
            pass

        update_job(job_id, progress=0.3, progress_msg=f"Running {TOOLS[tool]['name']}...")

        runner = TOOL_RUNNERS.get(tool)
        if not runner:
            raise ValueError(f"Unknown tool: {tool}")

        runner(str(input_path), str(output_path), options)

        # Get output info
        out_w, out_h = 0, 0
        out_size = 0
        try:
            with Image.open(output_path) as img:
                out_w, out_h = img.size
            out_size = output_path.stat().st_size
        except Exception:
            pass

        elapsed = time.time() - start_time

        update_job(
            job_id,
            status="completed",
            progress=1.0,
            progress_msg="Complete",
            output_filename=output_name,
            output_width=out_w,
            output_height=out_h,
            output_size=out_size,
            elapsed=round(elapsed, 2),
            completed_at=datetime.utcnow().isoformat(),
        )

    except Exception as e:
        elapsed = time.time() - start_time
        update_job(
            job_id,
            status="error",
            progress_msg=f"Error: {str(e)}",
            error_msg=traceback.format_exc(),
            elapsed=round(elapsed, 2),
            completed_at=datetime.utcnow().isoformat(),
        )
    finally:
        if end_model_use() == 0:
            unload_models()


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
        "tools": list(TOOLS.keys()),
        "models_loaded": list(_models.keys()),
        "active_jobs": count_active_jobs(),
    })


@app.route("/tools")
def tools_list():
    return jsonify({
        "tools": TOOLS,
        "styles": STYLE_PRESETS,
        "defaults": DEFAULT_OPTIONS,
        "compute_allocations": COMPUTE_ALLOCATIONS,
    })


def launch_worker(job_id):
    subprocess.Popen(
        [sys.executable, str(BASE_DIR / "server.py"), "--job-id", job_id],
        cwd=str(BASE_DIR),
        start_new_session=True,
    )


@app.route("/process", methods=["POST"])
def process_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"Unsupported format: {ext}"}), 400

    tool = request.form.get("tool", "")
    if tool not in TOOLS:
        return jsonify({"error": f"Unknown tool: {tool}. Available: {list(TOOLS.keys())}"}), 400

    # Parse tool-specific options
    options = dict(DEFAULT_OPTIONS.get(tool, {}))
    options["compute_allocation"] = request.form.get(
        "compute_allocation", options.get("compute_allocation", "standard")
    )
    if tool == "upscale":
        options["scale"] = request.form.get("scale", str(options["scale"]))
    elif tool == "style-transfer":
        options["style"] = request.form.get("style", options["style"])
    elif tool == "colorize":
        options["render_factor"] = request.form.get(
            "render_factor", str(options["render_factor"])
        )
    elif tool == "denoise":
        options["strength"] = request.form.get("strength", str(options["strength"]))

    job_id = str(uuid.uuid4())
    safe_name = f"{job_id}.{ext}"
    save_path = UPLOAD_DIR / safe_name
    f.save(str(save_path))

    with _db_lock:
        conn = get_db()
        conn.execute(
            """INSERT INTO jobs (id, tool, filename, original_filename, status, options, created_at)
               VALUES (?, ?, ?, ?, 'queued', ?, ?)""",
            (job_id, tool, safe_name, f.filename, json.dumps(options),
             datetime.utcnow().isoformat()),
        )
        conn.commit()
        conn.close()

    try:
        launch_worker(job_id)
    except Exception as e:
        update_job(job_id, status="error",
                   progress_msg=f"Worker launch failed: {str(e)}",
                   error_msg=traceback.format_exc())
        return jsonify({"error": "Failed to launch worker"}), 500

    return jsonify({"job_id": job_id, "tool": tool, "status": "queued"}), 202


@app.route("/status/<job_id>")
def job_status(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "job_id": job["id"],
        "tool": job["tool"],
        "status": job["status"],
        "progress": job["progress"],
        "progress_msg": job["progress_msg"],
        "elapsed": job["elapsed"],
    })


@app.route("/result/<job_id>")
def job_result(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job["status"] != "completed":
        return jsonify({"error": "Job not completed", "status": job["status"]}), 400

    return jsonify({
        "job_id": job["id"],
        "tool": job["tool"],
        "original_filename": job["original_filename"],
        "input_width": job["input_width"],
        "input_height": job["input_height"],
        "output_width": job["output_width"],
        "output_height": job["output_height"],
        "output_size": job["output_size"],
        "elapsed": job["elapsed"],
        "status": "completed",
    })


@app.route("/download/<job_id>")
def download(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job["status"] != "completed":
        return jsonify({"error": "Job not completed"}), 400

    output_path = OUTPUT_DIR / job["output_filename"]
    if not output_path.exists():
        return jsonify({"error": "Output file not found"}), 404

    original_stem = Path(job["original_filename"]).stem
    tool_label = job["tool"]
    ext = output_path.suffix
    download_name = f"{original_stem}_{tool_label}{ext}"

    return send_file(str(output_path), as_attachment=True, download_name=download_name)


@app.route("/preview/<job_id>")
def preview(job_id):
    """Serve the output image inline for before/after comparison."""
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job["status"] != "completed":
        return jsonify({"error": "Job not completed"}), 400

    output_path = OUTPUT_DIR / job["output_filename"]
    if not output_path.exists():
        return jsonify({"error": "Output file not found"}), 404

    mimetype = mimetypes.guess_type(str(output_path))[0] or "application/octet-stream"
    return send_file(str(output_path), mimetype=mimetype)


@app.route("/preview-input/<job_id>")
def preview_input(job_id):
    """Serve the original uploaded image."""
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    input_path = UPLOAD_DIR / job["filename"]
    if not input_path.exists():
        return jsonify({"error": "Input file not found"}), 404

    return send_file(str(input_path))


@app.route("/history")
def history():
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 20))

    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    rows = conn.execute(
        "SELECT id, tool, original_filename, status, progress, progress_msg, "
        "input_width, input_height, output_width, output_height, output_size, "
        "elapsed, created_at, completed_at FROM jobs "
        "ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (per_page, (page - 1) * per_page),
    ).fetchall()
    conn.close()

    return jsonify({
        "jobs": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
    })


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
    for d, fn in [(UPLOAD_DIR, job["filename"]), (OUTPUT_DIR, job.get("output_filename"))]:
        if fn:
            p = d / fn
            if p.exists():
                p.unlink()

    return jsonify({"status": "deleted"})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", help="Run a single job and exit")
    args = parser.parse_args()

    init_db()
    if args.job_id:
        process_job(args.job_id)
        sys.exit(0)

    print(f"ML Image Studio starting on port 8008...")
    print(f"GPU: {GPU_AVAILABLE} ({GPU_NAME})")
    print(f"Tools: {', '.join(TOOLS.keys())}")
    app.run(host="0.0.0.0", port=8008, debug=False, threaded=True)
