"""Deduplication for frames and OCR text lines."""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Frame deduplication (perceptual hash / dHash)
# ---------------------------------------------------------------------------

def _dhash(image_path: str, hash_size: int = 16) -> Optional[int]:
    """Compute a difference hash (dHash) for an image.

    Returns an integer hash, or None if the image cannot be read.
    """
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None

    resized = cv2.resize(img, (hash_size + 1, hash_size))
    diff = resized[:, 1:] > resized[:, :-1]

    # Convert boolean array to integer hash
    hash_val = 0
    for bit in diff.flatten():
        hash_val = (hash_val << 1) | int(bit)
    return hash_val


def _hamming_distance(hash1: int, hash2: int) -> int:
    """Compute Hamming distance between two integer hashes."""
    return bin(hash1 ^ hash2).count("1")


def dedupe_frames(
    frames: list[dict],
    threshold: int = 12,
    hash_size: int = 16,
) -> list[dict]:
    """Remove near-duplicate frames using perceptual hashing (dHash).

    Args:
        frames:    List of frame dicts with "path" key.
        threshold: Max Hamming distance to consider frames identical.
                   Lower = more aggressive deduplication.
        hash_size: dHash grid size (produces hash_size² bits).

    Returns:
        Filtered list of frame dicts with duplicates removed.
    """
    if not frames:
        return frames

    kept: list[dict] = []
    prev_hash: Optional[int] = None

    for frame in frames:
        frame_hash = _dhash(frame["path"], hash_size)
        if frame_hash is None:
            kept.append(frame)  # Keep unreadable frames (they'll fail later)
            continue

        if prev_hash is not None:
            dist = _hamming_distance(frame_hash, prev_hash)
            if dist <= threshold:
                log.debug("Skipping duplicate frame %s (dist=%d)", frame["path"], dist)
                continue

        prev_hash = frame_hash
        kept.append(frame)

    log.info("Frame dedupe: %d -> %d frames (threshold=%d)", len(frames), len(kept), threshold)
    return kept


# ---------------------------------------------------------------------------
# Text line deduplication
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """Normalize a text string for comparison.

    - Strip leading/trailing whitespace
    - Collapse internal whitespace
    - Lowercase
    - Normalize unicode
    - Remove common OCR artifacts
    """
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    text = text.lower()
    text = unicodedata.normalize("NFKC", text)
    # Remove isolated punctuation artifacts
    text = re.sub(r"^[^\w\s]+$", "", text)
    return text


def dedupe_lines(
    ocr_boxes: list,
    threshold: float = 85.0,
    use_fuzzy: bool = True,
) -> list:
    """Remove duplicate OCR lines using exact and fuzzy matching.

    Args:
        ocr_boxes: List of OCRBox objects (or dicts with "text" key).
        threshold: Fuzzy match ratio (0-100) above which lines are
                   considered duplicates. Only used if use_fuzzy is True.
        use_fuzzy: Whether to use rapidfuzz for fuzzy matching.
                   Falls back to exact matching if rapidfuzz is not available.

    Returns:
        Deduplicated list of OCR boxes, keeping the highest-confidence version.
    """
    if not ocr_boxes:
        return ocr_boxes

    # Import OCRBox for type checking
    from ocr_backends.base import OCRBox

    fuzz = None
    if use_fuzzy:
        try:
            from rapidfuzz import fuzz as _fuzz
            fuzz = _fuzz
        except ImportError:
            log.warning("rapidfuzz not installed; falling back to exact line dedupe")

    seen_texts: list[tuple[str, int]] = []  # (normalized_text, index_in_result)
    result: list = []

    for box in ocr_boxes:
        text = box.text if isinstance(box, OCRBox) else box.get("text", "")
        norm = normalize_text(text)

        if not norm:
            continue

        # Check for duplicates
        is_dup = False
        best_match_idx = -1

        for seen_norm, seen_idx in seen_texts:
            # Exact match
            if norm == seen_norm:
                is_dup = True
                best_match_idx = seen_idx
                break

            # Fuzzy match
            if fuzz is not None and len(norm) > 3 and len(seen_norm) > 3:
                ratio = fuzz.ratio(norm, seen_norm)
                if ratio >= threshold:
                    is_dup = True
                    best_match_idx = seen_idx
                    break

        if is_dup and best_match_idx >= 0:
            # Keep the version with higher confidence
            existing = result[best_match_idx]
            existing_conf = (
                existing.confidence if isinstance(existing, OCRBox)
                else existing.get("confidence", 0)
            ) or 0
            new_conf = (
                box.confidence if isinstance(box, OCRBox)
                else box.get("confidence", 0)
            ) or 0

            if new_conf > existing_conf:
                result[best_match_idx] = box
        else:
            seen_texts.append((norm, len(result)))
            result.append(box)

    log.info("Line dedupe: %d -> %d lines (threshold=%.0f)", len(ocr_boxes), len(result), threshold)
    return result
