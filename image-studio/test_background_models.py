#!/usr/bin/env python3
"""Smoke test for background removal models.

Creates a synthetic test image and runs each available bg_model with
standard compute allocation. Outputs are saved to outputs/test_<model>.png.
Unavailable optional models are skipped cleanly.

Usage:
    python test_background_models.py
"""

import os
import sys
import time

# Ensure image-studio dir is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path
from PIL import Image, ImageDraw


def create_test_image(path, size=512):
    """Create a simple gradient image with a centered circle (foreground)."""
    img = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(img)

    # Gradient background
    for y in range(size):
        r = int(80 + 120 * (y / size))
        g = int(140 + 80 * (y / size))
        b = int(200 - 60 * (y / size))
        draw.line([(0, y), (size, y)], fill=(r, g, b))

    # White circle as foreground object
    margin = size // 4
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(255, 255, 255),
        outline=(200, 200, 200),
        width=3,
    )

    # Add some detail inside the circle
    cx, cy = size // 2, size // 2
    r = size // 8
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 180, 100))

    img.save(path)
    return img


def main():
    # Import from server.py
    from server import BACKGROUND_MODELS, check_bg_model_available, run_remove_bg

    base_dir = Path(__file__).resolve().parent
    output_dir = base_dir / "outputs"
    output_dir.mkdir(exist_ok=True)

    # Create test input
    test_input = output_dir / "test_input.png"
    create_test_image(str(test_input), size=512)
    print(f"Created test input: {test_input}")

    results = []
    total_start = time.time()

    for model_key, spec in BACKGROUND_MODELS.items():
        print(f"\n{'='*60}")
        print(f"Testing: {model_key} — {spec['label']}")

        avail, err = check_bg_model_available(model_key)
        if not avail:
            print(f"  SKIPPED (not installed): {err}")
            results.append((model_key, "SKIPPED", 0, err))
            continue

        output_path = output_dir / f"test_{model_key}.png"
        options = {
            "compute_allocation": "standard",
            "bg_model": model_key,
            "bg_refinement": "auto",
            "bg_resolution_mode": "auto",
        }

        start = time.time()
        try:
            run_remove_bg(str(test_input), str(output_path), options)
            elapsed = time.time() - start

            # Validate output
            out_img = Image.open(output_path)
            assert out_img.mode == "RGBA", f"Expected RGBA, got {out_img.mode}"
            assert out_img.size[0] > 0 and out_img.size[1] > 0

            print(f"  PASS — {elapsed:.2f}s — {out_img.size[0]}x{out_img.size[1]} RGBA")
            print(f"  Output: {output_path}")
            results.append((model_key, "PASS", elapsed, None))

        except Exception as e:
            elapsed = time.time() - start
            print(f"  FAIL — {elapsed:.2f}s — {e}")
            results.append((model_key, "FAIL", elapsed, str(e)))

    total_elapsed = time.time() - total_start

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY ({total_elapsed:.1f}s total)")
    print(f"{'='*60}")
    passed = sum(1 for _, s, _, _ in results if s == "PASS")
    skipped = sum(1 for _, s, _, _ in results if s == "SKIPPED")
    failed = sum(1 for _, s, _, _ in results if s == "FAIL")

    for model_key, status, elapsed, err in results:
        line = f"  {status:8s}  {model_key}"
        if elapsed > 0:
            line += f"  ({elapsed:.2f}s)"
        if err:
            line += f"  — {err}"
        print(line)

    print(f"\n  {passed} passed, {skipped} skipped, {failed} failed")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
