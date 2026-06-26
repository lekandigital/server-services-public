"""Tesseract backend — classic CPU fallback via CLI or pytesseract."""

from __future__ import annotations

import csv
import io
import shutil
import subprocess
from typing import Optional

from .base import OCRBackend, OCRBox


class TesseractBackend(OCRBackend):
    """Tesseract OCR adapter — calls the system tesseract binary.

    Falls back to pytesseract if the binary is not found.
    No GPU required — useful as a baseline and emergency fallback.
    """

    @property
    def key(self) -> str:
        return "tesseract"

    def is_available(self) -> tuple[bool, Optional[str]]:
        # Check for tesseract binary
        if shutil.which("tesseract"):
            return True, None

        # Check for pytesseract
        try:
            import pytesseract  # noqa: F401
            return True, None
        except ImportError:
            pass

        return False, (
            "tesseract is not installed. "
            "Install system package: sudo apt-get install -y tesseract-ocr tesseract-ocr-eng "
            "or: pip install pytesseract"
        )

    def load(self, options: dict | None = None) -> None:
        pass  # Tesseract is a CLI tool, no model to pre-load

    def unload(self) -> None:
        pass  # Nothing to unload

    def _tesseract_lang(self, lang: str) -> str:
        """Map PaddleOCR lang codes to Tesseract lang codes."""
        mapping = {
            "en": "eng", "ch": "chi_sim", "chinese_cht": "chi_tra",
            "fr": "fra", "german": "deu", "ja": "jpn", "ko": "kor",
            "ar": "ara", "hi": "hin", "es": "spa", "pt": "por",
            "it": "ita", "nl": "nld", "rsc": "rus", "uk": "ukr",
            "pl": "pol", "tr": "tur", "vi": "vie", "th": "tha",
        }
        return mapping.get(lang, "eng")

    def ocr_image(self, image_path: str, options: dict | None = None) -> list[OCRBox]:
        options = options or {}
        lang = options.get("lang", "en")
        tess_lang = self._tesseract_lang(lang)

        # Try CLI first (faster, no Python overhead)
        if shutil.which("tesseract"):
            return self._ocr_via_cli(image_path, tess_lang)

        # Fallback to pytesseract
        return self._ocr_via_pytesseract(image_path, tess_lang)

    def _ocr_via_cli(self, image_path: str, tess_lang: str) -> list[OCRBox]:
        """Run tesseract CLI and parse TSV output."""
        try:
            result = subprocess.run(
                [
                    "tesseract", image_path, "stdout",
                    "-l", tess_lang,
                    "--psm", "3",  # Fully automatic page segmentation
                    "tsv",
                ],
                capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            return []

        if result.returncode != 0:
            return []

        return self._parse_tsv(result.stdout)

    def _ocr_via_pytesseract(self, image_path: str, tess_lang: str) -> list[OCRBox]:
        """Fallback using pytesseract Python binding."""
        import pytesseract
        from PIL import Image

        img = Image.open(image_path)
        tsv_data = pytesseract.image_to_data(
            img, lang=tess_lang, output_type=pytesseract.Output.STRING
        )
        return self._parse_tsv(tsv_data)

    def _parse_tsv(self, tsv_text: str) -> list[OCRBox]:
        """Parse Tesseract TSV output into OCRBox list."""
        boxes: list[OCRBox] = []
        reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")

        for row in reader:
            try:
                text = row.get("text", "").strip()
                conf = float(row.get("conf", -1))
                if not text or conf < 0:
                    continue

                left = float(row.get("left", 0))
                top = float(row.get("top", 0))
                width = float(row.get("width", 0))
                height = float(row.get("height", 0))

                box = [
                    [left, top],
                    [left + width, top],
                    [left + width, top + height],
                    [left, top + height],
                ]

                boxes.append(OCRBox(
                    text=text,
                    confidence=conf / 100.0,  # Tesseract uses 0-100 scale
                    box=box,
                    backend="tesseract",
                ))
            except (ValueError, KeyError):
                continue

        return boxes
