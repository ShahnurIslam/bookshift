"""Integration tests for the public reconciliation runner."""

from __future__ import annotations

from bookshift.domain.sync_loop import abs_progress_signature, orbit_progress_signature
from bookshift.reconciliation import ReconciliationRunner, select_reverse_position


class FakeRepository:
    def __init__(self, book: dict):
        self.book = book

    def list_active_books(self):
        return [self.book]

    def update_sync_metadata(self, _book_id, *, source, progress_seconds):
        self.book["last_sync_source"] = source
        self.book["last_sync_progress"] = progress_seconds

    def update_orbit_observation(self, _book_id, *, signature, sync_mode, updated_at=0):
        self.book["last_orbit_progress_signature"] = signature
        self.book["last_orbit_sync_mode"] = sync_mode
        self.book["last_orbit_updated_at"] = updated_at

    def update_abs_observation(self, _book_id, *, signature, last_update):
        self.book["last_abs_progress_signature"] = signature
        self.book["last_abs_last_update"] = last_update


class FakeABS:
    def __init__(self, progress: dict):
        self.progress = dict(progress)
        self.writes: list[dict] = []

    def get_progress(self, _item_id):
        return dict(self.progress)

    def update_progress(self, _item_id, payload):
        self.writes.append(dict(payload))
        self.progress.update(payload)
        self.progress["lastUpdate"] = int(self.progress.get("lastUpdate") or 0) + 1
        return 200, None


class FakeBookOrbit:
    def __init__(self, progress: dict):
        self.progress = dict(progress)
        self.writes: list[dict] = []

    def get_book_progress(self, _book_id, file_id=None):
        return {"http_code": 200, "file_id": file_id, "progress": dict(self.progress)}

    def update_book_progress(self, _book_id, _file_id, payload):
        self.writes.append(dict(payload))
        self.progress.update(payload)
        self.progress["updatedAt"] = int(self.progress.get("updatedAt") or 0) + 1
        return 201, None


class FakeResolver:
    def __init__(self, *, forward: dict, reverse_by_xpointer: dict[str, dict]):
        self.forward = forward
        self.reverse_by_xpointer = reverse_by_xpointer

    def from_audio(self, _book_id, _timestamp):
        return dict(self.forward)

    def from_ebook(self, _book_id, xpointer):
        value = self.reverse_by_xpointer.get(xpointer)
        return dict(value) if value else None


def _book(*, abs_seconds: float, abs_revision: int, orbit: dict) -> dict:
    return {
        "logical_book_id": 1,
        "title": "Example Book",
        "active_sync_mode": "FINE",
        "bookorbit_book_id": 2,
        "bookorbit_file_id": 3,
        "abs_library_item_id": "example-item",
        "last_sync_source": "",
        "last_orbit_progress_signature": orbit_progress_signature(
            float(orbit["percentage"]), str(orbit["koreaderProgress"])
        ),
        "last_orbit_sync_mode": "FINE",
        "last_orbit_updated_at": int(orbit.get("updatedAt") or 0),
        "last_abs_progress_signature": abs_progress_signature(abs_seconds, False),
        "last_abs_last_update": abs_revision,
    }


def _forward(audio_seconds: float, percentage: float) -> dict:
    return {
        "ok": True,
        "sync_mode": "FINE",
        "approximate": False,
        "audio_seconds": audio_seconds,
        "ebook_percentage": percentage,
        "xpointer": "/body/DocFragment[4]/body/p[2]/text().0",
        "epub_cfi": "epubcfi(/6/8!/4/2:0)",
    }


def test_fresh_abs_backward_seek_updates_bookorbit_then_stabilizes():
    old_orbit = {
        "percentage": 20.0,
        "koreaderProgress": "/body/DocFragment[5]/body/p[1]/text().0",
    }
    book = _book(abs_seconds=200.0, abs_revision=10, orbit=old_orbit)
    repo = FakeRepository(book)
    abs_adapter = FakeABS(
        {"currentTime": 100.0, "duration": 1000.0, "lastUpdate": 11, "isFinished": False}
    )
    orbit_adapter = FakeBookOrbit(old_orbit)
    resolver = FakeResolver(
        forward=_forward(100.0, 10.0),
        reverse_by_xpointer={
            old_orbit["koreaderProgress"]: {
                "sync_mode": "FINE",
                "approximate": False,
                "audio_seconds": 200.0,
            },
            "/body/DocFragment[4]/body/p[2]/text().0": {
                "sync_mode": "FINE",
                "approximate": False,
                "audio_seconds": 100.0,
            },
        },
    )
    runner = ReconciliationRunner(repo, abs_adapter, orbit_adapter, resolver, execute=True)

    first = runner.reconcile_book(book)
    second = runner.reconcile_book(book)

    assert first["direction"] == "abs_to_orbit"
    assert first["status"] == "synced"
    assert orbit_adapter.writes[0]["percentage"] == 10.0
    assert second["direction"] == "skip"
    assert "echo" in second["reason"]
    assert len(orbit_adapter.writes) == 1


def test_fresh_bookorbit_backward_seek_updates_abs():
    previous_orbit = {
        "percentage": 20.0,
        "koreaderProgress": "/body/DocFragment[5]/body/p[1]/text().0",
    }
    current_orbit = {
        "percentage": 5.0,
        "koreaderProgress": "/body/DocFragment[2]/body/p[1]/text().0",
    }
    book = _book(abs_seconds=200.0, abs_revision=10, orbit=previous_orbit)
    repo = FakeRepository(book)
    abs_adapter = FakeABS(
        {"currentTime": 200.0, "duration": 1000.0, "lastUpdate": 10, "isFinished": False}
    )
    orbit_adapter = FakeBookOrbit(current_orbit)
    resolver = FakeResolver(
        forward=_forward(200.0, 20.0),
        reverse_by_xpointer={
            current_orbit["koreaderProgress"]: {
                "sync_mode": "FINE",
                "approximate": False,
                "audio_seconds": 50.0,
            }
        },
    )
    runner = ReconciliationRunner(repo, abs_adapter, orbit_adapter, resolver, execute=True)

    result = runner.reconcile_book(book)

    assert result["direction"] == "orbit_to_abs"
    assert result["status"] == "synced"
    assert abs_adapter.writes[0]["currentTime"] == 50.0


def test_stable_state_is_a_noop_and_records_no_write():
    orbit = {
        "percentage": 10.0,
        "koreaderProgress": "/body/DocFragment[4]/body/p[2]/text().0",
    }
    book = _book(abs_seconds=100.0, abs_revision=10, orbit=orbit)
    repo = FakeRepository(book)
    abs_adapter = FakeABS(
        {"currentTime": 100.0, "duration": 1000.0, "lastUpdate": 10, "isFinished": False}
    )
    orbit_adapter = FakeBookOrbit(orbit)
    resolver = FakeResolver(
        forward=_forward(100.0, 10.0),
        reverse_by_xpointer={
            orbit["koreaderProgress"]: {
                "sync_mode": "FINE",
                "approximate": False,
                "audio_seconds": 100.0,
            }
        },
    )

    result = ReconciliationRunner(
        repo, abs_adapter, orbit_adapter, resolver, execute=True
    ).reconcile_book(book)

    assert result["direction"] == "skip"
    assert not abs_adapter.writes
    assert not orbit_adapter.writes


def test_ambiguous_conflict_rebases_then_next_abs_movement_wins():
    previous_orbit = {
        "percentage": 15.0,
        "koreaderProgress": "/body/DocFragment[4]/body/p[1]/text().0",
        "updatedAt": 1_700_000_190_000,
    }
    current_orbit = {
        "percentage": 10.0,
        "koreaderProgress": "/body/DocFragment[3]/body/p[1]/text().0",
        "updatedAt": 1_700_000_210_000,
    }
    book = _book(
        abs_seconds=150.0,
        abs_revision=1_700_000_190_000,
        orbit=previous_orbit,
    )
    repo = FakeRepository(book)
    abs_adapter = FakeABS(
        {
            "currentTime": 200.0,
            "duration": 1000.0,
            "lastUpdate": 1_700_000_200_000,
            "isFinished": False,
        }
    )
    orbit_adapter = FakeBookOrbit(current_orbit)
    resolver = FakeResolver(
        forward=_forward(200.0, 20.0),
        reverse_by_xpointer={
            current_orbit["koreaderProgress"]: {
                "sync_mode": "FINE",
                "approximate": False,
                "audio_seconds": 100.0,
            }
        },
    )
    runner = ReconciliationRunner(repo, abs_adapter, orbit_adapter, resolver, execute=True)

    ambiguous = runner.reconcile_book(book)
    assert ambiguous["direction"] == "skip"
    assert "ambiguous" in ambiguous["reason"]
    assert not abs_adapter.writes
    assert not orbit_adapter.writes
    assert book["last_abs_last_update"] == 1_700_000_200_000
    assert book["last_orbit_updated_at"] == 1_700_000_210_000

    abs_adapter.progress["currentTime"] = 250.0
    abs_adapter.progress["lastUpdate"] = 1_700_000_300_000
    resolver.forward = _forward(250.0, 25.0)
    after_rebase = runner.reconcile_book(book)

    assert after_rebase["direction"] == "abs_to_orbit"
    assert after_rebase["status"] == "synced"
    assert len(orbit_adapter.writes) == 1


def test_unchanged_coarse_to_fine_reinterpretation_is_not_activity():
    orbit = {
        "percentage": 20.0,
        "koreaderProgress": "/body/DocFragment[5]/body/text().0",
    }
    book = _book(abs_seconds=100.0, abs_revision=10, orbit=orbit)
    book["last_orbit_sync_mode"] = "COARSE"
    runner = ReconciliationRunner(
        FakeRepository(book),
        FakeABS({"currentTime": 100.0, "duration": 1000.0, "lastUpdate": 10}),
        FakeBookOrbit(orbit),
        FakeResolver(
            forward=_forward(100.0, 10.0),
            reverse_by_xpointer={
                orbit["koreaderProgress"]: {
                    "sync_mode": "FINE",
                    "approximate": False,
                    "audio_seconds": 200.0,
                }
            },
        ),
        execute=True,
    )

    result = runner.reconcile_book(book)

    assert result["direction"] == "skip"
    assert "reinterpretation" in result["reason"]


def test_exact_fine_reverse_mapping_precedes_fallback():
    exact = {"sync_mode": "FINE", "approximate": False, "audio_seconds": 12.0}
    fallback = {"sync_mode": "COARSE", "approximate": True, "audio_seconds": 10.0}
    assert select_reverse_position(exact, fallback) is exact


def test_available_coarse_xpointer_mapping_is_retained():
    coarse = {"sync_mode": "COARSE", "approximate": True, "audio_seconds": 10.0}
    fallback = {"audio_seconds": 11.0}
    assert select_reverse_position(coarse, fallback) is coarse


def test_fallback_is_used_when_xpointer_mapping_is_unavailable():
    fallback = {"audio_seconds": 11.0}
    assert select_reverse_position(None, fallback) is fallback


def test_existing_abs_position_initializes_bookorbit_when_abs_observation_is_missing():
    previous_orbit = {
        "percentage": 20.0,
        "koreaderProgress": "/body/DocFragment[5]/body/p[1]/text().0",
    }
    current_orbit = {"percentage": 0.0, "koreaderProgress": ""}
    book = _book(abs_seconds=200.0, abs_revision=10, orbit=previous_orbit)
    book["last_abs_progress_signature"] = ""
    book["last_abs_last_update"] = 0
    repo = FakeRepository(book)
    abs_adapter = FakeABS(
        {"currentTime": 200.0, "duration": 1000.0, "lastUpdate": 11, "isFinished": False}
    )
    orbit_adapter = FakeBookOrbit(current_orbit)
    resolver = FakeResolver(
        forward=_forward(200.0, 20.0),
        reverse_by_xpointer={},
    )
    runner = ReconciliationRunner(repo, abs_adapter, orbit_adapter, resolver, execute=True)

    result = runner.reconcile_book(book)

    assert result["direction"] == "abs_to_orbit"
    assert result["status"] == "synced"
    assert orbit_adapter.writes[0]["percentage"] == 20.0
