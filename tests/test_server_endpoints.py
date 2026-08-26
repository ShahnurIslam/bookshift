"""Sync API endpoint contract tests (in-process HTTP)."""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from bookshift.domain.locator_index import CoarseChapterIndex, FineLocatorIndex
from bookshift.domain.sync_cache import BookBundle, SyncCache
from bookshift.server.sync_server import SyncAPIHandler, load_cache
from bookshift.storage.init_schema import init_db
from bookshift.storage.state_repo import SQLiteStateRepository


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
        assert payload["service"] == "bookshift-sync-api"
        assert "gate" not in payload
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


def test_running_api_observes_atomic_fine_promotion(tmp_path: Path):
    coarse_path = tmp_path / "coarse.json"
    coarse_path.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "chapter_index": 0,
                        "doc_fragment_index": 1,
                        "abs_start": 0.0,
                        "abs_end": 100.0,
                        "abs_duration": 100.0,
                        "epub_title": "Coarse chapter",
                        "abs_title": "Coarse chapter",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    fine_map_path = tmp_path / "fine-map.json"
    fine_map_path.write_text("{}", encoding="utf-8")
    fine_table_path = tmp_path / "fine-locators.json"
    fine_table_path.write_text(
        json.dumps(
            {
                "locators": [
                    {
                        "audio_seconds": 40.0,
                        "audio_end_seconds": 60.0,
                        "xpointer": "/body/DocFragment[9]/body/p[7]/text().0",
                        "chapter_index": 8,
                        "doc_fragment_index": 9,
                        "text_snippet": "fine fixture",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    db = tmp_path / "pipeline_state.db"
    init_db(db)
    repo = SQLiteStateRepository(db)
    repo.migrate_schema()
    conn = repo.connect()
    try:
        conn.execute(
            """
            INSERT INTO logical_books(
                title, author, state, active_sync_mode, coarse_status,
                coarse_map_path, fine_status, fine_map_path,
                fine_locator_table_path, fine_sentence_count,
                generation_id, epub_fingerprint, audio_fingerprint
            ) VALUES (?, ?, 'SYNC_READY', 'COARSE', 'READY', ?, 'PENDING', ?, ?, 1, ?, ?, ?)
            """,
            (
                "Promotion fixture",
                "Author",
                str(coarse_path),
                str(fine_map_path),
                str(fine_table_path),
                "gen-1",
                "epub-1",
                "audio-1",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    cache = load_cache(db)
    SyncAPIHandler.cache = cache
    SyncAPIHandler.db_path = db
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), SyncAPIHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        _, before = _get_json(f"{base}/api/v1/sync/position?book_id=1&timestamp=50")
        assert before["sync_mode"] == "COARSE"
        assert cache.get(1) is not None
        assert cache.get(1).active_sync_mode == "COARSE"

        assert repo.promote_fine_alignment_cas(
            1,
            "gen-1",
            "epub-1",
            "audio-1",
            str(fine_map_path),
            str(fine_table_path),
            1,
        )

        _, after = _get_json(f"{base}/api/v1/sync/position?book_id=1&timestamp=50")
        assert after["sync_mode"] == "FINE"
        assert after["xpointer"] == "/body/DocFragment[9]/body/p[7]/text().0"
    finally:
        httpd.shutdown()
        SyncAPIHandler.db_path = None


def test_running_api_keeps_coarse_bundle_when_promoted_fine_artifact_is_malformed(
    tmp_path: Path,
):
    coarse_path = tmp_path / "coarse.json"
    coarse_path.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "chapter_index": 0,
                        "doc_fragment_index": 1,
                        "abs_start": 0.0,
                        "abs_end": 100.0,
                        "abs_duration": 100.0,
                        "epub_title": "Coarse chapter",
                        "abs_title": "Coarse chapter",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    malformed_fine_path = tmp_path / "fine-locators.json"
    malformed_fine_path.write_text("{not valid json", encoding="utf-8")

    db = tmp_path / "pipeline_state.db"
    init_db(db)
    repo = SQLiteStateRepository(db)
    repo.migrate_schema()
    conn = repo.connect()
    try:
        conn.execute(
            """
            INSERT INTO logical_books(
                title, author, state, active_sync_mode, coarse_status,
                coarse_map_path, fine_status, generation_id,
                epub_fingerprint, audio_fingerprint
            ) VALUES (?, ?, 'SYNC_READY', 'COARSE', 'READY', ?, 'PENDING', ?, ?, ?)
            """,
            (
                "Malformed promotion fixture",
                "Author",
                str(coarse_path),
                "gen-1",
                "epub-1",
                "audio-1",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    cache = load_cache(db)
    coarse_bundle = cache.get(1)
    assert coarse_bundle is not None
    SyncAPIHandler.cache = cache
    SyncAPIHandler.db_path = db
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), SyncAPIHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        _, before = _get_json(f"{base}/api/v1/sync/position?book_id=1&timestamp=50")
        assert before["sync_mode"] == "COARSE"

        assert repo.promote_fine_alignment_cas(
            1,
            "gen-1",
            "epub-1",
            "audio-1",
            str(tmp_path / "fine-map.json"),
            str(malformed_fine_path),
            1,
        )

        _, after = _get_json(f"{base}/api/v1/sync/position?book_id=1&timestamp=50")
        assert after["sync_mode"] == "COARSE"
        assert cache.get(1) is coarse_bundle
        assert cache.get(1).active_sync_mode == "COARSE"
    finally:
        httpd.shutdown()
        SyncAPIHandler.db_path = None
