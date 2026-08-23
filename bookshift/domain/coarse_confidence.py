"""COARSE chapter-map confidence scoring."""

from __future__ import annotations

from typing import Any


def compute_coarse_confidence(chapter_map: dict[str, Any]) -> float:
    """Score 0.0–1.0 from EPUB/audio chapter count and duration alignment."""
    pairs: list[dict[str, Any]] = list(chapter_map.get("pairs") or [])
    if not pairs:
        return 0.0

    abs_meta = chapter_map.get("abs_meta") or {}
    n_abs = int(chapter_map.get("abs_chapter_count") or abs_meta.get("num_chapters") or 0)
    n_epub = len(pairs)

    if n_abs <= 0:
        n_abs = n_epub

    count_ratio = min(n_epub, n_abs) / max(n_epub, n_abs, 1)
    count_score = max(0.0, min(1.0, count_ratio))

    abs_durations = [
        float(p.get("abs_duration") or max(0.0, float(p.get("abs_end", 0)) - float(p.get("abs_start", 0))))
        for p in pairs
    ]
    epub_weights = [float(p.get("total_code_points") or 1) for p in pairs]

    abs_total = sum(abs_durations) or 1.0
    epub_total = sum(epub_weights) or 1.0
    abs_fracs = [d / abs_total for d in abs_durations]
    epub_fracs = [w / epub_total for w in epub_weights]

    duration_delta = sum(abs(a - e) for a, e in zip(abs_fracs, epub_fracs)) / max(len(pairs), 1)
    duration_score = max(0.0, 1.0 - min(1.0, duration_delta * 2.0))

    stored = chapter_map.get("confidence")
    if isinstance(stored, (int, float)) and stored > 0:
        stored_score = max(0.0, min(1.0, float(stored)))
        return round(0.5 * stored_score + 0.25 * count_score + 0.25 * duration_score, 4)

    return round(0.55 * count_score + 0.45 * duration_score, 4)
