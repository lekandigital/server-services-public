# Whisper Transcriber (:8005)

Web UI for audio & video transcription using Faster Whisper (large-v3 model). Upload files, get transcriptions with timestamps. Uses GPU acceleration via CUDA.

## Screenshots

### Transcription Interface
Drag-and-drop upload with GPU status, advanced model options, and job history with download links.

![Whisper Transcriber UI](screenshots/transcriber-ui.png)

## How It Works

- Flask server with embedded HTML UI in `static/index.html`
- Uses `faster-whisper` library (CTranslate2 backend) for fast GPU inference
- Stores job state in SQLite (`jobs.db`, auto-created)
- Uploaded files go to `uploads/` directory (auto-created)

## Batches

Pick or drop any number of files; they upload one by one and join a single queue. One worker process
(`server.py --worker`, guarded by `worker.lock`) works through the queue oldest-first, claiming the GPU and
loading the model once for the whole run. Picking more files while a batch runs adds them to it, and a
reload picks the running batch back up. Finished batches download as one zip per format and can be
reopened from History (📦).

API: `POST /transcribe` takes several `file` fields (or a `batch_id` to add to an existing batch) and
returns `batch_id`; then `GET /batch/<id>`, `POST /batch/<id>/cancel`, `GET /batch/<id>/download/<srt|vtt|txt|json>`.

```bash
curl -F file=@a.m4a -F file=@b.m4a http://<box>:8005/transcribe
```

A job left `processing` when its worker died (service restart, crash) is marked "Interrupted" by the next
worker so it can't block the queue; upload it again to retry.

## Dependencies

```
Python 3.10+
NVIDIA GPU with CUDA support
CUDA toolkit + cuDNN (for GPU acceleration)
ffmpeg (for audio extraction from video files)

pip packages:
  flask==3.1.2
  flask-cors==6.0.2
  faster-whisper==1.2.1
```

### System packages (Ubuntu 22.04)

```bash
sudo apt install -y ffmpeg
pip3 install -r requirements.txt
# For GPU: pip3 install nvidia-cublas-cu11 nvidia-cudnn-cu11  (or matching CUDA version)
```

## Files

| File | Purpose |
|------|---------|
| `server.py` | Flask app — API + job management |
| `static/index.html` | Frontend UI |
| `jobs.db` | SQLite job database (auto-created at runtime) |
| `uploads/` | Uploaded media files (auto-created at runtime) |

## Run Locally

```bash
pip3 install -r requirements.txt
python3 server.py
# Serves on http://0.0.0.0:8005
```

## Original Deployment Location

```
/opt/faster-whisper-ui/
```
