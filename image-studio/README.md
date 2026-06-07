# ML Image Studio

GPU-accelerated image editing service powered by deep learning models.

**Port:** 8008
**Tech:** Python/Flask + CUDA (PyTorch, ONNX Runtime, Transformers)
**GPU:** NVIDIA RTX 3090 Ti

All configurable tools start at their maximum-quality defaults. You can lower those settings from the UI or API when you want faster jobs.
You can also raise `compute_allocation` per job to ask supported tools to spend more runtime on refinement.

## Tools

| Tool | Model | Description |
|------|-------|-------------|
| ✂️ Background Removal | BiRefNet / BRIA RMBG / BEN2 / rembg | Remove backgrounds with selectable CUDA backends |
| 🔍 Super-Resolution | Real-ESRGAN | 2×/4× AI upscaling with detail generation |
| 🎨 Style Transfer | PyTorch NST | Apply artistic styles (Monet, Candy, etc.) |
| 🌈 Colorization | DeOldify | Auto-colorize black & white photos |
| 👤 Face Restoration | GFPGAN | Enhance and restore faces |
| ✨ Denoising | OpenCV NLM | Remove noise and grain from photos |

## Background Removal Models

The background removal tool supports **6 selectable backends** via the `bg_model` parameter:

| Key | Model | HF ID | Best For |
|-----|-------|--------|----------|
| `birefnet-dynamic` | BiRefNet Dynamic | `ZhengPeng7/BiRefNet_dynamic` | General-purpose, adaptive resolution |
| `birefnet-hr-matting` | BiRefNet HR Matting | `ZhengPeng7/BiRefNet_HR-matting` | Best edge quality, hair/fur detail |
| `bria-rmbg-2` | BRIA RMBG-2.0 | `briaai/RMBG-2.0` | Product/e-commerce images |
| `ben2` | BEN2 | `PramaLLC/BEN2` | Object/product with edge refinement |
| `birefnet-general` | BiRefNet General | `ZhengPeng7/BiRefNet` | Reliable fallback |
| `legacy-rembg` | rembg (U²-Net) | N/A (ONNX) | Old compatibility mode |

### Compute Allocation for Background Removal

| Allocation | Resolution | Behavior |
|------------|-----------|----------|
| `standard` | ≤1024 | Fast fp16 inference, baseline quality |
| `high` | 1536–2048 | Better detail, light mask cleanup |
| `max` | Up to 2048–2304 | Heaviest path, full VRAM utilization, mask feathering |

## Quick Start

```bash
pip3 install -r requirements.txt
python3 server.py
# → http://localhost:8008
```

### Optional: BEN2 model

```bash
pip3 install -r requirements-extra-bg.txt
```

BEN2 is optional. If not installed, the UI will show it as an option but the server will return a clear error message if selected.

### First-run model download

All HuggingFace models are downloaded automatically on first use and cached in `~/.cache/huggingface/`. The first run with a new model may take several minutes depending on your connection.

## API

### `GET /health`
Service health, GPU status, CUDA availability, loaded models, and background model availability.

### `POST /process`
Upload an image for processing.

**Form fields:**
- `file` — Image file (PNG, JPG, WebP, BMP, TIFF)
- `tool` — One of: `remove-bg`, `upscale`, `style-transfer`, `colorize`, `restore-face`, `denoise`
- Tool-specific options:
  - `compute_allocation` — `standard`, `high`, or `max`
  - **Background removal** (`remove-bg`):
    - `bg_model` — One of: `birefnet-dynamic` (default), `birefnet-hr-matting`, `bria-rmbg-2`, `ben2`, `birefnet-general`, `legacy-rembg`
    - `bg_refinement` — `auto`, `none`, `light`, `heavy`
    - `bg_resolution_mode` — `auto`, `native`, `force-square`
  - `scale` — Upscale factor (2 or 4, default: 4)
  - `style` — Style preset (mosaic, candy, rain_princess, udnie, pointilism)
  - `render_factor` — Colorization quality (7–45, default: 45)
  - `strength` — Denoise strength (1–30, default: 30)

**Returns:** `{ "job_id": "...", "status": "queued" }`

### `GET /tools`
Lists all tools, styles, defaults, compute allocations, and background model metadata.

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
- **BiRefNet models:** ~900MB each (cached in `~/.cache/huggingface/`)
- **BRIA RMBG-2.0:** ~1.5GB (cached in `~/.cache/huggingface/`)
- **BEN2:** varies (optional, cached in `~/.cache/huggingface/`)
- **Real-ESRGAN:** ~64MB (`RealESRGAN_x4plus.pth`)
- **GFPGAN:** ~332MB (`GFPGANv1.4.pth`)
- **Style Transfer:** ~7MB per style (`.t7` format)
- **rembg:** ~170MB (U²-Net ONNX, cached in `~/.u2net/`)
- **DeOldify:** ~250MB (auto-downloaded)

Total: ~4–5GB disk space for all models.

## Model Licenses

| Model | License |
|-------|---------|
| BiRefNet (all variants) | MIT |
| **BRIA RMBG-2.0** | **⚠️ Non-commercial** — Self-hosted HuggingFace weights are non-commercial unless you hold a separate BRIA commercial license/agreement. Do not assume free commercial use. |
| BEN2 | MIT base — check the [PramaLLC/BEN2 model card](https://huggingface.co/PramaLLC/BEN2) for any commercial/full-model caveats. |
| rembg / U²-Net | MIT |
| Real-ESRGAN | BSD-3-Clause |
| GFPGAN | Apache-2.0 |
| DeOldify | MIT |

## Testing

```bash
# Syntax check
python3 -m py_compile server.py

# Smoke test all available background models
python3 test_background_models.py

# Health check
curl http://localhost:8008/health | python3 -m json.tool
curl http://localhost:8008/tools | python3 -m json.tool
```

## Systemd

```bash
sudo cp image-studio.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now image-studio
journalctl -u image-studio -f
```
