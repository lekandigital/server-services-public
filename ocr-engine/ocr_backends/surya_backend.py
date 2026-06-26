"""Surya 2 backend — heavy document/layout OCR (optional)."""

from __future__ import annotations

import gc
import re
import threading
from html import unescape
from typing import Optional

from .base import OCRBackend, OCRBox

_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    text = _TAG_RE.sub(" ", html)
    return " ".join(unescape(text).split())


class SuryaBackend(OCRBackend):
    """Surya 2 OCR adapter — large model for max-quality document extraction.

    Provides OCR, layout analysis, reading order, and table recognition.
    Model weights have separate commercial-use terms.
    """

    _rec_predictor = None
    _model = None
    _lock = threading.Lock()

    @property
    def key(self) -> str:
        return "surya"

    def _vllm_image_ready(self) -> tuple[bool, Optional[str]]:
        import subprocess

        try:
            from surya.settings import settings
            image = settings.VLLM_DOCKER_IMAGE
        except Exception:
            image = "vllm/vllm-openai:v0.20.1"

        try:
            r = subprocess.run(
                ["docker", "images", "-q", image],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                return True, None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False, (
                "Docker is required for Surya 2. Install Docker and pull: "
                f"docker pull {image}"
            )

        return False, (
            f"Surya 2 requires the vLLM Docker image. Pull it first: "
            f"docker pull {image}"
        )

    def is_available(self) -> tuple[bool, Optional[str]]:
        try:
            import surya  # noqa: F401
            from surya.recognition import RecognitionPredictor  # noqa: F401
        except ImportError:
            return False, (
                "surya-ocr is not installed. "
                "Install requirements-extra-ocr.txt or: pip install surya-ocr"
            )
        return self._vllm_image_ready()

    def load(self, options: dict | None = None) -> None:
        available, err = self.is_available()
        if not available:
            raise RuntimeError(err)
        self._ensure_model()

    def unload(self) -> None:
        with self._lock:
            self._rec_predictor = None
            self._model = None
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass

    def _ensure_model(self):
        if self._rec_predictor is not None:
            return

        with self._lock:
            if self._rec_predictor is not None:
                return

            try:
                # Surya 0.20+ — full-page VLM OCR via RecognitionPredictor.
                from surya.inference import SuryaInferenceManager
                from surya.recognition import RecognitionPredictor

                manager = SuryaInferenceManager()
                self._rec_predictor = RecognitionPredictor(manager)
                self._model = "surya2"
            except ImportError as exc:
                raise RuntimeError(f"Failed to load Surya models: {exc}")

    def _boxes_from_page(self, page) -> list[OCRBox]:
        boxes: list[OCRBox] = []

        if hasattr(page, "text_lines"):
            for line in page.text_lines:
                text = line.text if hasattr(line, "text") else str(line)
                conf = line.confidence if hasattr(line, "confidence") else None
                bbox = None
                if hasattr(line, "bbox"):
                    b = line.bbox
                    bbox = [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]
                boxes.append(OCRBox(
                    text=text,
                    confidence=float(conf) if conf is not None else None,
                    box=bbox,
                    backend="surya",
                ))
            return boxes

        for block in getattr(page, "blocks", []) or []:
            if getattr(block, "skipped", False):
                continue
            text = _html_to_text(getattr(block, "html", "") or "")
            if not text.strip():
                continue
            conf = getattr(block, "confidence", None)
            polygon = getattr(block, "polygon", None)
            bbox = None
            if polygon:
                xs = [p[0] for p in polygon]
                ys = [p[1] for p in polygon]
                bbox = [
                    [min(xs), min(ys)],
                    [max(xs), min(ys)],
                    [max(xs), max(ys)],
                    [min(xs), max(ys)],
                ]
            boxes.append(OCRBox(
                text=text,
                confidence=float(conf) if conf is not None else None,
                box=bbox,
                backend="surya",
            ))

        return boxes

    def ocr_image(self, image_path: str, options: dict | None = None) -> list[OCRBox]:
        options = options or {}
        available, err = self.is_available()
        if not available:
            raise RuntimeError(err)

        self._ensure_model()

        from PIL import Image
        img = Image.open(image_path).convert("RGB")

        if self._model == "surya2":
            try:
                results = self._rec_predictor([img], full_page=True)
                boxes: list[OCRBox] = []
                for page in results:
                    boxes.extend(self._boxes_from_page(page))
                return boxes
            except Exception as exc:
                raise RuntimeError(f"Surya OCR failed: {exc}")

        return []
