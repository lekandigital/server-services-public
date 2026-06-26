"""PaddleOCR 3.x / PP-OCRv5 backend — optional newer high-accuracy backend."""

from __future__ import annotations

import gc
import threading
from typing import Optional

from .base import OCRBackend, OCRBox


class Paddle3Backend(OCRBackend):
    """PaddleOCR 3.x / PP-OCRv5 / PaddleX adapter.

    This backend attempts to use the newer PaddleX or PaddleOCR 3.x APIs
    which provide PP-OCRv5, PP-StructureV3, and PaddleOCR-VL capabilities.
    If neither is installed, it reports itself as unavailable.
    """

    _model = None
    _lock = threading.Lock()

    @property
    def key(self) -> str:
        return "paddle3"

    def is_available(self) -> tuple[bool, Optional[str]]:
        # Try paddlex first (PP-OCRv5 path)
        try:
            import paddlex  # noqa: F401
            return True, None
        except ImportError:
            pass

        # Try newer paddleocr >= 3.x
        try:
            import paddleocr
            ver = getattr(paddleocr, "__version__", "0.0.0")
            major = int(ver.split(".")[0])
            if major >= 3:
                return True, None
            return False, (
                f"PaddleOCR {ver} is installed but v3.x+ is required. "
                "Install paddlex or upgrade paddleocr for PP-OCRv5 support."
            )
        except (ImportError, ValueError):
            return False, (
                "Neither paddlex nor paddleocr 3.x is installed. "
                "pip install paddlex or upgrade paddleocr for PP-OCRv5."
            )

    def load(self, options: dict | None = None) -> None:
        available, err = self.is_available()
        if not available:
            raise RuntimeError(err)
        # Lazy-load on first OCR call
        pass

    def unload(self) -> None:
        with self._lock:
            self._model = None
        gc.collect()
        try:
            import paddle
            paddle.device.cuda.empty_cache()
        except Exception:
            pass

    def _ensure_model(self, options: dict):
        """Lazy-load the PaddleX or PaddleOCR 3.x model."""
        if self._model is not None:
            return

        with self._lock:
            if self._model is not None:
                return

            # Try PaddleX pipeline first
            try:
                from paddlex import create_pipeline
                self._model = create_pipeline(pipeline="OCR")
                self._model_type = "paddlex"
                return
            except Exception:
                pass

            # Fallback to PaddleOCR 3.x
            try:
                from paddleocr import PaddleOCR
                self._model = PaddleOCR(
                    use_angle_cls=True,
                    lang=options.get("lang", "en"),
                    ocr_version="PP-OCRv5",
                )
                self._model_type = "paddleocr3"
                return
            except Exception:
                pass

            raise RuntimeError(
                "Failed to initialize PaddleOCR 3.x / PP-OCRv5. "
                "Check paddlex or paddleocr installation."
            )

    def ocr_image(self, image_path: str, options: dict | None = None) -> list[OCRBox]:
        options = options or {}
        available, err = self.is_available()
        if not available:
            raise RuntimeError(err)

        self._ensure_model(options)
        boxes: list[OCRBox] = []

        if self._model_type == "paddlex":
            # PaddleX pipeline output
            try:
                output = self._model.predict(image_path)
                for res in output:
                    if hasattr(res, "rec_texts"):
                        texts = res.rec_texts if hasattr(res, "rec_texts") else []
                        scores = res.rec_scores if hasattr(res, "rec_scores") else []
                        det_boxes = res.dt_polys if hasattr(res, "dt_polys") else []
                        for i, text in enumerate(texts):
                            conf = float(scores[i]) if i < len(scores) else None
                            box = det_boxes[i].tolist() if i < len(det_boxes) else None
                            boxes.append(OCRBox(
                                text=str(text),
                                confidence=conf,
                                box=box,
                                backend="paddle3",
                            ))
            except Exception as exc:
                raise RuntimeError(f"PP-OCRv5 pipeline failed: {exc}")

        elif self._model_type == "paddleocr3":
            # PaddleOCR 3.x API (similar to 2.x)
            result = self._model.ocr(image_path)
            if result and result[0]:
                for line in result[0]:
                    box_coords = line[0]
                    text = line[1][0]
                    confidence = float(line[1][1])
                    boxes.append(OCRBox(
                        text=text,
                        confidence=confidence,
                        box=[[float(p[0]), float(p[1])] for p in box_coords],
                        backend="paddle3",
                    ))

        return boxes
