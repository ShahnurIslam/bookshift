"""Gate 5 benchmark harness tests."""

from __future__ import annotations

import json
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from bookshift.benchmarks.accuracy import evaluate_coarse_accuracy
from bookshift.benchmarks.disruption import run_disruption_during_promotion
from bookshift.benchmarks.harness import BenchmarkRunner, BenchmarkTimings
from bookshift.benchmarks.fixtures import BookFixture
from bookshift.domain.locator_index import CoarseChapterIndex, FineLocatorIndex
from bookshift.domain.sync_cache import BookBundle
from bookshift.server.sync_server import SyncAPIHandler
from bookshift.storage.state_repo import SQLiteStateRepository


@pytest.fixture
def sample_coarse_map() -> dict:
    return {
        "pairs": [
            {
                "abs_index": 0,
                "abs_start": 0.0,
                "abs_end": 100.0,
                "abs_duration": 100.0,
                "chapter_index": 1,
                "doc_fragment_index": 2,
                "epub_title": "Ch1",
            },
            {
                "abs_index": 1,
                "abs_start": 100.0,
                "abs_end": 250.0,
                "abs_duration": 150.0,
                "chapter_index": 2,
                "doc_fragment_index": 3,
                "epub_title": "Ch2",
            },
        ],
        "confidence": 0.9,
    }


@pytest.fixture
def sample_fine_rows() -> list[dict]:
    return [
        {
            "audio_seconds": 10.0,
            "audio_end_seconds": 12.0,
            "xpointer": "/body/DocFragment[2]/body/p[1]/span/text().0",
            "chapter_index": 1,
            "doc_fragment_index": 2,
        },
        {
            "audio_seconds": 120.0,
            "audio_end_seconds": 125.0,
            "xpointer": "/body/DocFragment[3]/body/p[1]/span/text().0",
            "chapter_index": 2,
            "doc_fragment_index": 3,
        },
    ]


def test_accuracy_coarse_forward_error_near_zero(sample_coarse_map):
    metrics = evaluate_coarse_accuracy(
        sample_coarse_map,
        audio_duration=250.0,
        sample_count=10,
        seed=1,
    )
    assert metrics.sample_count == 10
    assert metrics.coarse_mean_error_seconds < 0.001


def test_accuracy_fine_exact_match_rate(sample_coarse_map, sample_fine_rows):
    metrics = evaluate_coarse_accuracy(
        sample_coarse_map,
        fine_table=sample_fine_rows,
        audio_duration=250.0,
        sample_count=2,
        seed=0,
    )
    assert metrics.fine_samples >= 1
    assert 0.0 <= metrics.fine_exact_match_rate <= 1.0


def test_benchmark_timings_pass_gate(sample_coarse_map):
    t = BenchmarkTimings(
        title="Mock",
        audio_duration_seconds=100,
        ingest_to_coarse_ready_s=0.5,
        first_usable_sync_s=0.55,
        session_disrupted=False,
        coarse_ready_within_2s=True,
    )
    assert t.passed()


def test_disruption_zero_failures_during_mock_promotion(sample_coarse_map, sample_fine_rows):
    promoted = threading.Event()

    def promote_fn() -> None:
        time.sleep(0.05)
        promoted.set()

    result = run_disruption_during_promotion(
        book_id=7,
        coarse_map=sample_coarse_map,
        fine_rows=sample_fine_rows,
        promote_fn=promote_fn,
        requests_per_sec=50,
        duration_sec=0.5,
    )
    assert promoted.is_set()
    assert result.failures == 0
    assert result.progress_reverted is False


def test_harness_coarse_ready_under_two_seconds(tmp_path: Path):
    epub = Path("data/benchmarks/dr_jekyll/dr_jekyll.epub")
    if not epub.is_file():
        pytest.skip("Dr Jekyll fixture EPUB not present")
    fx = BookFixture(
        key="dr_jekyll",
        title="Dr Jekyll",
        tier=1,
        epub=epub.resolve(),
        audio_duration_seconds=9310.0,
        synthetic_abs=True,
    )
    runner = BenchmarkRunner(fx, skip_compile=True, skip_disruption=True)
    result = runner.run()
    assert result.ingest_to_coarse_ready_s is not None
    assert result.ingest_to_coarse_ready_s < 2.0
    assert result.coarse_ready_within_2s


def test_cas_promotion_timing_ms(tmp_path: Path, sample_coarse_map, sample_fine_rows):
    from bookshift.storage.init_schema import init_db

    db = tmp_path / "bench.db"
    init_db(db)
    repo = SQLiteStateRepository(db)
    repo.migrate_schema()
    conn = repo.connect()
    try:
        conn.execute(
            """
            INSERT INTO logical_books (
                id, title, author, state, active_sync_mode,
                generation_id, epub_fingerprint, audio_fingerprint
            ) VALUES (1, 'T', 'A', 'PAIRED', 'COARSE', 'g1', 'ef', 'af')
            """
        )
        conn.commit()
    finally:
        conn.close()

    t0 = time.perf_counter()
    ok = repo.promote_fine_alignment_cas(
        1,
        target_generation_id="g1",
        input_epub_fp="ef",
        input_audio_fp="af",
        fine_map_path="/tmp/map.json",
        fine_table_path="/tmp/table.json",
        sentence_count=2,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert ok is True
    assert elapsed_ms < 50.0


def test_segments_to_locator_rows_matches_jekyll_epub():
    from bookshift.benchmarks.transcribe import segments_to_locator_rows

    epub = Path("data/benchmarks/dr_jekyll/dr_jekyll.epub")
    if not epub.is_file():
        pytest.skip("Dr Jekyll fixture EPUB not present")
    rows = segments_to_locator_rows(
        [
            {
                "audio_seconds": 12.0,
                "audio_end_seconds": 16.0,
                "text_snippet": "Mr. Utterson the lawyer was a man of a rugged countenance",
            }
        ],
        epub,
    )
    assert len(rows) == 1
    assert rows[0]["xpointer"].startswith("/body/DocFragment[")
    assert rows[0]["chapter_index"] >= 1


def test_probe_whisper_health_endpoint():
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from bookshift.benchmarks.transcribe import probe_whisper

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A003
            return

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        health = probe_whisper(f"http://127.0.0.1:{port}")
        assert health["online"] is True
        assert health["http_status"] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()

