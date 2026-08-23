"""In-memory sync cache and position resolution (FINE / COARSE)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bookshift.domain.locator_index import CoarseChapterIndex, FineLocatorIndex


@dataclass
class BookBundle:
    book_id: int
    title: str
    active_sync_mode: str
    fine: FineLocatorIndex | None = None
    coarse: CoarseChapterIndex | None = None
    fine_table_path: str | None = None
    coarse_map_path: str | None = None


@dataclass
class SyncCache:
    books: dict[int, BookBundle] = field(default_factory=dict)
    loaded_at: str = ""
    db_path: str = ""

    def get(self, book_id: int) -> BookBundle | None:
        return self.books.get(book_id)


def resolve_position(
    bundle: BookBundle,
    *,
    timestamp: float | None,
    xpointer: str | None,
) -> dict[str, Any]:
    mode = bundle.active_sync_mode
    t0 = time.perf_counter()

    def with_meta(payload: dict[str, Any]) -> dict[str, Any]:
        payload["book_id"] = bundle.book_id
        payload["title"] = bundle.title
        payload["lookup_ms"] = round((time.perf_counter() - t0) * 1000, 4)
        return payload

    use_fine = mode == "FINE" and bundle.fine is not None
    use_coarse = (
        mode == "COARSE" or (mode == "FINE" and bundle.fine is None)
    ) and bundle.coarse is not None

    if timestamp is not None and xpointer:
        raise ValueError("provide either timestamp or xpointer, not both")
    if timestamp is None and not xpointer:
        raise ValueError("require timestamp or xpointer")

    if use_fine:
        assert bundle.fine is not None
        if timestamp is not None:
            row = bundle.fine.by_timestamp(timestamp)
            if row is None:
                raise KeyError("timestamp_out_of_range")
            out = {
                "ok": True,
                "sync_mode": "FINE",
                "approximate": False,
                "audio_seconds": float(row["audio_seconds"]),
                "audio_end_seconds": float(row["audio_end_seconds"]),
                "chapter_index": row.get("chapter_index"),
                "doc_fragment_index": row.get("doc_fragment_index"),
                "xpointer": row["xpointer"],
                "text_snippet": row.get("text_snippet") or "",
                "chapter_title": row.get("chapter_title"),
                "element_id": row.get("element_id"),
                "epub_cfi": row.get("epub_cfi"),
                "query_timestamp": float(timestamp),
            }
            if bundle.coarse is not None:
                out["coarse_confidence"] = bundle.coarse.confidence_score
            return with_meta(out)
        row = bundle.fine.by_xpointer(xpointer or "")
        if row is None:
            if bundle.coarse is not None:
                payload = bundle.coarse.xpointer_to_position(xpointer or "")
                payload["degraded_from"] = "FINE"
                return with_meta(payload)
            raise KeyError("xpointer_not_found")
        return with_meta(
            {
                "ok": True,
                "sync_mode": "FINE",
                "approximate": False,
                "audio_seconds": float(row["audio_seconds"]),
                "audio_end_seconds": float(row["audio_end_seconds"]),
                "chapter_index": row.get("chapter_index"),
                "doc_fragment_index": row.get("doc_fragment_index"),
                "xpointer": row["xpointer"],
                "text_snippet": row.get("text_snippet") or "",
                "chapter_title": row.get("chapter_title"),
                "element_id": row.get("element_id"),
                "epub_cfi": row.get("epub_cfi"),
            }
        )

    if use_coarse or (
        bundle.coarse is not None and mode in ("COARSE", "NONE", None, "")
    ):
        assert bundle.coarse is not None
        if timestamp is not None:
            return with_meta(bundle.coarse.timestamp_to_position(timestamp))
        return with_meta(bundle.coarse.xpointer_to_position(xpointer or ""))

    raise KeyError(f"no_locator_data mode={mode}")


def load_cache_from_rows(
    rows: list[dict[str, Any]],
    *,
    db_path: Path,
    default_fine_table: Path,
    default_coarse_map: Path,
    lw_book_id: int = 13,
) -> SyncCache:
    cache = SyncCache(
        db_path=str(db_path),
        loaded_at=datetime.now(timezone.utc).isoformat(),
    )
    for row in rows:
        mode = (row.get("active_sync_mode") or "NONE").upper()
        bundle = BookBundle(
            book_id=int(row["id"]),
            title=str(row.get("title") or ""),
            active_sync_mode=mode,
        )
        fine_path = row.get("fine_locator_table_path")
        if not fine_path and int(row["id"]) == lw_book_id and default_fine_table.is_file():
            fine_path = str(default_fine_table)

        coarse_path = row.get("coarse_map_path")
        if not coarse_path and int(row["id"]) == lw_book_id and default_coarse_map.is_file():
            coarse_path = str(default_coarse_map)

        if fine_path and Path(fine_path).is_file():
            doc = json.loads(Path(fine_path).read_text(encoding="utf-8"))
            locs = doc.get("locators") or []
            bundle.fine = FineLocatorIndex(locs, path=fine_path)
            bundle.fine_table_path = fine_path

        if coarse_path and Path(coarse_path).is_file():
            cmap = json.loads(Path(coarse_path).read_text(encoding="utf-8"))
            bundle.coarse = CoarseChapterIndex(cmap, path=coarse_path)
            bundle.coarse_map_path = coarse_path

        cache.books[bundle.book_id] = bundle
    return cache
