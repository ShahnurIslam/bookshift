"""Benchmark lifecycle runner — measures real pipeline timings."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from bookshift.benchmarks.accuracy import AccuracyMetrics, evaluate_coarse_accuracy
from bookshift.benchmarks.disruption import DisruptionResult, run_disruption_during_promotion
from bookshift.benchmarks.fixtures import BookFixture
from bookshift.config import REPO_ROOT, Settings, get_settings
from bookshift.domain.locator_index import CoarseChapterIndex, FineLocatorIndex
from bookshift.domain.sync_cache import BookBundle, SyncCache
from bookshift.server.sync_server import SyncAPIHandler
from bookshift.storage.state_repo import SQLiteStateRepository

from bookshift.domain.coarse_chapter_map import AbsChapter, build_chapter_map
from bookshift.domain.locators.compiler import build_chapters
from bookshift.storage.init_schema import init_db


@dataclass
class BenchmarkTimings:
    title: str
    audio_duration_seconds: float
    ingest_to_coarse_ready_s: float | None = None
    first_usable_sync_s: float | None = None
    fine_ready_s: float | None = None
    atomic_promotion_ms: float | None = None
    coarse_mean_error_s: float | None = None
    session_disrupted: bool | None = None
    accuracy: AccuracyMetrics | None = None
    disruption: DisruptionResult | None = None
    notes: list[str] = field(default_factory=list)
    coarse_ready_within_2s: bool = False

    def passed(self) -> bool:
        if self.ingest_to_coarse_ready_s is None:
            return False
        if self.ingest_to_coarse_ready_s >= 2.0:
            return False
        if self.session_disrupted is True:
            return False
        return True


def _abs_from_coarse_map(cmap: dict[str, Any]) -> list[AbsChapter]:
    chapters: list[AbsChapter] = []
    for p in cmap.get("pairs") or []:
        chapters.append(
            AbsChapter(
                index=int(p["abs_index"]),
                id=p.get("abs_id", p["abs_index"]),
                title=str(p.get("abs_title") or ""),
                start=float(p["abs_start"]),
                end=float(p["abs_end"]),
            )
        )
    return chapters


def _synthetic_abs_chapters(
    epub_chapters: dict[str, Any], total_duration: float
) -> list[AbsChapter]:
    narrative = list(epub_chapters.get("toc_narrative") or [])
    if not narrative:
        return []
    weights = [max(1, int(n.get("total_code_points") or 1)) for n in narrative]
    total_w = float(sum(weights)) or 1.0
    out: list[AbsChapter] = []
    t = 0.0
    for i, (n, w) in enumerate(zip(narrative, weights)):
        dur = total_duration * (w / total_w)
        title = str(n.get("title") or f"Chapter {i + 1}")
        out.append(AbsChapter(index=i, id=i, title=title, start=t, end=t + dur))
        t += dur
    return out


def _write_temp_coarse_map(cmap: dict[str, Any], slug: str) -> Path:
    out_dir = REPO_ROOT / "data" / "benchmarks" / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"coarse_{slug}.json"
    path.write_text(json.dumps(cmap, indent=2) + "\n", encoding="utf-8")
    return path


class BenchmarkRunner:
    """Runs progressive-sync benchmarks for one fixture using production code paths."""

    def __init__(
        self,
        fixture: BookFixture,
        *,
        settings: Settings | None = None,
        skip_transcription: bool = True,
        skip_compile: bool = False,
        skip_disruption: bool = False,
        sample_port: int = 0,
        progress: Callable[[str], None] | None = None,
    ):
        self.fixture = fixture
        self.settings = settings or get_settings()
        self.skip_transcription = skip_transcription
        self.skip_compile = skip_compile
        self.skip_disruption = skip_disruption
        self.sample_port = sample_port
        self.progress = progress or (lambda s: None)

    def run(self) -> BenchmarkTimings:
        result = BenchmarkTimings(
            title=self.fixture.title,
            audio_duration_seconds=self.fixture.audio_duration_seconds,
        )
        t_start = time.perf_counter()

        cmap, coarse_ready_elapsed = self._build_coarse_map()
        result.ingest_to_coarse_ready_s = coarse_ready_elapsed
        result.coarse_ready_within_2s = coarse_ready_elapsed < 2.0

        bundle = BookBundle(
            book_id=int(self.fixture.logical_book_id or 0),
            title=self.fixture.title,
            active_sync_mode="COARSE",
            coarse=CoarseChapterIndex(cmap, path=str(self.fixture.coarse_map or "")),
        )
        result.first_usable_sync_s = self._measure_first_sync(bundle, t_start)

        fine_table_rows: list[dict[str, Any]] | None = None
        if not self.skip_transcription:
            fine_ready, table_rows, live_notes, live_duration = self._live_transcribe_and_align()
            result.fine_ready_s = fine_ready
            fine_table_rows = table_rows or None
            result.notes.extend(live_notes)
            if live_duration:
                result.audio_duration_seconds = live_duration
        elif self.fixture.fine_alignment_map and not self.skip_compile:
            if not self._compile_available():
                result.notes.append("fine_ready=skipped_compile_unavailable")
            else:
                fine_ready, table_rows = self._compile_fine_locators()
                result.fine_ready_s = fine_ready
                fine_table_rows = table_rows
                result.notes.append("fine_ready=compile_skip_transcription")
        elif self.fixture.fine_locator_table and self.fixture.fine_locator_table.is_file():
            doc = json.loads(self.fixture.fine_locator_table.read_text(encoding="utf-8"))
            fine_table_rows = list(doc.get("locators") or [])
            result.notes.append("fine_ready=precomputed_table")
        else:
            result.notes.append("fine_ready=skipped_no_alignment_map")

        acc = evaluate_coarse_accuracy(
            cmap,
            fine_table=fine_table_rows,
            audio_duration=self.fixture.audio_duration_seconds,
        )
        result.accuracy = acc
        result.coarse_mean_error_s = acc.coarse_mean_error_seconds

        if fine_table_rows and not self.skip_disruption:
            promo_ms, disruption = self._measure_promotion_and_disruption(cmap, fine_table_rows)
            result.atomic_promotion_ms = promo_ms
            result.disruption = disruption
            result.session_disrupted = disruption.session_disrupted
        elif self.fixture.compile_logical_book_id and not self.skip_disruption:
            promo_ms, disruption = self._measure_promotion_and_disruption(cmap, fine_table_rows or [])
            result.atomic_promotion_ms = promo_ms
            result.disruption = disruption
            result.session_disrupted = disruption.session_disrupted
        else:
            result.session_disrupted = False
            result.notes.append("disruption=skipped")

        return result

    def _build_coarse_map(self) -> tuple[dict[str, Any], float]:
        t0 = time.perf_counter()
        epub_chapters = build_chapters(self.fixture.epub)

        if self.fixture.coarse_map and self.fixture.coarse_map.is_file() and not self.fixture.synthetic_abs:
            cached = json.loads(self.fixture.coarse_map.read_text(encoding="utf-8"))
            abs_chapters = _abs_from_coarse_map(cached)
            result_map = build_chapter_map(abs_chapters, epub_chapters)
            elapsed = time.perf_counter() - t0
            # Index load (part of COARSE readiness)
            CoarseChapterIndex(result_map if result_map.get("pairs") else cached)
            return (result_map if result_map.get("pairs") else cached, elapsed)

        total_dur = self.fixture.audio_duration_seconds or 3600.0
        abs_chapters = _synthetic_abs_chapters(epub_chapters, total_dur)
        chapter_map = build_chapter_map(abs_chapters, epub_chapters)
        chapter_map.setdefault("built_at", datetime.now(timezone.utc).isoformat())
        chapter_map["schema"] = "coarse-chapter-map/v1"
        slug = self.fixture.key
        _write_temp_coarse_map(chapter_map, slug)
        CoarseChapterIndex(chapter_map)
        elapsed = time.perf_counter() - t0
        return chapter_map, elapsed

    def _audio_dir(self) -> Path | None:
        if self.fixture.audio_dir and self.fixture.audio_dir.is_dir():
            return self.fixture.audio_dir
        fallback = self.fixture.epub.parent / "audio"
        return fallback if fallback.is_dir() else None

    def _live_transcribe_and_align(self) -> tuple[float, list[dict[str, Any]], list[str], float]:
        from bookshift.benchmarks.transcribe import (
            probe_whisper,
            segments_to_locator_rows,
            transcribe_directory,
        )

        notes: list[str] = []
        audio_dir = self._audio_dir()
        if audio_dir is None:
            raise FileNotFoundError(
                f"live transcription requires audio files for {self.fixture.key} "
                f"(set catalog audio_dir or place MP3s next to the EPUB under audio/)"
            )
        whisper_url = self.settings.m1_whisper_url
        health = probe_whisper(whisper_url)
        if not health.get("online"):
            raise RuntimeError(f"Whisper endpoint offline: {health}")
        notes.append(f"whisper={whisper_url}")
        out_dir = REPO_ROOT / "data" / "benchmarks" / "artifacts"
        out_dir.mkdir(parents=True, exist_ok=True)
        cache_path = out_dir / f"{self.fixture.key}_whisper.json"
        table_path = out_dir / f"{self.fixture.key}_fine_table.json"
        t0 = time.perf_counter()
        asr = transcribe_directory(
            audio_dir,
            base_url=whisper_url,
            progress=self.progress,
            cache_path=cache_path,
        )
        rows = segments_to_locator_rows(list(asr.get("segments") or []), self.fixture.epub)
        elapsed = time.perf_counter() - t0
        duration = float(asr.get("audio_duration_seconds") or 0.0)
        n_seg = len(asr.get("segments") or [])
        n_rows = len(rows)
        notes.append(f"fine_ready=live_whisper segments={n_seg} locators={n_rows}")
        if n_seg and n_rows / n_seg < 0.2:
            notes.append("warning=low_locator_match_rate")
        table_path.write_text(
            json.dumps(
                {
                    "schema": "bookshift-fine-locator-table/v1",
                    "title": self.fixture.title,
                    "source": "live_whisper",
                    "whisper_url": whisper_url,
                    "audio_duration_seconds": duration,
                    "segment_count": n_seg,
                    "locator_count": n_rows,
                    "locators": rows,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.progress(
            f"  aligned {n_rows}/{n_seg} segments → {table_path.name} in {elapsed:.1f}s"
        )
        return elapsed, rows, notes, duration

    def _measure_first_sync(self, bundle: BookBundle, t_start: float) -> float:
        cache = SyncCache(books={bundle.book_id: bundle}, loaded_at=datetime.now(timezone.utc).isoformat())
        host = "127.0.0.1"
        port = self._pick_port()
        SyncAPIHandler.cache = cache
        SyncAPIHandler.db_path = None
        SyncAPIHandler.settings = self.settings
        httpd = ThreadingHTTPServer((host, port), SyncAPIHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            # Warm chapter mid-point timestamp
            pairs = bundle.coarse.pairs if bundle.coarse else []
            ts = float(pairs[len(pairs) // 2]["abs_start"]) + 1.0 if pairs else 100.0
            url = (
                f"http://{host}:{port}/api/v1/sync/position?"
                + urllib.parse.urlencode({"book_id": bundle.book_id, "timestamp": ts})
            )
            deadline = time.perf_counter() + 5.0
            while time.perf_counter() < deadline:
                try:
                    with urllib.request.urlopen(url, timeout=1.0) as resp:
                        payload = json.loads(resp.read().decode("utf-8"))
                        if resp.status == 200 and payload.get("xpointer"):
                            return time.perf_counter() - t_start
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                    time.sleep(0.02)
            result_note = "first_sync_timeout"
            raise RuntimeError(result_note)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def _pick_port(self) -> int:
        if self.sample_port:
            return self.sample_port
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    def _compile_available(self) -> bool:
        try:
            import analysis.compile_locators  # noqa: F401

            return True
        except ImportError:
            return False

    def _compile_fine_locators(self) -> tuple[float, list[dict[str, Any]]]:
        from analysis.compile_locators import run_python_compiler

        align = self.fixture.fine_alignment_map
        extract = self.fixture.storyteller_extract
        if align is None or not align.is_file():
            return (0.0, [])
        t0 = time.perf_counter()
        out_dir = REPO_ROOT / "data" / "benchmarks" / "artifacts"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"compiled_{self.fixture.key}.enriched.json"
        run_python_compiler(
            map_path=align,
            extract_dir=extract or out_dir,
            epub=self.fixture.epub,
            sequential=True,
            skip_assert=extract is None or not extract.is_dir(),
            out_path=out_path,
        )
        data = json.loads(out_path.read_text(encoding="utf-8"))
        rows = list((data.get("locators") or data.get("sentences") or []))
        # Build locator table shape if compile returned sentences only
        if rows and "xpointer" not in rows[0]:
            from analysis.compile_locators import build_locator_table

            rows = build_locator_table(data)
        elapsed = time.perf_counter() - t0
        return elapsed, rows

    def _measure_promotion_and_disruption(
        self,
        coarse_map: dict[str, Any],
        fine_rows: list[dict[str, Any]],
    ) -> tuple[float, DisruptionResult]:
        if not fine_rows:
            empty = DisruptionResult(
                requests=0,
                failures=0,
                max_latency_ms=0.0,
                p95_latency_ms=0.0,
                min_sync_mode="",
                max_sync_mode="",
                progress_reverted=False,
                session_disrupted=False,
                latencies_ms=[],
            )
            return (0.0, empty)

        with tempfile.TemporaryDirectory(prefix="bookshift_bench_") as tmp:
            db_path = Path(tmp) / "bench.db"
            self._seed_temp_db(db_path, coarse_map, fine_rows)
            repo = SQLiteStateRepository(db_path, self.settings)
            book_id = int(self.fixture.logical_book_id or 99)

            promo_ms_box: list[float] = [0.0]

            def promote_fn() -> None:
                row = repo.get_book(book_id) or {}
                t0 = time.perf_counter()
                repo.promote_fine_alignment_cas(
                    book_id,
                    target_generation_id=str(row.get("generation_id") or ""),
                    input_epub_fp=str(row.get("epub_fingerprint") or "bench_epub"),
                    input_audio_fp=str(row.get("audio_fingerprint") or "bench_audio"),
                    fine_map_path=str(row.get("fine_map_path") or ""),
                    fine_table_path=str(row.get("fine_locator_table_path") or ""),
                    sentence_count=len(fine_rows),
                )
                promo_ms_box[0] = (time.perf_counter() - t0) * 1000.0

            disruption = run_disruption_during_promotion(
                book_id=book_id,
                coarse_map=coarse_map,
                fine_rows=fine_rows,
                promote_fn=promote_fn,
                requests_per_sec=100,
                duration_sec=2.0,
            )
            return promo_ms_box[0], disruption

    def _seed_temp_db(
        self,
        db_path: Path,
        coarse_map: dict[str, Any],
        fine_rows: list[dict[str, Any]],
    ) -> None:
        init_db(db_path)
        repo = SQLiteStateRepository(db_path, self.settings)
        repo.migrate_schema()
        conn = repo.connect()
        try:
            conn.execute(
                """
                INSERT INTO logical_books (
                    id, title, author, state, active_sync_mode, coarse_status,
                    coarse_map_path, fine_status, generation_id, epub_fingerprint,
                    audio_fingerprint, fine_map_path, fine_locator_table_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(self.fixture.logical_book_id or 99),
                    self.fixture.title,
                    "Benchmark",
                    "PAIRED",
                    "COARSE",
                    "READY",
                    str(self.fixture.coarse_map or ""),
                    "QUEUED",
                    "bench-gen-1",
                    "bench_epub",
                    "bench_audio",
                    str(self.fixture.fine_alignment_map or ""),
                    str(self.fixture.fine_locator_table or ""),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        # Persist coarse map for bundle loading
        if self.fixture.coarse_map is None or not self.fixture.coarse_map.is_file():
            slug = self.fixture.key
            path = _write_temp_coarse_map(coarse_map, slug)
            conn = repo.connect()
            try:
                conn.execute(
                    "UPDATE logical_books SET coarse_map_path = ? WHERE id = ?",
                    (str(path), int(self.fixture.logical_book_id or 99)),
                )
                conn.commit()
            finally:
                conn.close()


def run_all(
    fixtures: list[BookFixture],
    *,
    skip_transcription: bool = True,
    skip_compile: bool = False,
    skip_disruption: bool = False,
    progress: Callable[[str], None] | None = None,
) -> list[BenchmarkTimings]:
    emit = progress or (lambda s: None)
    results: list[BenchmarkTimings] = []
    for fx in fixtures:
        emit(f"Benchmarking {fx.title}…")
        runner = BenchmarkRunner(
            fx,
            skip_transcription=skip_transcription,
            skip_compile=skip_compile,
            skip_disruption=skip_disruption,
            progress=emit,
        )
        results.append(runner.run())
    return results
