"""Base classes and shared data structures for OCR backends."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class OCRBox:
    """Normalized OCR output — every backend must produce this."""

    text: str
    confidence: Optional[float] = None
    box: Optional[list] = None  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] or similar
    backend: str = ""
    frame_index: Optional[int] = None
    timestamp: Optional[float] = None
    variant: Optional[str] = None
    scale: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


class OCRBackend(abc.ABC):
    """Abstract base for every OCR backend adapter.

    Subclasses must implement:
      - key (property)
      - is_available()
      - load()
      - unload()
      - ocr_image()
    """

    @property
    @abc.abstractmethod
    def key(self) -> str:
        """Unique registry key, e.g. 'paddle', 'easyocr'."""
        ...

    @abc.abstractmethod
    def is_available(self) -> tuple[bool, Optional[str]]:
        """Check whether this backend's dependencies are installed.

        Returns (True, None) if available, or (False, error_message) if not.
        """
        ...

    @abc.abstractmethod
    def load(self, options: dict | None = None) -> None:
        """Pre-load models / warm up the backend."""
        ...

    @abc.abstractmethod
    def unload(self) -> None:
        """Release models and free VRAM/RAM."""
        ...

    @abc.abstractmethod
    def ocr_image(self, image_path: str, options: dict | None = None) -> list[OCRBox]:
        """Run OCR on a single image file.

        Args:
            image_path: Absolute path to an image file (PNG/JPG/etc.).
            options:    Dict of backend-specific knobs (lang, threshold, …).

        Returns:
            List of OCRBox results.
        """
        ...

    def ocr_line_image(self, image_path: str, options: dict | None = None) -> list[OCRBox]:
        """Optional: Recognize a pre-cropped text-line image.

        Only meaningful for line-level recognizers like TrOCR.
        Default implementation falls back to ocr_image().
        """
        return self.ocr_image(image_path, options)
