"""COARSE chapter pairing — ABS chapters to EPUB narrative spines."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

MIN_ACCEPT_CONFIDENCE = 0.75
COUNT_MATCH_SCORE = 0.55
CORRELATION_WEIGHT = 0.35
ANCHOR_BONUS = 0.10


@dataclass
class AbsChapter:
    index: int
    id: Any
    title: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, float(self.end) - float(self.start))


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    if statistics.pstdev(xs) == 0 or statistics.pstdev(ys) == 0:
        return None
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    if den == 0:
        return None
    return num / den


def build_chapter_map(
    abs_chapters: list[AbsChapter], epub_chapters: dict[str, Any]
) -> dict[str, Any]:
    narrative = list(epub_chapters.get("toc_narrative") or [])
    n_abs = len(abs_chapters)
    n_epub = len(narrative)

    reasons: list[str] = []
    rejected = False

    if n_abs == 0 or n_epub == 0:
        rejected = True
        reasons.append("empty_abs_or_epub_narrative")

    count_ratio = (min(n_abs, n_epub) / max(n_abs, n_epub)) if max(n_abs, n_epub) else 0.0
    if abs(n_abs - n_epub) > 2 or count_ratio < 0.9:
        rejected = True
        reasons.append(
            f"chapter_count_mismatch abs={n_abs} epub_narrative={n_epub} ratio={count_ratio:.3f}"
        )

    pair_n = min(n_abs, n_epub)
    pairs: list[dict[str, Any]] = []
    for i in range(pair_n):
        a = abs_chapters[i]
        e = narrative[i]
        if e.get("chapter_index") is None or e.get("chapter_index") < 0:
            rejected = True
            reasons.append(f"unresolved_spine narrative_index={i} title={e.get('title')}")
        pairs.append(
            {
                "abs_index": a.index,
                "abs_id": a.id,
                "abs_title": a.title,
                "abs_start": a.start,
                "abs_end": a.end,
                "abs_duration": a.duration,
                "epub_narrative_index": e.get("narrative_index"),
                "epub_title": e.get("title"),
                "epub_href": e.get("href"),
                "chapter_index": e.get("chapter_index"),
                "doc_fragment_index": e.get("doc_fragment_index"),
                "total_code_points": e.get("total_code_points") or 0,
            }
        )

    xs = [float(p["abs_duration"]) for p in pairs if p["total_code_points"]]
    ys = [float(p["total_code_points"]) for p in pairs if p["total_code_points"]]
    corr = pearson(xs, ys) if len(xs) == len(ys) else None

    score = 0.0
    if n_abs == n_epub and n_abs > 0:
        score += COUNT_MATCH_SCORE
        reasons.append(f"exact_count_match n={n_abs}")
    elif not rejected and count_ratio >= 0.9:
        score += COUNT_MATCH_SCORE * count_ratio
        reasons.append(f"near_count_match ratio={count_ratio:.3f}")

    if corr is not None:
        corr_term = max(0.0, corr) * CORRELATION_WEIGHT
        score += corr_term
        reasons.append(f"duration_codepoints_pearson={corr:.3f}")
    else:
        reasons.append("duration_codepoints_pearson=n/a")

    if pairs:
        first = pairs[0]
        title = str(first.get("epub_title") or "").lower()
        if title.startswith("prologue") and first.get("doc_fragment_index") == 8:
            score += ANCHOR_BONUS
            reasons.append("anchor_prologue_docfragment_8")

    score = min(1.0, score)
    if rejected:
        score = min(score, 0.4)
        reasons.append("REJECTED_ambiguous_or_mismatched_structure")

    accepted = (not rejected) and score >= MIN_ACCEPT_CONFIDENCE

    return {
        "schema": "coarse-chapter-map/v1",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "abs_chapter_count": n_abs,
        "epub_narrative_count": n_epub,
        "paired_count": pair_n,
        "confidence": round(score, 4),
        "accepted": accepted,
        "min_accept_confidence": MIN_ACCEPT_CONFIDENCE,
        "confidence_reasons": reasons,
        "duration_codepoints_pearson": corr,
        "pairs": pairs,
        "unpaired_abs": [asdict(a) for a in abs_chapters[pair_n:]],
        "unpaired_epub": narrative[pair_n:],
    }
