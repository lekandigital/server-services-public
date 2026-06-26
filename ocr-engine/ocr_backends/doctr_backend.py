"""docTR backend — PyTorch document OCR with detection + recognition."""

from __future__ import annotations

import gc
import threading
from typing import Optional

from .base import OCRBackend, OCRBox


class DocTRBackend(OCRBackend):
    """docTR adapter — document OCR with detection + recognition pipeline.

    Good for clean documents, screenshots, and PDF-like pages.
    """

    _predictor = None
    _lock = threading.Lock()

    @property
    def key(self) -> str:
        return "doctr"

    def is_available(self) -> tuple[bool, Optional[str]]:
        try:
            import doctr  # noqa: F401
            return True, None
        except ImportError:
            return False, (
                "python-doctr is not installed. "
                "Install requirements-extra-ocr.txt or: pip install python-doctr[torch]"
            )

    def load(self, options: dict | None = None) -> None:
        available, err = self.is_available()
        if not available:
            raise RuntimeError(err)
        self._ensure_predictor()

    def unload(self) -> None:
        with self._lock:
            self._predictor = None
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass

    def _ensure_predictor(self):
        if self._predictor is not None:
            return

        with self._lock:
            if self._predictor is not None:
                return

            from doctr.models import ocr_predictor
            self._predictor = ocr_predictor(
                det_arch="db_resnet50",
                reco_arch="crnn_vgg16_bn",
                pretrained=True,
            )
            # Move to GPU if available
            try:
                import torch
                if torch.cuda.is_available():
                    self._predictor = self._predictor.cuda()
            except Exception:
                pass

    def ocr_image(self, image_path: str, options: dict | None = None) -> list[OCRBox]:
        options = options or {}
        available, err = self.is_available()
        if not available:
            raise RuntimeError(err)

        self._ensure_predictor()

        from doctr.io import DocumentFile
        doc = DocumentFile.from_images(image_path)
        result = self._predictor(doc)

        boxes: list[OCRBox] = []
        for page in result.pages:
            page_w, page_h = page.dimensions[1], page.dimensions[0]  # (height, width)
            for block in page.blocks:
                for line in block.lines:
                    # Concatenate words in the line
                    line_text = " ".join(w.value for w in line.words)
                    line_conf = (
                        sum(w.confidence for w in line.words) / len(line.words)
                        if line.words else None
                    )

                    # Convert relative coords to absolute
                    if line.geometry:
                        (x1, y1), (x2, y2) = line.geometry
                        abs_box = [
                            [x1 * page_w, y1 * page_h],
                            [x2 * page_w, y1 * page_h],
                            [x2 * page_w, y2 * page_h],
                            [x1 * page_w, y2 * page_h],
                        ]
                    else:
                        abs_box = None

                    boxes.append(OCRBox(
                        text=line_text,
                        confidence=float(line_conf) if line_conf is not None else None,
                        box=abs_box,
                        backend="doctr",
                    ))

        return boxes
