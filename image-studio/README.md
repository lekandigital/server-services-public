# ML Image Studio

GPU-accelerated image editing service powered by deep learning models.

**Port:** 8008
**Tech:** Python/Flask + CUDA (PyTorch, ONNX Runtime)
**GPU:** NVIDIA RTX 3090 Ti

All configurable tools start at their maximum-quality defaults. You can lower those settings from the UI or API when you want faster jobs.
You can also raise `compute_allocation` per job to ask supported tools to spend more runtime on refinement.

## Tools

| Tool | Model | Description |
|------|-------|-------------|
| ✂️ Background Removal | rembg (U²-Net) | Remove backgrounds, output transparent PNG |
| 🔍 Super-Resolution | Real-ESRGAN | 2×/4× AI upscaling with detail generation |
| 🎨 Style Transfer | PyTorch NST | Apply artistic styles (Monet, Candy, etc.) |
| 🌈 Colorization | DeOldify | Auto-colorize black & white photos |
| 👤 Face Restoration | GFPGAN | Enhance and restore faces |
| ✨ Denoising | OpenCV NLM | Remove noise and grain from photos |

## Quick Start

```bash
pip3 install -r requirements.txt
python3 server.py
# → http://localhost:8008
```

## API

### `GET /health`
Service health, GPU status, and loaded models.

### `POST /process`
Upload an image for processing.

**Form fields:**
- `file` — Image file (PNG, JPG, WebP, BMP, TIFF)
- `tool` — One of: `remove-bg`, `upscale`, `style-transfer`, `colorize`, `restore-face`, `denoise`
- Tool-specific options:
  - `compute_allocation` — `standard`, `high`, or `max` (extra compute is most meaningful for background removal, upscale, face restoration, and denoising)
  - `scale` — Upscale factor (2 or 4, default: 4 / max quality)
  - `style` — Style preset (mosaic, candy, rain_princess, udnie, pointilism)
  - `render_factor` — Colorization quality (7–45, default: 45 / max quality)
  - `strength` — Denoise strength (1–30, default: 30 / max quality)

**Returns:** `{ "job_id": "...", "status": "queued" }`

### `GET /status/<job_id>`
Poll processing progress.

### `GET /result/<job_id>`
Get result metadata (dimensions, file size, elapsed time).

### `GET /download/<job_id>`
Download the processed image.

### `GET /preview/<job_id>`
View the output image inline.

### `GET /history`
List all jobs with pagination (`?page=1&per_page=20`).

### `DELETE /delete/<job_id>`
Delete a job and its files.

## Model Weights

Models are downloaded automatically on first use:
- **Real-ESRGAN:** ~64MB (`RealESRGAN_x4plus.pth`)
- **GFPGAN:** ~332MB (`GFPGANv1.4.pth`)
- **Style Transfer:** ~7MB per style (`.t7` format)
- **rembg:** ~170MB (U²-Net ONNX, cached in `~/.u2net/`)
- **DeOldify:** ~250MB (auto-downloaded)

Total: ~1GB disk space for all models.

## Systemd

```bash
sudo cp image-studio.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now image-studio
journalctl -u image-studio -f
```
