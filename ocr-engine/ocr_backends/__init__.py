"""OCR Backend Registry — modeled after Image Studio's BACKGROUND_MODELS.

Provides:
  - OCR_BACKENDS            – metadata for every backend
  - VIDEO_OCR_QUALITY_MODES – standard / high / max
  - VIDEO_OCR_STRATEGIES    – scrolling-page / slides / document-camera / full-archive
  - OCR_ENGINE_MODES        – single / cascade / consensus / maximum-recall
  - PREPROCESS_VARIANTS     – preprocessing variant definitions
  - get_backend(key)        – lazy-import factory
  - check_all_backends()    – bulk availability check
"""

from __future__ import annotations

import importlib
import threading
from typing import Optional

from .base import OCRBackend, OCRBox  # noqa: F401  — re-export

# ---------------------------------------------------------------------------
# Backend registry
# ---------------------------------------------------------------------------
OCR_BACKENDS = {
    "paddle": {
        "label": "PaddleOCR 2.x — current stable backend",
        "desc": "Existing backend, fast and reliable for screen text, images, PDFs.",
        "gpu": True,
        "optional": False,
        "license_note": "Apache-2.0",
        "best_for": ["screen recordings", "documents", "multilingual text"],
    },
    "paddle3": {
        "label": "PaddleOCR 3.x / PP-OCRv5 — newer high-accuracy backend",
        "desc": "Use PP-OCRv5 / PP-StructureV3 / PaddleOCR-VL where available.",
        "gpu": True,
        "optional": True,
        "license_note": "Apache-2.0; check model cards for any model-specific terms.",
        "best_for": ["high accuracy", "layout", "tables", "mixed documents"],
    },
    "easyocr": {
        "label": "EasyOCR — PyTorch fallback / scene text",
        "desc": "Good secondary OCR engine for recall and cross-checking.",
        "gpu": True,
        "optional": True,
        "license_note": "Apache-2.0",
        "best_for": ["scene text", "fallback", "multilingual"],
    },
    "surya": {
        "label": "Surya 2 — heavy document/layout OCR",
        "desc": "Large OCR/layout/table model. Use for max-quality document extraction.",
        "gpu": True,
        "optional": True,
        "license_note": "Code Apache-2.0; model weights have separate commercial-use terms.",
        "best_for": ["layout", "tables", "reading order", "max quality"],
    },
    "doctr": {
        "label": "docTR — PyTorch document OCR",
        "desc": "Document OCR backend with detection + recognition.",
        "gpu": True,
        "optional": True,
        "license_note": "Apache-2.0",
        "best_for": ["documents", "clean screenshots", "PDF-like pages"],
    },
    "rapidocr": {
        "label": "RapidOCR — ONNX/TensorRT/Paddle fast backend",
        "desc": "Fast engineering-focused OCR path; useful as a speed backend.",
        "gpu": True,
        "optional": True,
        "license_note": "Apache-2.0; model copyright notes apply.",
        "best_for": ["fast batch OCR", "ONNX/TensorRT experiments"],
    },
    "tesseract": {
        "label": "Tesseract — classic CPU fallback",
        "desc": "Useful baseline and emergency fallback. Not GPU-heavy.",
        "gpu": False,
        "optional": True,
        "license_note": "Apache-2.0",
        "best_for": ["fallback", "debugging", "simple text"],
    },
    "trocr": {
        "label": "TrOCR — transformer recognizer for cropped lines",
        "desc": "Recognition model for detected/cropped lines; not a full detector by itself.",
        "gpu": True,
        "optional": True,
        "license_note": "MIT / model-card-dependent; check checkpoint terms.",
        "best_for": ["cropped lines", "handwriting", "line-level ensemble"],
    },
}

# ---------------------------------------------------------------------------
# Quality modes — biased toward RTX 3090 Ti (24 GB VRAM)
# ---------------------------------------------------------------------------
VIDEO_OCR_QUALITY_MODES = {
    "standard": {
        "label": "Standard",
        "desc": "Fast extraction with a single backend.",
        "fps": 2,
        "max_width": 1920,
        "scales": [1.0],
        "preprocess_variants": ["native"],
        "frame_dedupe": True,
        "line_dedupe": True,
        "ensemble": False,
        "engine_mode": "single",
        "primary_backend": "paddle",
        "fallback_backends": [],
        "secondary_backends": [],
    },
    "high": {
        "label": "High",
        "desc": "Multi-scale cascade with fallback backends. Recommended for 3090 Ti.",
        "fps": 4,
        "max_width": 2560,
        "scales": [1.0, 1.5],
        "preprocess_variants": ["native", "sharpen", "contrast"],
        "frame_dedupe": True,
        "line_dedupe": True,
        "ensemble": True,
        "engine_mode": "cascade",
        "primary_backend": "paddle",
        "fallback_backends": ["easyocr", "tesseract"],
        "secondary_backends": [],
    },
    "max": {
        "label": "Max / 3090 Ti",
        "desc": "Maximum recall through multiple backends, scales, and preprocessing. Uses most available VRAM.",
        "fps": 6,
        "max_width": 3840,
        "scales": [1.0, 1.5, 2.0],
        "preprocess_variants": [
            "native",
            "sharpen",
            "contrast",
            "grayscale",
            "adaptive_threshold",
        ],
        "frame_dedupe": False,
        "line_dedupe": True,
        "ensemble": True,
        "engine_mode": "maximum-recall",
        "primary_backend": "paddle",
        "fallback_backends": [],
        "secondary_backends": ["easyocr", "doctr", "surya", "tesseract"],
    },
}

# ---------------------------------------------------------------------------
# Video OCR strategies
# ---------------------------------------------------------------------------
VIDEO_OCR_STRATEGIES = {
    "scrolling-page": {
        "label": "Scrolling Page / Chat / Webpage",
        "desc": "Best for screen recordings where text moves vertically.",
        "dedupe_lines": True,
        "dedupe_frames": True,
        "preserve_frame_markers": False,
        "prefer_plain_text": True,
    },
    "slides": {
        "label": "Slides / Presentation",
        "desc": "Dedupes stable frames aggressively and keeps slide boundaries.",
        "dedupe_lines": True,
        "dedupe_frames": True,
        "preserve_frame_markers": True,
    },
    "document-camera": {
        "label": "Document Camera / Physical Paper",
        "desc": "Uses deblur, contrast, thresholding, deskew, and layout preservation.",
        "dedupe_lines": True,
        "dedupe_frames": True,
        "preprocess_heavy": True,
        "preserve_layout": True,
        "preserve_frame_markers": True,
    },
    "full-archive": {
        "label": "Full Archive",
        "desc": "Keeps every frame's OCR with timestamps. Least deduping.",
        "dedupe_lines": False,
        "dedupe_frames": False,
        "preserve_frame_markers": True,
    },
}

# ---------------------------------------------------------------------------
# Engine modes
# ---------------------------------------------------------------------------
OCR_ENGINE_MODES = {
    "single": {
        "label": "Single",
        "desc": "Run one selected backend.",
    },
    "cascade": {
        "label": "Cascade",
        "desc": "Run primary backend; use fallback only on low-confidence frames.",
    },
    "consensus": {
        "label": "Consensus",
        "desc": "Run multiple backends and merge lines by confidence/fuzzy similarity.",
    },
    "maximum-recall": {
        "label": "Maximum Recall",
        "desc": "Run multiple backends, multiple scales, and keep all unique lines.",
    },
}

# ---------------------------------------------------------------------------
# Preprocessing variants
# ---------------------------------------------------------------------------
PREPROCESS_VARIANTS = {
    "native": "No modification.",
    "grayscale": "Convert to grayscale.",
    "contrast": "CLAHE / local contrast boost.",
    "sharpen": "Unsharp mask for compressed screen text.",
    "threshold": "Binary threshold for black-on-white pages.",
    "adaptive_threshold": "Adaptive threshold for uneven backgrounds.",
    "upscale_2x": "Lanczos upscaling (2×).",
}

# ---------------------------------------------------------------------------
# Backend instance cache  (lazy-loaded, thread-safe)
# ---------------------------------------------------------------------------
_backend_instances: dict[str, OCRBackend] = {}
_backend_lock = threading.Lock()

# Module paths for lazy import
_BACKEND_MODULES = {
    "paddle": ".paddle_backend",
    "paddle3": ".paddle3_backend",
    "easyocr": ".easyocr_backend",
    "surya": ".surya_backend",
    "doctr": ".doctr_backend",
    "rapidocr": ".rapidocr_backend",
    "tesseract": ".tesseract_backend",
    "trocr": ".trocr_backend",
}

# Class names inside each module
_BACKEND_CLASSES = {
    "paddle": "PaddleBackend",
    "paddle3": "Paddle3Backend",
    "easyocr": "EasyOCRBackend",
    "surya": "SuryaBackend",
    "doctr": "DocTRBackend",
    "rapidocr": "RapidOCRBackend",
    "tesseract": "TesseractBackend",
    "trocr": "TrOCRBackend",
}


def get_backend(key: str) -> OCRBackend:
    """Lazily import and instantiate a backend by key.

    Raises KeyError if the key is unknown.
    """
    if key not in OCR_BACKENDS:
        raise KeyError(f"Unknown OCR backend: {key}")

    with _backend_lock:
        if key not in _backend_instances:
            mod_path = _BACKEND_MODULES[key]
            cls_name = _BACKEND_CLASSES[key]
            mod = importlib.import_module(mod_path, package=__package__)
            cls = getattr(mod, cls_name)
            _backend_instances[key] = cls()
        return _backend_instances[key]


def check_all_backends() -> dict[str, dict]:
    """Check availability of every registered backend.

    Returns ``{key: {"available": bool, "error": str|None, ...metadata}}``
    """
    results = {}
    for key, meta in OCR_BACKENDS.items():
        try:
            backend = get_backend(key)
            available, err = backend.is_available()
        except Exception as exc:
            available, err = False, str(exc)
        results[key] = {
            **meta,
            "available": available,
            "error": err,
        }
    return results
