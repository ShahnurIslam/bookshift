"""Fine and coarse locator index bisect tests."""

from __future__ import annotations

from bookshift.domain.locator_index import CoarseChapterIndex, FineLocatorIndex
from bookshift.domain.sync_cache import BookBundle, resolve_position


def _fine_rows():
    return [
        {
            "audio_seconds": 0.0,
            "audio_end_seconds": 10.0,
            "xpointer": "/body/DocFragment[8]/body/p[1]/text().0",
            "chapter_index": 7,
            "doc_fragment_index": 8,
            "text_snippet": "start",
        },
        {
            "audio_seconds": 10.0,
            "audio_end_seconds": 20.0,
            "xpointer": "/body/DocFragment[8]/body/p[11]/span/text().0",
            "chapter_index": 7,
            "doc_fragment_index": 8,
            "text_snippet": "middle",
            "element_id": "el-mid",
            "epub_cfi": "epubcfi(/6/16!/4/30/2/1:0)",
        },
        {
            "audio_seconds": 20.0,
            "audio_end_seconds": 30.0,
            "xpointer": "/body/DocFragment[9]/body/p[1]/text().0",
            "chapter_index": 8,
            "doc_fragment_index": 9,
            "text_snippet": "end",
        },
    ]


def _coarse_map():
    return {
        "pairs": [
            {
                "chapter_index": 7,
                "doc_fragment_index": 8,
                "abs_start": 0.0,
                "abs_end": 100.0,
                "abs_duration": 100.0,
                "abs_title": "Ch1",
                "epub_title": "Chapter One",
            },
            {
                "chapter_index": 8,
                "doc_fragment_index": 9,
                "abs_start": 100.0,
                "abs_end": 200.0,
                "abs_duration": 100.0,
                "abs_title": "Ch2",
                "epub_title": "Chapter Two",
            },
        ]
    }


def test_fine_index_by_timestamp():
    idx = FineLocatorIndex(_fine_rows())
    row = idx.by_timestamp(15.0)
    assert row is not None
    assert row["xpointer"].endswith("p[11]/span/text().0")


def test_fine_index_by_xpointer():
    idx = FineLocatorIndex(_fine_rows())
    xp = "/body/DocFragment[8]/body/p[11]/span/text().0"
    row = idx.by_xpointer(xp)
    assert row is not None
    assert row["audio_seconds"] == 10.0


def test_coarse_index_timestamp_interpolation():
    idx = CoarseChapterIndex(_coarse_map())
    pos = idx.timestamp_to_position(50.0)
    assert pos["sync_mode"] == "COARSE"
    assert pos["approximate"] is True
    assert pos["local_fraction"] == 0.5
    assert "DocFragment[8]" in pos["xpointer"]
    assert 0.0 < pos["coarse_confidence"] <= 1.0


def test_resolve_position_fine_timestamp():
    bundle = BookBundle(
        book_id=13,
        title="Test",
        active_sync_mode="FINE",
        fine=FineLocatorIndex(_fine_rows()),
    )
    result = resolve_position(bundle, timestamp=15.0, xpointer=None)
    assert result["ok"] is True
    assert result["sync_mode"] == "FINE"
    assert result["book_id"] == 13
    assert "lookup_ms" in result


def test_fine_mode_uses_exact_locator_even_when_coarse_is_further_ahead():
    bundle = BookBundle(
        book_id=13,
        title="Test",
        active_sync_mode="FINE",
        fine=FineLocatorIndex(_fine_rows()),
        coarse=CoarseChapterIndex(_coarse_map()),
    )
    xp = "/body/DocFragment[8]/body/p[11]/span/text().0"
    result = resolve_position(bundle, timestamp=None, xpointer=xp)
    assert result["sync_mode"] == "FINE"
    assert result["approximate"] is False
    assert result["audio_seconds"] == 10.0


def test_fine_mode_degrades_to_coarse_for_unknown_exact_locator():
    bundle = BookBundle(
        book_id=13,
        title="Test",
        active_sync_mode="FINE",
        fine=FineLocatorIndex(_fine_rows()),
        coarse=CoarseChapterIndex(_coarse_map()),
    )
    result = resolve_position(
        bundle,
        timestamp=None,
        xpointer="/body/DocFragment[8]/body/p[404]/text().0",
    )
    assert result["sync_mode"] == "COARSE"
    assert result["approximate"] is True
    assert result["degraded_from"] == "FINE"


def test_resolve_position_coarse_xpointer_reverse():
    bundle = BookBundle(
        book_id=1,
        title="Test",
        active_sync_mode="COARSE",
        coarse=CoarseChapterIndex(_coarse_map()),
    )
    xp = "/body/DocFragment[8]/body/div/p[1]/text().0"
    result = resolve_position(bundle, timestamp=None, xpointer=xp)
    assert result["sync_mode"] == "COARSE"
    assert result["audio_seconds"] < 5.0, "near chapter start with p[1]/text().0"
