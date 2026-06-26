"""EasyOCR backend — PyTorch fallback / scene text."""

from __future__ import annotations

import gc
import threading
from typing import Optional

from .base import OCRBackend, OCRBox

# EasyOCR lang codes differ from PaddleOCR — map common ones
_LANG_MAP = {
    "en": "en",
    "ch": "ch_sim",
    "chinese_cht": "ch_tra",
    "fr": "fr",
    "german": "de",
    "ja": "ja",
    "ko": "ko",
    "ar": "ar",
    "hi": "hi",
    "es": "es",
    "pt": "pt",
    "it": "it",
    "nl": "nl",
    "tr": "tr",
    "vi": "vi",
    "th": "th",
    "id": "id",
    "ms": "ms",
    "rsc": "ru",
    "uk": "uk",
    "pl": "pl",
}


class EasyOCRBackend(OCRBackend):
    """EasyOCR adapter — good secondary engine for scene text and cross-checking."""

    _readers: dict = {}
    _lock = threading.Lock()

    @property
    def key(self) -> str:
        return "easyocr"

    def is_available(self) -> tuple[bool, Optional[str]]:
        try:
            import easyocr  # noqa: F401
            return True, None
        except ImportError:
            return False, (
                "easyocr is not installed. "
                "Install requirements-extra-ocr.txt or: pip install easyocr"
            )

    def load(self, options: dict | None = None) -> None:
        options = options or {}
        lang = options.get("lang", "en")
        self._get_reader(lang)

    def unload(self) -> None:
        with self._lock:
            self._readers.clear()
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass

    def _get_reader(self, lang: str = "en"):
        import easyocr

        mapped = _LANG_MAP.get(lang, lang)
        lang_list = [mapped] if mapped != "en" else ["en"]
        if mapped != "en" and "en" not in lang_list:
            lang_list.append("en")

        cache_key = tuple(sorted(lang_list))
        with self._lock:
            if cache_key not in self._readers:
                self._readers[cache_key] = self._create_reader(lang_list)
            return self._readers[cache_key]

    def _create_reader(self, lang_list: list[str], *, force_cpu: bool = False):
        """Create EasyOCR reader, falling back to CPU on GPU/cuDNN conflicts."""
        import easyocr

        if force_cpu:
            return easyocr.Reader(lang_list, gpu=False, verbose=False)

        gpu = False
        try:
            import torch
            gpu = torch.cuda.is_available()
        except Exception:
            pass

        if gpu:
            try:
                return easyocr.Reader(lang_list, gpu=True, verbose=False)
            except Exception:
                pass

        return easyocr.Reader(lang_list, gpu=False, verbose=False)

    def ocr_image(self, image_path: str, options: dict | None = None) -> list[OCRBox]:
        options = options or {}
        lang = options.get("lang", "en")

        reader = self._get_reader(lang)
        try:
            results = reader.readtext(image_path)
        except Exception as exc:
            err = str(exc).lower()
            if "cudnn" not in err and "cuda" not in err:
                raise
            # Paddle may load a system cuDNN that conflicts with PyTorch CUDA.
            with self._lock:
                mapped = _LANG_MAP.get(lang, lang)
                lang_list = [mapped] if mapped != "en" else ["en"]
                if mapped != "en" and "en" not in lang_list:
                    lang_list.append("en")
                cache_key = tuple(sorted(lang_list))
                self._readers[cache_key] = self._create_reader(lang_list, force_cpu=True)
                reader = self._readers[cache_key]
            results = reader.readtext(image_path)

        boxes: list[OCRBox] = []
        for bbox, text, confidence in results:
            # EasyOCR bbox is [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            box = [[float(p[0]), float(p[1])] for p in bbox]
            boxes.append(OCRBox(
                text=text,
                confidence=float(confidence),
                box=box,
                backend="easyocr",
            ))

        return boxes
