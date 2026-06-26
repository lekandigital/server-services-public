"""TrOCR backend — transformer line-level recognizer."""

from __future__ import annotations

import gc
import threading
from typing import Optional

from .base import OCRBackend, OCRBox


class TrOCRBackend(OCRBackend):
    """TrOCR adapter — HuggingFace transformer for line-level text recognition.

    This is NOT a full-page detector. It recognizes text from pre-cropped
    line images. Only useful in ensemble mode when another detector provides
    line crops. If used standalone, it will attempt basic detection using
    a simple contour approach, but results will be limited.
    """

    _processor = None
    _model = None
    _lock = threading.Lock()
    _model_id = "microsoft/trocr-large-printed"

    @property
    def key(self) -> str:
        return "trocr"

    def is_available(self) -> tuple[bool, Optional[str]]:
        try:
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel  # noqa: F401
            return True, None
        except ImportError:
            return False, (
                "transformers is not installed. "
                "Install requirements-extra-ocr.txt or: pip install transformers"
            )

    def load(self, options: dict | None = None) -> None:
        available, err = self.is_available()
        if not available:
            raise RuntimeError(err)
        self._ensure_model()

    def unload(self) -> None:
        with self._lock:
            self._processor = None
            self._model = None
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass

    def _ensure_model(self):
        if self._model is not None:
            return

        with self._lock:
            if self._model is not None:
                return

            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
            import torch

            self._processor = TrOCRProcessor.from_pretrained(self._model_id)
            model = VisionEncoderDecoderModel.from_pretrained(self._model_id)

            device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model = model.to(device).eval()
            self._device = device

    def ocr_line_image(self, image_path: str, options: dict | None = None) -> list[OCRBox]:
        """Recognize a single pre-cropped text line image."""
        options = options or {}
        self._ensure_model()

        from PIL import Image
        import torch

        img = Image.open(image_path).convert("RGB")
        pixel_values = self._processor(images=img, return_tensors="pt").pixel_values
        pixel_values = pixel_values.to(self._device)

        with torch.inference_mode():
            generated_ids = self._model.generate(pixel_values)

        text = self._processor.batch_decode(generated_ids, skip_special_tokens=True)[0]

        return [OCRBox(
            text=text.strip(),
            confidence=None,  # TrOCR doesn't provide per-line confidence easily
            box=None,
            backend="trocr",
        )]

    def ocr_image(self, image_path: str, options: dict | None = None) -> list[OCRBox]:
        """Attempt full-page OCR using simple line detection + TrOCR recognition.

        This is a basic approach since TrOCR is a line recognizer.
        For best results, use in ensemble mode where another detector
        provides line crops.
        """
        options = options or {}
        self._ensure_model()

        import cv2
        import numpy as np
        from PIL import Image
        import torch

        img_cv = cv2.imread(image_path)
        if img_cv is None:
            return []

        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        # Simple line detection via horizontal projection
        # Binarize
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # Find contours for text regions
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (w // 4, 3))
        dilated = cv2.dilate(binary, kernel, iterations=1)
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Extract bounding boxes and sort by y-coordinate
        line_boxes = []
        for cnt in contours:
            x, y, bw, bh = cv2.boundingRect(cnt)
            if bw > w * 0.05 and bh > 5:  # Filter tiny regions
                line_boxes.append((x, y, bw, bh))

        line_boxes.sort(key=lambda b: b[1])

        boxes: list[OCRBox] = []
        for x, y, bw, bh in line_boxes[:100]:  # Limit to 100 lines
            # Crop line with padding
            pad = max(4, bh // 4)
            y1 = max(0, y - pad)
            y2 = min(h, y + bh + pad)
            x1 = max(0, x - pad)
            x2 = min(w, x + bw + pad)

            crop = img_cv[y1:y2, x1:x2]
            crop_pil = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))

            pixel_values = self._processor(images=crop_pil, return_tensors="pt").pixel_values
            pixel_values = pixel_values.to(self._device)

            with torch.inference_mode():
                generated_ids = self._model.generate(pixel_values)

            text = self._processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
            text = text.strip()

            if text:
                box = [
                    [float(x1), float(y1)],
                    [float(x2), float(y1)],
                    [float(x2), float(y2)],
                    [float(x1), float(y2)],
                ]
                boxes.append(OCRBox(
                    text=text,
                    confidence=None,
                    box=box,
                    backend="trocr",
                ))

        return boxes
