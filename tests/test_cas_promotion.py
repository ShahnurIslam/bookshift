"""Compare-And-Swap FINE promotion tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bookshift.storage.state_repo import SQLiteStateRepository


def _seed_book(path: Path, *, generation_id: str, epub_fp: str, audio_fp: str) -> SQLiteStateRepository:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE logical_books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            state TEXT,
            active_sync_mode TEXT DEFAULT 'COARSE',
            coarse_status TEXT,
            fine_status TEXT,
            fine_map_path TEXT,
            fine_locator_table_path TEXT,
            fine_sentence_count INTEGER,
            koreader_endpoint_status TEXT,
            error_message TEXT,
            alignment_job_status TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_meta(key, value) VALUES ('schema_version', '7');
        """
    )
    conn.execute(
        """
        INSERT INTO logical_books(
            title, author, state, active_sync_mode, coarse_status,
            fine_map_path, fine_locator_table_path, fine_sentence_count
        ) VALUES (?, ?, 'SYNC_READY', 'COARSE', 'READY', ?, ?, ?)
        """,
        ("CAS Book", "Author", "/tmp/map.json", "/tmp/table.json", 42),
    )
    conn.commit()
    conn.close()
    repo = SQLiteStateRepository(path)
    repo.migrate_schema()
    repo.save_book(
        1,
        generation_id=generation_id,
        epub_fingerprint=epub_fp,
        audio_fingerprint=audio_fp,
    )
    return repo


def test_cas_promotion_success(tmp_path: Path):
    db = tmp_path / "cas.db"
    repo = _seed_book(db, generation_id="gen-1", epub_fp="epub-a", audio_fp="audio-a")
    ok = repo.promote_fine_alignment_cas(
        1,
        "gen-1",
        "epub-a",
        "audio-a",
        "/maps/fine.json",
        "/maps/table.json",
        100,
    )
    assert ok is True
    book = repo.get_book(1)
    assert book is not None
    assert book["active_sync_mode"] == "FINE"
    assert book["fine_status"] == "READY"


def test_promotion_preserves_observation_for_reinterpretation_guard(tmp_path: Path):
    db = tmp_path / "cas-observation.db"
    repo = _seed_book(db, generation_id="gen-1", epub_fp="epub-a", audio_fp="audio-a")
    repo.update_orbit_observation(
        1, signature="unchanged", sync_mode="COARSE", updated_at=5678
    )
    repo.update_abs_observation(1, signature="unchanged-abs", last_update=1234)
    assert repo.promote_fine_alignment_cas(
        1, "gen-1", "epub-a", "audio-a", "/maps/fine.json", "/maps/table.json", 100
    )
    book = repo.get_book(1)
    assert book is not None
    assert book["active_sync_mode"] == "FINE"
    assert book["last_orbit_progress_signature"] == "unchanged"
    assert book["last_orbit_sync_mode"] == "COARSE"
    assert book["last_orbit_updated_at"] == 5678
    assert book["last_abs_progress_signature"] == "unchanged-abs"
    assert book["last_abs_last_update"] == 1234


def test_v11_migration_is_additive_and_bootstraps_empty_observations(tmp_path: Path):
    db = tmp_path / "migration.db"
    repo = _seed_book(db, generation_id="gen-1", epub_fp="epub-a", audio_fp="audio-a")
    book = repo.get_book(1)
    assert book is not None
    assert book["title"] == "CAS Book"
    assert book["active_sync_mode"] == "COARSE"
    assert book["last_orbit_progress_signature"] == ""
    assert book["last_orbit_sync_mode"] == ""
    assert book["last_abs_progress_signature"] == ""
    assert book["last_abs_last_update"] == 0
    assert book["last_orbit_updated_at"] == 0
    assert repo.get_schema_version() == 11


def test_v10_to_v11_migration_preserves_existing_observations(tmp_path: Path):
    db = tmp_path / "v10-to-v11.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE logical_books (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            last_orbit_progress_signature TEXT DEFAULT '',
            last_orbit_sync_mode TEXT DEFAULT '',
            last_abs_progress_signature TEXT DEFAULT '',
            last_abs_last_update INTEGER DEFAULT 0
        );
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_meta(key, value) VALUES ('schema_version', '10');
        INSERT INTO logical_books VALUES (
            1, 'Existing Book', 'orbit-signature', 'FINE', 'abs-signature', 1234
        );
        """
    )
    conn.commit()
    conn.close()

    repo = SQLiteStateRepository(db)
    repo.migrate_schema()
    book = repo.get_book(1)

    assert book is not None
    assert book["title"] == "Existing Book"
    assert book["last_orbit_progress_signature"] == "orbit-signature"
    assert book["last_abs_progress_signature"] == "abs-signature"
    assert book["last_abs_last_update"] == 1234
    assert book["last_orbit_updated_at"] == 0
    assert repo.get_schema_version() == 11


def test_cas_stale_generation_rejected(tmp_path: Path):
    db = tmp_path / "cas2.db"
    repo = _seed_book(db, generation_id="gen-new", epub_fp="epub-a", audio_fp="audio-a")
    ok = repo.promote_fine_alignment_cas(
        1,
        "gen-old",
        "epub-a",
        "audio-a",
        "/maps/fine.json",
        "/maps/table.json",
        100,
    )
    assert ok is False
    book = repo.get_book(1)
    assert book is not None
    assert book["active_sync_mode"] == "COARSE"


def test_cas_fingerprint_mismatch_rejected(tmp_path: Path):
    db = tmp_path / "cas3.db"
    repo = _seed_book(db, generation_id="gen-1", epub_fp="epub-a", audio_fp="audio-a")
    ok = repo.promote_fine_alignment_cas(
        1,
        "gen-1",
        "epub-changed",
        "audio-a",
        "/maps/fine.json",
        "/maps/table.json",
        100,
    )
    assert ok is False
    book = repo.get_book(1)
    assert book is not None
    assert book["active_sync_mode"] == "COARSE"
