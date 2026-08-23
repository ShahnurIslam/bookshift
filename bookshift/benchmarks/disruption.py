"""Session disruption measurement during COARSE→FINE promotion."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from typing import Callable

from bookshift.domain.locator_index import CoarseChapterIndex, FineLocatorIndex
from bookshift.domain.sync_cache import BookBundle, SyncCache
from bookshift.server.sync_server import SyncAPIHandler


@dataclass
class DisruptionResult:
    requests: int
    failures: int
    max_latency_ms: float
    p95_latency_ms: float
    min_sync_mode: str
    max_sync_mode: str
    progress_reverted: bool
    session_disrupted: bool
    latencies_ms: list[float]


def run_disruption_during_promotion(
    *,
    book_id: int,
    coarse_map: dict[str, Any],
    fine_rows: list[dict[str, Any]],
    promote_fn: Callable[[], None],
    requests_per_sec: int = 100,
    duration_sec: float = 2.0,
    host: str = "127.0.0.1",
) -> DisruptionResult:
    """Flood position API while atomic promotion + cache reload executes."""
    import socket

    coarse = CoarseChapterIndex(coarse_map)
    fine = FineLocatorIndex(fine_rows) if fine_rows else None
    cache = SyncCache(
        books={
            book_id: BookBundle(
                book_id=book_id,
                title="disruption-test",
                active_sync_mode="COARSE",
                coarse=coarse,
            )
        }
    )
    SyncAPIHandler.cache = cache
    SyncAPIHandler.db_path = None

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        port = int(s.getsockname()[1])

    httpd = ThreadingHTTPServer((host, port), SyncAPIHandler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    pairs = coarse.pairs
    mid_ts = float(pairs[len(pairs) // 2]["abs_start"]) + 5.0 if pairs else 300.0
    url = (
        f"http://{host}:{port}/api/v1/sync/position?"
        + urllib.parse.urlencode({"book_id": book_id, "timestamp": mid_ts})
    )

    latencies: list[float] = []
    failures = 0
    modes: list[str] = []
    audio_samples: list[float] = []
    stop = threading.Event()
    lock = threading.Lock()

    def worker() -> None:
        nonlocal failures
        interval = 1.0 / max(requests_per_sec, 1)
        while not stop.is_set():
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(url, timeout=0.5) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    ms = (time.perf_counter() - t0) * 1000.0
                    with lock:
                        latencies.append(ms)
                        modes.append(str(payload.get("sync_mode") or ""))
                        if payload.get("audio_seconds") is not None:
                            audio_samples.append(float(payload["audio_seconds"]))
                    if resp.status != 200:
                        failures += 1
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
                failures += 1
            time.sleep(interval)

    workers = [threading.Thread(target=worker, daemon=True) for _ in range(4)]
    for w in workers:
        w.start()
    time.sleep(0.25)

    promote_fn()
    bundle = cache.books[book_id]
    bundle.active_sync_mode = "FINE"
    bundle.fine = fine
    bundle.coarse = coarse
    if fine is not None:
        from bookshift.domain.sync_cache import resolve_position

        try:
            resolve_position(bundle, timestamp=mid_ts, xpointer=None)
        except KeyError:
            pass

    time.sleep(duration_sec)
    stop.set()
    for w in workers:
        w.join(timeout=1.0)

    httpd.shutdown()
    httpd.server_close()

    progress_reverted = False
    if len(audio_samples) >= 4 and (not modes or min(modes) == max(modes)):
        for i in range(1, len(audio_samples)):
            if audio_samples[i] + 1.0 < audio_samples[i - 1]:
                progress_reverted = True
                break

    max_lat = max(latencies) if latencies else 0.0
    p95 = 0.0
    if latencies:
        ordered = sorted(latencies)
        p95 = ordered[int(min(len(ordered) - 1, len(ordered) * 0.95))]

    session_disrupted = failures > 0 or p95 >= 5.0 or progress_reverted
    return DisruptionResult(
        requests=len(latencies) + failures,
        failures=failures,
        max_latency_ms=max_lat,
        p95_latency_ms=p95,
        min_sync_mode=min(modes) if modes else "",
        max_sync_mode=max(modes) if modes else "",
        progress_reverted=progress_reverted,
        session_disrupted=session_disrupted,
        latencies_ms=latencies,
    )
