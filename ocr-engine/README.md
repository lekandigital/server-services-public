# 🔍 OCR Engine — Multi-Backend Video OCR

GPU-accelerated OCR service with **8 selectable backends**, video support, quality modes, and ensemble engine modes.

## Features

- **Image OCR** — PNG, JPG, WebP, BMP, TIFF, GIF
- **PDF OCR** — Multi-page with layout preservation
- **Video OCR** — MP4, MOV, MKV, WebM, AVI, and more
- **Multi-Backend** — PaddleOCR, EasyOCR, Surya, docTR, RapidOCR, Tesseract, TrOCR
- **Engine Modes** — Single, Cascade, Consensus, Maximum Recall
- **Quality Modes** — Standard, High, Max (3090 Ti optimized)
- **Video Strategies** — Scrolling Page, Slides, Document Camera, Full Archive
- **109+ Languages** supported

## Architecture

```
ocr-engine/
├── server.py                 # Flask app, routes, job processing
├── ocr_backends/
│   ├── __init__.py           # Registry, config dicts, factory
│   ├── base.py               # OCRBox dataclass, OCRBackend ABC
│   ├── paddle_backend.py     # PaddleOCR 2.x (default, required)
│   ├── paddle3_backend.py    # PaddleOCR 3.x / PP-OCRv5 (optional)
│   ├── easyocr_backend.py    # EasyOCR (optional)
│   ├── surya_backend.py      # Surya 2 (optional, heavy)
│   ├── doctr_backend.py      # docTR (optional)
│   ├── rapidocr_backend.py   # RapidOCR (optional)
│   ├── tesseract_backend.py  # Tesseract CLI (optional)
│   └── trocr_backend.py      # TrOCR line recognizer (optional)
├── video_ocr/
│   ├── __init__.py
│   ├── frames.py             # ffmpeg/ffprobe frame extraction
│   ├── preprocessing.py      # CLAHE, sharpen, threshold variants
│   ├── dedupe.py             # dHash frame + fuzzy line deduplication
│   ├── consensus.py          # Multi-backend merge strategies
│   ├── exports.py            # 10 export formats
│   └── pipeline.py           # Main orchestrator
├── requirements.txt          # Base dependencies
├── requirements-extra-ocr.txt # Optional backends
├── static/index.html         # Web UI
└── paddleocr.service         # systemd unit
```

## OCR Backends

| Backend | Key | GPU | Required | Best For |
|---------|-----|-----|----------|----------|
| PaddleOCR 2.x | `paddle` | ✅ | ✅ | Screen recordings, documents, multilingual |
| PaddleOCR 3.x | `paddle3` | ✅ | ❌ | High accuracy, layout, tables |
| EasyOCR | `easyocr` | ✅ | ❌ | Scene text, fallback, multilingual |
| Surya 2 | `surya` | ✅ | ❌ | Max quality, layout, tables |
| docTR | `doctr` | ✅ | ❌ | Documents, clean screenshots |
| RapidOCR | `rapidocr` | ✅ | ❌ | Fast batch OCR, ONNX |
| Tesseract | `tesseract` | ❌ | ❌ | Fallback, debugging |
| TrOCR | `trocr` | ✅ | ❌ | Cropped lines, handwriting |

## Quality Modes

- **Standard** — FPS=2, 1920px, single backend, fast
- **High** (recommended for 3090 Ti) — FPS=4, 2560px, cascade with fallbacks
- **Max** — FPS=6, 3840px, maximum recall, all backends, all scales

## Installation

### Base install (PaddleOCR only)

```bash
pip3 install -r requirements.txt
```

### Optional backends

```bash
pip3 install -r requirements-extra-ocr.txt

# Tesseract (system package):
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng
```

### System dependencies

```bash
# Already in deploy.sh:
sudo apt-get install -y ffmpeg poppler-utils
```

## Running

```bash
python3 server.py
# → http://localhost:8006
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web UI |
| `/health` | GET | Status + backend availability |
| `/backends` | GET | Full backend registry + config |
| `/ocr` | POST | Upload file for OCR |
| `/status/<id>` | GET | Job progress |
| `/result/<id>` | GET | Job result |
| `/download/<id>/<fmt>` | GET | Download export |
| `/history` | GET | Job history |
| `/delete/<id>` | DELETE | Delete job |

### Export Formats

`txt`, `deduped_txt`, `by_frame_txt`, `timestamps_txt`, `json`, `csv`, `srt`, `vtt`, `md`, `debug_json`

## Port

**8006** — do not change without updating `paddleocr.service` and `deploy.sh`.
