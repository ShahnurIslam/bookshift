"""Gate 17 sync API — stdlib ThreadingHTTPServer."""

from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from bookshift.config import Settings, get_settings
from bookshift.domain.locator_index import FineLocatorIndex
from bookshift.domain.sync_loop import SyncLoopGuard
from datetime import datetime, timezone
from bookshift.domain.sync_cache import SyncCache, load_cache_from_rows, resolve_position
from bookshift.storage.state_repo import SQLiteStateRepository


def load_cache(db_path: Path | None = None, settings: Settings | None = None) -> SyncCache:
    cfg = settings or get_settings()
    path = Path(db_path or cfg.db_path)
    repo = SQLiteStateRepository(path, cfg)
    if not path.is_file():
        return SyncCache(
            db_path=str(path),
            loaded_at=datetime.now(timezone.utc).isoformat(),
        )
    rows = repo.load_sync_cache_bundles()
    cache = load_cache_from_rows(
        rows,
        db_path=path,
        default_fine_table=cfg.analysis_dir / "exact_locator_table.json",
        default_coarse_map=cfg.analysis_dir / "coarse_chapter_map.json",
    )
    lw = cache.books.get(13)
    default_fine = cfg.analysis_dir / "exact_locator_table.json"
    if lw and lw.fine is None and default_fine.is_file():
        doc = json.loads(default_fine.read_text(encoding="utf-8"))
        lw.fine = FineLocatorIndex(doc.get("locators") or [], path=str(default_fine))
        lw.fine_table_path = str(default_fine)
        if lw.active_sync_mode in ("", "NONE", None):
            lw.active_sync_mode = "FINE"
    return cache


def mark_endpoint_ready(
    db_path: Path | None = None,
    book_id: int = 13,
    settings: Settings | None = None,
) -> None:
    cfg = settings or get_settings()
    repo = SQLiteStateRepository(Path(db_path or cfg.db_path), cfg)
    repo.mark_koreader_endpoint_ready(book_id, f"{cfg.sync_api_base()}/api/v1/sync/position")


class SyncAPIHandler(BaseHTTPRequestHandler):
    cache: SyncCache = SyncCache()
    db_path: Path | None = None
    settings: Settings | None = None

    def _repo(self) -> SQLiteStateRepository:
        cfg = self.settings or get_settings()
        path = Path(self.db_path or cfg.db_path)
        return SQLiteStateRepository(path, cfg)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"[sync-api] {self.address_string()} - {fmt % args}\n")

    def _send(self, code: int, payload: dict[str, Any], *, started: float) -> None:
        payload.setdefault("http_ms", round((time.perf_counter() - started) * 1000, 4))
        body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        started = time.perf_counter()
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path in ("/api/v1/sync/health", "/health"):
            books = [
                {
                    "book_id": b.book_id,
                    "title": b.title,
                    "active_sync_mode": b.active_sync_mode,
                    "fine_loaded": b.fine is not None,
                    "fine_count": len(b.fine.rows) if b.fine else 0,
                    "coarse_loaded": b.coarse is not None,
                }
                for b in self.cache.books.values()
            ]
            self._send(
                200,
                {
                    "ok": True,
                    "service": "storyteller-poc-sync-api",
                    "gate": 17,
                    "loaded_at": self.cache.loaded_at,
                    "books": books,
                },
                started=started,
            )
            return

        if path != "/api/v1/sync/position":
            self._send(404, {"ok": False, "error": "not_found", "path": path}, started=started)
            return

        try:
            book_id = int((qs.get("book_id") or [""])[0])
        except (TypeError, ValueError):
            self._send(400, {"ok": False, "error": "book_id_required"}, started=started)
            return

        bundle = self.cache.get(book_id)
        if bundle is None:
            self._send(
                404,
                {"ok": False, "error": "book_not_found", "book_id": book_id},
                started=started,
            )
            return

        ts_raw = (qs.get("timestamp") or [None])[0]
        xp_raw = (qs.get("xpointer") or [None])[0]
        source_raw = (qs.get("source") or [None])[0]
        record_raw = (qs.get("record") or ["0"])[0]
        timestamp = None
        xpointer = None
        source = str(source_raw).strip() if source_raw else ""
        record_sync = str(record_raw).strip().lower() in ("1", "true", "yes")
        if ts_raw is not None and str(ts_raw) != "":
            try:
                timestamp = float(ts_raw)
            except ValueError:
                self._send(400, {"ok": False, "error": "invalid_timestamp"}, started=started)
                return
        if xp_raw is not None and str(xp_raw) != "":
            xpointer = unquote(str(xp_raw))

        try:
            result = resolve_position(bundle, timestamp=timestamp, xpointer=xpointer)
            progress_seconds = float(
                result.get("audio_seconds")
                if result.get("audio_seconds") is not None
                else (timestamp or 0.0)
            )
            if source and record_sync and self.db_path is not None:
                book_row = self._repo().get_book(book_id) or {}
                last_src, last_ts, last_prog = SyncLoopGuard.metadata_from_book(book_row)
                if SyncLoopGuard.should_suppress_sync(
                    book_id,
                    source,
                    progress_seconds,
                    last_src,
                    last_ts,
                    last_prog,
                ):
                    result["sync_suppressed"] = True
                    result["sync_suppress_reason"] = "echo_within_debounce_window"
                else:
                    self._repo().update_sync_metadata(
                        book_id,
                        source=source,
                        progress_seconds=progress_seconds,
                    )
            self._send(200, result, started=started)
        except ValueError as exc:
            self._send(400, {"ok": False, "error": str(exc)}, started=started)
        except KeyError as exc:
            self._send(404, {"ok": False, "error": str(exc).strip("'")}, started=started)
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"ok": False, "error": "internal", "detail": str(exc)}, started=started)


def run_server(
    host: str,
    port: int,
    cache: SyncCache,
    *,
    db_path: Path | None = None,
    settings: Settings | None = None,
) -> None:
    SyncAPIHandler.cache = cache
    SyncAPIHandler.db_path = db_path
    SyncAPIHandler.settings = settings or get_settings()
    httpd = ThreadingHTTPServer((host, port), SyncAPIHandler)
    print(
        f"sync-api listening on http://{host}:{port} "
        f"books={len(cache.books)} loaded_at={cache.loaded_at}",
        flush=True,
    )
    for b in cache.books.values():
        print(
            f"  book {b.book_id} {b.title!r} mode={b.active_sync_mode} "
            f"fine={len(b.fine.rows) if b.fine else 0} "
            f"coarse={'yes' if b.coarse else 'no'}",
            flush=True,
        )
    httpd.serve_forever()


def main(argv: list[str] | None = None) -> int:
    cfg = get_settings()
    ap = argparse.ArgumentParser(description="BookShift Gate 17 sync API")
    ap.add_argument("--host", default=cfg.sync_host)
    ap.add_argument("--port", type=int, default=cfg.sync_port)
    ap.add_argument("--db", type=Path, default=cfg.db_path)
    ap.add_argument("--mark-ready", action="store_true")
    ap.add_argument("--reload-only", action="store_true")
    args = ap.parse_args(argv)

    cache = load_cache(args.db, cfg)
    if not cache.books:
        print(f"WARN: no books loaded from {args.db}", flush=True)

    if args.mark_ready:
        mark_endpoint_ready(args.db, book_id=13, settings=cfg)
        print("pipeline_state: koreader_endpoint_status=READY", flush=True)

    if args.reload_only:
        print(
            json.dumps(
                {
                    "books": len(cache.books),
                    "lw_fine": len(cache.books[13].fine.rows)
                    if 13 in cache.books and cache.books[13].fine
                    else 0,
                }
            )
        )
        return 0

    run_server(args.host, args.port, cache, db_path=args.db, settings=cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
