"""Configurable Audiobookshelf ↔ BookOrbit progress reconciliation runner."""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
from pathlib import Path
from typing import Any, Protocol

from bookshift.adapters.audiobookshelf import AudiobookshelfAdapter
from bookshift.adapters.bookorbit import BookOrbitAdapter
from bookshift.adapters.http_util import http_json
from bookshift.adapters.koreader import format_bookorbit_progress_payload
from bookshift.config import Settings, get_settings
from bookshift.domain.sync_loop import (
    abs_progress_signature,
    evaluate_reconciliation_plan,
    orbit_progress_signature,
)
from bookshift.storage.state_repo import SQLiteStateRepository


class PositionResolver(Protocol):
    def from_audio(self, book_id: int, timestamp: float) -> dict[str, Any] | None: ...

    def from_ebook(self, book_id: int, xpointer: str) -> dict[str, Any] | None: ...


class SyncApiPositionResolver:
    """Resolve positions through the public BookShift sync API."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _lookup(self, book_id: int, **query: Any) -> dict[str, Any] | None:
        params = urllib.parse.urlencode({"book_id": int(book_id), **query})
        code, payload = http_json(
            "GET", f"{self.base_url}/api/v1/sync/position?{params}", timeout=5.0
        )
        if code != 200 or not isinstance(payload, dict) or not payload.get("ok"):
            return None
        return payload

    def from_audio(self, book_id: int, timestamp: float) -> dict[str, Any] | None:
        return self._lookup(book_id, timestamp=float(timestamp))

    def from_ebook(self, book_id: int, xpointer: str) -> dict[str, Any] | None:
        if not xpointer.startswith("/body/DocFragment["):
            return None
        return self._lookup(book_id, xpointer=xpointer)


def select_reverse_position(
    xpointer_position: dict[str, Any] | None,
    fallback_position: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Prefer an exact FINE XPointer; otherwise retain an available fallback."""
    if xpointer_position:
        mode = str(xpointer_position.get("sync_mode") or "").upper()
        if mode == "FINE" and not bool(xpointer_position.get("approximate")):
            return xpointer_position
    return xpointer_position or fallback_position


def _bookorbit_progress(response: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    progress = response.get("progress")
    return progress if isinstance(progress, dict) else response


def _mapped_percentage(
    position: dict[str, Any], *, audio_seconds: float, duration_seconds: float
) -> float:
    for key in ("ebook_percentage", "percentage"):
        if position.get(key) is not None:
            return max(0.0, min(100.0, float(position[key])))
    if duration_seconds <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * audio_seconds / duration_seconds))


def _abs_payload(current_time: float, duration: float, is_finished: bool = False) -> dict[str, Any]:
    current = max(0.0, float(current_time))
    total = max(float(duration), current)
    ratio = 0.0 if total <= 0 else current / total
    return {
        "duration": total,
        "currentTime": current,
        "progress": 1.0 if is_finished else min(1.0, ratio),
        "isFinished": bool(is_finished),
    }


class ReconciliationRunner:
    """Reconcile active books using source activity rather than furthest-only state."""

    def __init__(
        self,
        repository: SQLiteStateRepository,
        abs_adapter: AudiobookshelfAdapter,
        bookorbit_adapter: BookOrbitAdapter,
        resolver: PositionResolver,
        *,
        execute: bool = False,
        min_delta_pct: float = 0.5,
    ):
        self.repository = repository
        self.abs = abs_adapter
        self.bookorbit = bookorbit_adapter
        self.resolver = resolver
        self.execute = execute
        self.min_delta_pct = min_delta_pct

    def reconcile_book(self, book: dict[str, Any]) -> dict[str, Any]:
        logical_id = int(book["logical_book_id"])
        item_id = str(book["abs_library_item_id"])
        book_id = int(book["bookorbit_book_id"])
        file_id = int(book["bookorbit_file_id"])
        mode = str(book.get("active_sync_mode") or "").upper()

        abs_progress = self.abs.get_progress(item_id) or {}
        abs_seconds = float(abs_progress.get("currentTime") or 0.0)
        duration = float(abs_progress.get("duration") or 0.0)
        abs_revision = int(abs_progress.get("lastUpdate") or 0)
        abs_signature = abs_progress_signature(
            abs_seconds, bool(abs_progress.get("isFinished"))
        )

        orbit_response = self.bookorbit.get_book_progress(book_id, file_id=file_id)
        orbit_progress = _bookorbit_progress(orbit_response)
        orbit_pct = float(orbit_progress.get("percentage") or 0.0)
        xpointer = str(orbit_progress.get("koreaderProgress") or "")
        orbit_signature = orbit_progress_signature(orbit_pct, xpointer)

        forward = self.resolver.from_audio(logical_id, abs_seconds)
        reverse = select_reverse_position(
            self.resolver.from_ebook(logical_id, xpointer) if xpointer else None
        )
        orbit_audio = (
            float(reverse["audio_seconds"])
            if reverse and reverse.get("audio_seconds") is not None
            else None
        )
        mapped_pct = (
            _mapped_percentage(forward, audio_seconds=abs_seconds, duration_seconds=duration)
            if forward
            else None
        )

        decision = evaluate_reconciliation_plan(
            abs_audio_s=abs_seconds,
            orbit_audio_s=orbit_audio,
            duration_s=duration,
            min_delta_pct=self.min_delta_pct,
            orbit_signature=orbit_signature,
            previous_orbit_signature=str(book.get("last_orbit_progress_signature") or ""),
            active_sync_mode=mode,
            previous_sync_mode=str(book.get("last_orbit_sync_mode") or "").upper(),
            last_sync_source=str(book.get("last_sync_source") or ""),
            abs_signature=abs_signature,
            previous_abs_signature=str(book.get("last_abs_progress_signature") or ""),
            abs_revision=abs_revision,
            previous_abs_revision=int(book.get("last_abs_last_update") or 0),
            abs_mapped_ebook_pct=mapped_pct,
            orbit_pct=orbit_pct,
        )
        result = {
            "book_id": logical_id,
            "title": str(book.get("title") or ""),
            "direction": decision.direction,
            "reason": decision.reason,
            "status": "dry_run" if decision.should_sync and not self.execute else "skip",
            "sync_mode": mode,
        }

        if not self.execute:
            return result
        if not decision.should_sync:
            if decision.record_observations:
                self._record_observations(
                    logical_id, orbit_signature, mode, abs_signature, abs_revision
                )
            return result

        if decision.direction == "abs_to_orbit":
            if not forward or not forward.get("xpointer") or mapped_pct is None:
                return {**result, "status": "error", "reason": "ebook mapping unavailable"}
            payload = format_bookorbit_progress_payload(
                percentage=mapped_pct,
                xpointer=str(forward["xpointer"]),
                cfi=str(forward.get("epub_cfi") or "") or None,
            )
            code, _ = self.bookorbit.update_book_progress(book_id, file_id, payload)
            if code not in (200, 201, 204):
                return {**result, "status": "error", "reason": f"BookOrbit write HTTP {code}"}
            verified = _bookorbit_progress(
                self.bookorbit.get_book_progress(book_id, file_id=file_id)
            )
            verified_pct = float(verified.get("percentage") or 0.0)
            if abs(verified_pct - mapped_pct) >= 0.05:
                return {**result, "status": "error", "reason": "BookOrbit verification mismatch"}
            verified_sig = orbit_progress_signature(
                verified_pct,
                str(verified.get("koreaderProgress") or payload["koreaderProgress"] or ""),
            )
            self.repository.update_sync_metadata(
                logical_id, source="abs", progress_seconds=abs_seconds
            )
            self._record_observations(
                logical_id, verified_sig, mode, abs_signature, abs_revision
            )
            return {**result, "status": "synced"}

        if reverse is None or orbit_audio is None:
            return {**result, "status": "error", "reason": "audio mapping unavailable"}
        payload = _abs_payload(orbit_audio, duration)
        code, _ = self.abs.update_progress(item_id, payload)
        if code not in (200, 201, 204):
            return {**result, "status": "error", "reason": f"ABS write HTTP {code}"}
        verified_abs = self.abs.get_progress(item_id) or {}
        verified_seconds = float(verified_abs.get("currentTime") or -1.0)
        if abs(verified_seconds - orbit_audio) >= 0.5:
            return {**result, "status": "error", "reason": "ABS verification mismatch"}
        self.repository.update_sync_metadata(
            logical_id, source="bookorbit", progress_seconds=orbit_audio
        )
        self._record_observations(
            logical_id,
            orbit_signature,
            mode,
            abs_progress_signature(
                verified_seconds, bool(verified_abs.get("isFinished"))
            ),
            int(verified_abs.get("lastUpdate") or 0),
        )
        return {**result, "status": "synced"}

    def _record_observations(
        self,
        logical_id: int,
        orbit_signature: str,
        mode: str,
        abs_signature: str,
        abs_revision: int,
    ) -> None:
        self.repository.update_orbit_observation(
            logical_id, signature=orbit_signature, sync_mode=mode
        )
        self.repository.update_abs_observation(
            logical_id, signature=abs_signature, last_update=abs_revision
        )

    def run_once(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for book in self.repository.list_active_books():
            try:
                results.append(self.reconcile_book(book))
            except Exception as exc:  # keep one title from aborting the whole cycle
                results.append(
                    {
                        "book_id": int(book.get("logical_book_id") or 0),
                        "title": str(book.get("title") or ""),
                        "direction": "skip",
                        "status": "error",
                        "reason": str(exc),
                    }
                )
        return results


def build_runner(
    *,
    settings: Settings,
    db_path: Path | None = None,
    execute: bool = False,
    min_delta_pct: float = 0.5,
) -> ReconciliationRunner:
    repository = SQLiteStateRepository(db_path or settings.db_path, settings)
    repository.migrate_schema()
    return ReconciliationRunner(
        repository,
        AudiobookshelfAdapter(settings),
        BookOrbitAdapter(settings),
        SyncApiPositionResolver(settings.sync_api_base()),
        execute=execute,
        min_delta_pct=min_delta_pct,
    )


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Reconcile ABS and BookOrbit progress")
    parser.add_argument("--db", type=Path, default=settings.db_path)
    parser.add_argument("--execute", action="store_true", help="Persist writes (default: dry-run)")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--min-delta-pct", type=float, default=0.5)
    args = parser.parse_args(argv)
    runner = build_runner(
        settings=settings,
        db_path=args.db,
        execute=args.execute,
        min_delta_pct=args.min_delta_pct,
    )
    while True:
        for result in runner.run_once():
            print(json.dumps(result, sort_keys=True), flush=True)
        if args.once:
            return 0
        time.sleep(max(1.0, args.interval))
