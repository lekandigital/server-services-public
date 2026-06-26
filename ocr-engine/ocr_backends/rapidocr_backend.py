"""RapidOCR backend — ONNX-based fast OCR engine."""

from __future__ import annotations

import threading
from typing import Optional

from .base import OCRBackend, OCRBox


class RapidOCRBackend(OCRBackend):
    """RapidOCR adapter — fast engineering-focused OCR via ONNX runtime.

    Useful as a speed backend for batch OCR and ONNX/TensorRT experiments.
    """

    _engine = None
    _lock = threading.Lock()

    @property
    def key(self) -> str:
        return "rapidocr"

    def is_available(self) -> tuple[bool, Optional[str]]:
        try:
            import rapidocr_onnxruntime  # noqa: F401
            return True, None
        except ImportError:
            pass
        try:
            import rapidocr  # noqa: F401
            return True, None
        except ImportError:
            pass
        return False, (
            "rapidocr is not installed. "
            "Install requirements-extra-ocr.txt or: pip install rapidocr-onnxruntime"
        )

    def load(self, options: dict | None = None) -> None:
        available, err = self.is_available()
        if not available:
            raise RuntimeError(err)
        self._ensure_engine()

    def unload(self) -> None:
        with self._lock:
            self._engine = None

    def _ensure_engine(self):
        if self._engine is not None:
            return

        with self._lock:
            if self._engine is not None:
                return

            try:
                from rapidocr_onnxruntime import RapidOCR
                self._engine = RapidOCR()
            except ImportError:
                from rapidocr import RapidOCR
                self._engine = RapidOCR()

    def ocr_image(self, image_path: str, options: dict | None = None) -> list[OCRBox]:
        options = options or {}
        available, err = self.is_available()
        if not available:
            raise RuntimeError(err)

        self._ensure_engine()

        result, elapse = self._engine(image_path)

        boxes: list[OCRBox] = []
        if not result:
            return boxes

        for item in result:
            # RapidOCR returns: [box_coords, text, confidence]
            if len(item) >= 3:
                box_coords, text, confidence = item[0], item[1], item[2]
            elif len(item) == 2:
                text, confidence = item[0], item[1]
                box_coords = None
            else:
                continue

            box = None
            if box_coords is not None:
                try:
                    box = [[float(p[0]), float(p[1])] for p in box_coords]
                except (TypeError, IndexError):
                    box = None

            boxes.append(OCRBox(
                text=str(text),
                confidence=float(confidence) if confidence is not None else None,
                box=box,
                backend="rapidocr",
            ))

        return boxes
