"""Bisect-based FINE and COARSE locator indexes."""

from __future__ import annotations

import re
from bisect import bisect_right
from typing import Any

from bookshift.domain.coarse_confidence import compute_coarse_confidence

_DF_RE = re.compile(r"DocFragment\[(\d+)\]")
_TEXT_OFFSET_RE = re.compile(r"text\(\)\.(\d+)")
_ELEMENT_INDEX_RE = re.compile(r"/(?:p|div|section|li)\[(\d+)\]", re.I)


def _estimate_xpointer_fraction(xp: str, total_code_points: int = 0) -> float:
    """Estimate 0.0–1.0 position within a chapter from xpointer structure.

    Uses the text() character offset and element indices as a heuristic.
    When total_code_points is available from the coarse chapter map, we
    can produce a better estimate by treating the element index as a
    proportion of content.
    """
    text_match = _TEXT_OFFSET_RE.search(xp)
    element_matches = _ELEMENT_INDEX_RE.findall(xp)

    if not element_matches and not text_match:
        return 0.0

    max_idx = max((int(m) for m in element_matches), default=0)
    text_offset = int(text_match.group(1)) if text_match else 0

    if total_code_points > 0:
        # Assume ~100 chars per paragraph as a rough average for novels.
        estimated_cp = max_idx * 100 + text_offset
        return min(1.0, max(0.0, estimated_cp / total_code_points))

    # Fallback: typical chapters have 20–200 paragraphs; use 80 as midpoint.
    elem_frac = min(1.0, max_idx / 80.0) if max_idx > 0 else 0.0
    text_bonus = min(0.01, text_offset / 50000.0)
    return min(1.0, elem_frac + text_bonus)


class FineLocatorIndex:
    """O(log n) timestamp→row, O(1) xpointer→row."""

    def __init__(self, locators: list[dict[str, Any]], *, path: str = ""):
        self.path = path
        self.rows = locators
        self.starts = [float(r["audio_seconds"]) for r in locators]
        self.by_xp: dict[str, int] = {}
        for i, r in enumerate(locators):
            xp = r.get("xpointer")
            if isinstance(xp, str) and xp not in self.by_xp:
                self.by_xp[xp] = i

    def by_timestamp(self, t: float) -> dict[str, Any] | None:
        if not self.rows:
            return None
        i = bisect_right(self.starts, t) - 1
        if i < 0:
            return None
        return self.rows[i]

    def by_xpointer(self, xp: str) -> dict[str, Any] | None:
        i = self.by_xp.get(xp)
        return self.rows[i] if i is not None else None


class CoarseChapterIndex:
    """Gate 14A chapter interpolation without BookOrbit Docker calls."""

    def __init__(self, chapter_map: dict[str, Any], *, path: str = ""):
        self.path = path
        self.map = chapter_map
        self.pairs: list[dict[str, Any]] = list(chapter_map.get("pairs") or [])
        self.confidence_score = compute_coarse_confidence(chapter_map)
        self.starts = [float(p["abs_start"]) for p in self.pairs]
        self.by_chapter_index = {int(p["chapter_index"]): p for p in self.pairs}
        self.by_doc_fragment = {int(p["doc_fragment_index"]): p for p in self.pairs}

    def pair_for_audio(self, t: float) -> dict[str, Any] | None:
        if not self.pairs:
            return None
        i = bisect_right(self.starts, t) - 1
        if i < 0:
            return self.pairs[0]
        return self.pairs[i]

    def timestamp_to_position(self, t: float) -> dict[str, Any]:
        pair = self.pair_for_audio(t)
        if pair is None:
            raise KeyError("no coarse pairs")
        dur = max(float(pair["abs_duration"]), 1e-9)
        frac = max(0.0, min(1.0, (t - float(pair["abs_start"])) / dur))
        df = int(pair["doc_fragment_index"])
        xp = f"/body/DocFragment[{df}]/body/text().0"
        return {
            "ok": True,
            "sync_mode": "COARSE",
            "approximate": True,
            "book_id": None,
            "audio_seconds": float(t),
            "audio_end_seconds": float(pair["abs_end"]),
            "chapter_index": int(pair["chapter_index"]),
            "doc_fragment_index": df,
            "local_fraction": round(frac, 6),
            "xpointer": xp,
            "text_snippet": pair.get("epub_title") or "",
            "chapter_title": pair.get("epub_title"),
            "abs_chapter_title": pair.get("abs_title"),
            "coarse_confidence": self.confidence_score,
        }

    def xpointer_to_position(self, xp: str) -> dict[str, Any]:
        m = _DF_RE.search(xp)
        if not m:
            raise KeyError("xpointer_missing_docfragment")
        df = int(m.group(1))
        pair = self.by_doc_fragment.get(df)
        if pair is None:
            pair = self.by_chapter_index.get(df - 1)
        if pair is None:
            raise KeyError(f"doc_fragment_not_in_coarse_map:{df}")

        local_frac = _estimate_xpointer_fraction(
            xp, int(pair.get("total_code_points") or 0)
        )
        abs_start = float(pair["abs_start"])
        abs_end = float(pair["abs_end"])
        dur = max(0.0, abs_end - abs_start)
        audio_s = abs_start + dur * local_frac

        return {
            "ok": True,
            "sync_mode": "COARSE",
            "approximate": True,
            "book_id": None,
            "audio_seconds": audio_s,
            "audio_end_seconds": abs_end,
            "chapter_index": int(pair["chapter_index"]),
            "doc_fragment_index": int(pair["doc_fragment_index"]),
            "local_fraction": round(local_frac, 6),
            "xpointer": xp,
            "text_snippet": pair.get("epub_title") or "",
            "chapter_title": pair.get("epub_title"),
            "abs_chapter_title": pair.get("abs_title"),
            "coarse_confidence": self.confidence_score,
        }
