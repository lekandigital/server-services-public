"""Multi-backend consensus and result merging for OCR."""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)


def merge_results(
    results_by_backend: dict[str, list],
    mode: str = "single",
    fuzzy_threshold: float = 85.0,
) -> list:
    """Merge OCR results from multiple backends according to the engine mode.

    Args:
        results_by_backend: Dict mapping backend key -> list of OCRBox objects.
        mode:              Engine mode: "single", "cascade", "consensus", "maximum-recall".
        fuzzy_threshold:   Fuzzy match ratio for merging similar lines.

    Returns:
        Merged list of OCRBox objects.
    """
    if not results_by_backend:
        return []

    backend_keys = list(results_by_backend.keys())

    if mode == "single" or len(backend_keys) == 1:
        # Just return the first (primary) backend's results
        return list(results_by_backend[backend_keys[0]])

    if mode == "cascade":
        return _merge_cascade(results_by_backend, backend_keys, fuzzy_threshold)

    if mode == "consensus":
        return _merge_consensus(results_by_backend, fuzzy_threshold)

    if mode == "maximum-recall":
        return _merge_maximum_recall(results_by_backend, fuzzy_threshold)

    # Fallback
    log.warning("Unknown engine mode '%s', using single", mode)
    return list(results_by_backend[backend_keys[0]])


def _normalize_for_match(text: str) -> str:
    """Normalize text for fuzzy matching."""
    import re
    import unicodedata
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = unicodedata.normalize("NFKC", text)
    return text


def _get_fuzz():
    """Try to import rapidfuzz, return None if not available."""
    try:
        from rapidfuzz import fuzz
        return fuzz
    except ImportError:
        return None


def _get_confidence(box) -> float:
    """Extract confidence from an OCRBox, defaulting to 0."""
    from ocr_backends.base import OCRBox
    if isinstance(box, OCRBox):
        return box.confidence or 0.0
    return box.get("confidence", 0) or 0.0


def _get_text(box) -> str:
    """Extract text from an OCRBox."""
    from ocr_backends.base import OCRBox
    if isinstance(box, OCRBox):
        return box.text
    return box.get("text", "")


def _merge_cascade(
    results_by_backend: dict[str, list],
    backend_keys: list[str],
    fuzzy_threshold: float,
) -> list:
    """Cascade mode: use primary backend, fill in low-confidence gaps from fallbacks.

    Primary backend is the first key. If a line from the primary backend has
    confidence below 0.5, check fallback backends for a better version.
    """
    fuzz = _get_fuzz()
    primary_key = backend_keys[0]
    primary_results = list(results_by_backend[primary_key])
    fallback_keys = backend_keys[1:]

    if not fallback_keys:
        return primary_results

    # Collect all fallback results into a single list
    fallback_pool = []
    for fk in fallback_keys:
        fallback_pool.extend(results_by_backend.get(fk, []))

    # For each low-confidence primary line, look for a better fallback
    LOW_CONF_THRESHOLD = 0.5
    merged = []

    for pbox in primary_results:
        pconf = _get_confidence(pbox)
        ptext = _get_text(pbox)

        if pconf >= LOW_CONF_THRESHOLD:
            merged.append(pbox)
            continue

        # Look for a matching fallback line with higher confidence
        best_fallback = None
        best_fallback_conf = pconf

        ptext_norm = _normalize_for_match(ptext)
        for fbox in fallback_pool:
            ftext_norm = _normalize_for_match(_get_text(fbox))

            # Check similarity
            is_match = (ptext_norm == ftext_norm)
            if not is_match and fuzz and len(ptext_norm) > 3 and len(ftext_norm) > 3:
                ratio = fuzz.ratio(ptext_norm, ftext_norm)
                is_match = ratio >= fuzzy_threshold

            if is_match:
                fconf = _get_confidence(fbox)
                if fconf > best_fallback_conf:
                    best_fallback = fbox
                    best_fallback_conf = fconf

        merged.append(best_fallback if best_fallback else pbox)

    # Also add unique fallback lines not present in primary
    primary_texts = {_normalize_for_match(_get_text(b)) for b in primary_results}
    for fbox in fallback_pool:
        ftext_norm = _normalize_for_match(_get_text(fbox))
        if not ftext_norm:
            continue

        is_new = True
        for pt in primary_texts:
            if pt == ftext_norm:
                is_new = False
                break
            if fuzz and len(pt) > 3 and len(ftext_norm) > 3:
                if fuzz.ratio(pt, ftext_norm) >= fuzzy_threshold:
                    is_new = False
                    break

        if is_new:
            merged.append(fbox)
            primary_texts.add(ftext_norm)

    return merged


def _merge_consensus(
    results_by_backend: dict[str, list],
    fuzzy_threshold: float,
) -> list:
    """Consensus mode: merge lines across backends, keep highest-confidence version.

    Lines that appear in multiple backends with similar text are merged,
    keeping the version with the best confidence score.
    """
    fuzz = _get_fuzz()
    all_boxes = []
    for key, boxes in results_by_backend.items():
        all_boxes.extend(boxes)

    if not all_boxes:
        return []

    # Cluster similar lines
    clusters: list[list] = []

    for box in all_boxes:
        text_norm = _normalize_for_match(_get_text(box))
        if not text_norm:
            continue

        matched_cluster = None
        for cluster in clusters:
            rep_text = _normalize_for_match(_get_text(cluster[0]))
            if text_norm == rep_text:
                matched_cluster = cluster
                break
            if fuzz and len(text_norm) > 3 and len(rep_text) > 3:
                if fuzz.ratio(text_norm, rep_text) >= fuzzy_threshold:
                    matched_cluster = cluster
                    break

        if matched_cluster is not None:
            matched_cluster.append(box)
        else:
            clusters.append([box])

    # From each cluster, pick the highest-confidence line
    merged = []
    for cluster in clusters:
        best = max(cluster, key=lambda b: _get_confidence(b))
        merged.append(best)

    log.info("Consensus merge: %d boxes -> %d merged lines from %d clusters",
             len(all_boxes), len(merged), len(clusters))
    return merged


def _merge_maximum_recall(
    results_by_backend: dict[str, list],
    fuzzy_threshold: float,
) -> list:
    """Maximum recall mode: keep all unique lines from all backends.

    Similar to consensus but with a lower bar — keeps lines that are
    even somewhat unique. Designed for scrolling-text scenarios where
    catching every line matters more than perfect deduplication.
    """
    fuzz = _get_fuzz()
    all_boxes = []
    for key, boxes in results_by_backend.items():
        all_boxes.extend(boxes)

    if not all_boxes:
        return []

    # Stricter uniqueness check — higher threshold = keep more lines
    recall_threshold = max(fuzzy_threshold + 5, 92.0)

    unique: list = []
    unique_texts: list[str] = []

    for box in all_boxes:
        text_norm = _normalize_for_match(_get_text(box))
        if not text_norm or len(text_norm) < 2:
            continue

        is_dup = False
        for ut in unique_texts:
            if text_norm == ut:
                is_dup = True
                break
            if fuzz and len(text_norm) > 3 and len(ut) > 3:
                if fuzz.ratio(text_norm, ut) >= recall_threshold:
                    is_dup = True
                    break

        if not is_dup:
            unique.append(box)
            unique_texts.append(text_norm)

    log.info("Maximum recall: %d boxes -> %d unique lines", len(all_boxes), len(unique))
    return unique
