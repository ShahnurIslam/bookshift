"""Sync API endpoint contract tests (in-process HTTP)."""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from bookshift.domain.locator_index import CoarseChapterIndex, FineLocatorIndex
from bookshift.domain.sync_cache import BookBundle, SyncCache
from bookshift.server.sync_server import SyncAPIHandler


def _start_test_server() -> tuple[ThreadingHTTPServer, str]:
    fine = FineLocatorIndex(
        [
            {
                "audio_seconds": 276.0,
                "audio_end_seconds": 280.0,
                "xpointer": "/body/DocFragment[8]/body/p[11]/span/text().0",
                "chapter_index": 7,
                "doc_fragment_index": 8,
                "text_snippet": "fixture",
            }
        ]
    )
    coarse = CoarseChapterIndex(
        {
            "pairs": [
                {
                    "chapter_index": 7,
                    "doc_fragment_index": 8,
                    "abs_start": 0.0,
                    "abs_end": 500.0,
                    "abs_duration": 500.0,
                    "epub_title": "Chapter One",
                    "abs_title": "Ch1",
                }
            ]
        }
    )
    cache = SyncCache(
        books={
            1: BookBundle(
                book_id=1,
                title="The Strange Case of Dr Jekyll and Mr Hyde",
                active_sync_mode="FINE",
                fine=fine,
                coarse=coarse,
            )
        },
        loaded_at="test",
        db_path="test",
    )
    SyncAPIHandler.cache = cache
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), SyncAPIHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"http://127.0.0.1:{port}"


def _get_json(url: str) -> tuple[int, dict]:
    with urllib.request.urlopen(url, timeout=5) as resp:
        body = resp.read().decode("utf-8")
        return resp.status, json.loads(body)


def test_health_endpoint():
    httpd, base = _start_test_server()
    try:
        code, payload = _get_json(f"{base}/api/v1/sync/health")
        assert code == 200
        assert payload["ok"] is True
        assert payload["service"] == "storyteller-poc-sync-api"
        assert payload["gate"] == 17
        assert len(payload["books"]) == 1
        assert payload["books"][0]["book_id"] == 1
    finally:
        httpd.shutdown()


def test_position_timestamp_fine():
    httpd, base = _start_test_server()
    try:
        code, payload = _get_json(f"{base}/api/v1/sync/position?book_id=1&timestamp=276.0")
        assert code == 200
        assert payload["ok"] is True
        assert payload["sync_mode"] == "FINE"
        assert payload["xpointer"] == "/body/DocFragment[8]/body/p[11]/span/text().0"
        assert "http_ms" in payload
    finally:
        httpd.shutdown()


def test_position_unknown_book_404():
    httpd, base = _start_test_server()
    try:
        import urllib.error

        try:
            _get_json(f"{base}/api/v1/sync/position?book_id=999&timestamp=1.0")
            assert False, "expected 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
            payload = json.loads(e.read().decode("utf-8"))
            assert payload["error"] == "book_not_found"
    finally:
        httpd.shutdown()
