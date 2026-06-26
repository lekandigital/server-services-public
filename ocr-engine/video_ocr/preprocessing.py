"""Preprocessing variants for OCR input images."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)


def generate_variants(
    frame_path: str,
    variant_list: list[str],
    scales: list[float] | None = None,
    output_dir: str | None = None,
) -> list[dict]:
    """Generate preprocessing variants of a single frame.

    Args:
        frame_path:   Path to the source frame image.
        variant_list: List of variant names to generate (e.g. ["native", "sharpen"]).
        scales:       Optional list of scale factors (e.g. [1.0, 1.5]).
        output_dir:   Directory to write variant images. If None, uses frame dir.

    Returns:
        List of dicts: [{"path": str, "variant": str, "scale": float}, ...]
    """
    scales = scales or [1.0]
    if output_dir is None:
        output_dir = str(Path(frame_path).parent)

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    img = cv2.imread(frame_path)
    if img is None:
        log.warning("Failed to read image: %s", frame_path)
        return []

    stem = Path(frame_path).stem
    results = []

    for scale in scales:
        # Scale the image if needed
        if scale != 1.0:
            h, w = img.shape[:2]
            new_w = int(w * scale)
            new_h = int(h * scale)
            scaled = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
        else:
            scaled = img

        for variant in variant_list:
            processed = _apply_variant(scaled, variant)
            if processed is None:
                continue

            # For native at 1.0x, just use the original file
            if variant == "native" and scale == 1.0:
                results.append({
                    "path": frame_path,
                    "variant": "native",
                    "scale": 1.0,
                })
                continue

            # Write variant to disk
            suffix = f"_{variant}_s{scale:.1f}" if scale != 1.0 else f"_{variant}"
            out_path = str(Path(output_dir) / f"{stem}{suffix}.png")
            cv2.imwrite(out_path, processed)
            results.append({
                "path": out_path,
                "variant": variant,
                "scale": scale,
            })

    return results


def _apply_variant(img: np.ndarray, variant: str) -> Optional[np.ndarray]:
    """Apply a single preprocessing variant to an image.

    Returns the processed image, or None if the variant is unknown.
    """
    if variant == "native":
        return img

    if variant == "grayscale":
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    if variant == "contrast":
        return _apply_clahe(img)

    if variant == "sharpen":
        return _apply_unsharp_mask(img)

    if variant == "threshold":
        return _apply_binary_threshold(img)

    if variant == "adaptive_threshold":
        return _apply_adaptive_threshold(img)

    if variant == "upscale_2x":
        h, w = img.shape[:2]
        return cv2.resize(img, (w * 2, h * 2), interpolation=cv2.INTER_LANCZOS4)

    log.warning("Unknown preprocessing variant: %s", variant)
    return img  # Return original for unknown variants


def _apply_clahe(img: np.ndarray) -> np.ndarray:
    """Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_channel, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)
    enhanced = cv2.merge([l_channel, a, b])
    return cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)


def _apply_unsharp_mask(img: np.ndarray, sigma: float = 1.0, strength: float = 1.5) -> np.ndarray:
    """Apply unsharp masking for compressed screen text."""
    blurred = cv2.GaussianBlur(img, (0, 0), sigma)
    sharpened = cv2.addWeighted(img, 1.0 + strength, blurred, -strength, 0)
    return np.clip(sharpened, 0, 255).astype(np.uint8)


def _apply_binary_threshold(img: np.ndarray) -> np.ndarray:
    """Binary threshold — good for clean black-on-white pages."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


def _apply_adaptive_threshold(img: np.ndarray) -> np.ndarray:
    """Adaptive threshold — handles uneven backgrounds."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    adaptive = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=11,
        C=2,
    )
    return cv2.cvtColor(adaptive, cv2.COLOR_GRAY2BGR)
