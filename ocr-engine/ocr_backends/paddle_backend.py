"""PaddleOCR 2.x backend — the required default backend."""

from __future__ import annotations

import gc
import threading
from typing import Optional

from .base import OCRBackend, OCRBox


class PaddleBackend(OCRBackend):
    """PaddleOCR 2.x adapter — wraps the existing lazy-loaded PaddleOCR logic."""

    _ocr_instances: dict = {}
    _lock = threading.Lock()

    @property
    def key(self) -> str:
        return "paddle"

    def is_available(self) -> tuple[bool, Optional[str]]:
        try:
            import paddleocr  # noqa: F401
            return True, None
        except ImportError:
            return False, "paddleocr is not installed. pip install paddleocr"

    def load(self, options: dict | None = None) -> None:
        options = options or {}
        lang = options.get("lang", "en")
        self._get_ocr(lang=lang)

    def unload(self) -> None:
        with self._lock:
            self._ocr_instances.clear()
        gc.collect()
        try:
            import paddle
            paddle.device.cuda.empty_cache()
        except Exception:
            pass

    def _get_ocr(self, lang: str = "en", use_angle_cls: bool = True,
                 det_db_thresh: float = 0.3):
        from paddleocr import PaddleOCR

        cache_key = (lang, use_angle_cls, det_db_thresh)
        with self._lock:
            if cache_key not in self._ocr_instances:
                self._ocr_instances[cache_key] = PaddleOCR(
                    use_angle_cls=use_angle_cls,
                    lang=lang,
                    det_db_thresh=det_db_thresh,
                )
            return self._ocr_instances[cache_key]

    def ocr_image(self, image_path: str, options: dict | None = None) -> list[OCRBox]:
        options = options or {}
        lang = options.get("lang", "en")
        use_angle_cls = options.get("use_angle_cls", True)
        det_db_thresh = float(options.get("det_db_thresh", 0.3))

        ocr = self._get_ocr(lang=lang, use_angle_cls=use_angle_cls,
                            det_db_thresh=det_db_thresh)
        result = ocr.ocr(image_path)

        boxes: list[OCRBox] = []
        if not result or not result[0]:
            return boxes

        for line in result[0]:
            box_coords = line[0]
            text = line[1][0]
            confidence = float(line[1][1])
            boxes.append(OCRBox(
                text=text,
                confidence=confidence,
                box=[[float(p[0]), float(p[1])] for p in box_coords],
                backend="paddle",
            ))

        return boxes

    # --- Legacy helpers for backward compat with server.py ---

    def boxes_to_layout_text(self, ocr_boxes: list[OCRBox]) -> str:
        """Convert OCRBox list into layout-preserved text."""
        if not ocr_boxes:
            return ""

        items = []
        for b in ocr_boxes:
            if b.box and len(b.box) >= 3:
                y_center = (b.box[0][1] + b.box[2][1]) / 2
                x_left = b.box[0][0]
            else:
                y_center = 0.0
                x_left = 0.0
            items.append((y_center, x_left, b.text, b.confidence or 0.0))

        items.sort(key=lambda it: (it[0], it[1]))

        line_groups: list[list] = []
        current_group = [items[0]]
        for item in items[1:]:
            if abs(item[0] - current_group[-1][0]) < 15:
                current_group.append(item)
            else:
                line_groups.append(current_group)
                current_group = [item]
        line_groups.append(current_group)

        lines = []
        for group in line_groups:
            group.sort(key=lambda it: it[1])
            line_text = "   ".join(it[2] for it in group)
            lines.append(line_text)

        return "\n".join(lines)

    def simple_text(self, ocr_boxes: list[OCRBox]) -> str:
        """Extract plain text from OCRBox list."""
        return "\n".join(b.text for b in ocr_boxes if b.text)

    def result_to_json(self, ocr_boxes: list[OCRBox], page_num: int = 0) -> list[dict]:
        """Convert OCRBox list into structured JSON dicts."""
        items = []
        for b in ocr_boxes:
            items.append({
                "page": page_num,
                "text": b.text,
                "confidence": round(b.confidence, 4) if b.confidence else 0.0,
                "box": b.box or [],
            })
        return items
