"""Alignment job lease acquisition, heartbeats, and reclamation."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from bookshift.storage.state_repo import SQLiteStateRepository
from bookshift.worker.alignment_worker import _verify_fine_artifacts


def _seed_db(path: Path) -> tuple[SQLiteStateRepository, int, int]:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE logical_books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            state TEXT,
            active_sync_mode TEXT,
            coarse_status TEXT,
            fine_status TEXT,
            alignment_job_status TEXT,
            error_message TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE alignment_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            logical_book_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            last_error TEXT,
            lifecycle_json TEXT,
            completed_at TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_meta(key, value) VALUES ('schema_version', '7');
        """
    )
    conn.execute(
        "INSERT INTO logical_books(title, author, state, active_sync_mode, coarse_status) "
        "VALUES ('Lease Test', 'Author', 'SYNC_READY', 'COARSE', 'READY')"
    )
    book_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.execute(
        "INSERT INTO alignment_jobs(logical_book_id, status, lifecycle_json) VALUES (?, 'PENDING', '{}')",
        (book_id,),
    )
    job_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.commit()
    conn.close()
    repo = SQLiteStateRepository(path)
    repo.migrate_schema()
    return repo, book_id, job_id


def test_acquire_lease_and_heartbeat(tmp_path: Path):
    db = tmp_path / "leases.db"
    repo, _book_id, job_id = _seed_db(db)
    job = repo.acquire_alignment_job("worker-a", lease_duration_sec=300)
    assert job is not None
    assert job.id == job_id
    assert job.status == "IN_PROGRESS"
    assert job.job_token == "worker-a"
    assert job.lease_expires_at > time.time()
    assert repo.heartbeat_alignment_job(job_id, "worker-a", extend_sec=600) is True
    assert repo.heartbeat_alignment_job(job_id, "worker-b", extend_sec=600) is False


def test_concurrent_worker_rejected(tmp_path: Path):
    db = tmp_path / "leases2.db"
    repo, _book_id, _job_id = _seed_db(db)
    first = repo.acquire_alignment_job("worker-a", lease_duration_sec=300)
    assert first is not None
    second = repo.acquire_alignment_job("worker-b", lease_duration_sec=300)
    assert second is None


def test_expired_lease_reclamation(tmp_path: Path):
    db = tmp_path / "leases3.db"
    repo, _book_id, job_id = _seed_db(db)
    first = repo.acquire_alignment_job("worker-a", lease_duration_sec=1)
    assert first is not None
    time.sleep(1.1)
    reclaimed = repo.acquire_alignment_job("worker-b", lease_duration_sec=300)
    assert reclaimed is not None
    assert reclaimed.id == job_id
    assert reclaimed.job_token == "worker-b"


def test_fail_alignment_job_retries(tmp_path: Path):
    db = tmp_path / "leases4.db"
    repo, book_id, job_id = _seed_db(db)
    acquired = repo.acquire_alignment_job("worker-a")
    assert acquired is not None
    repo.fail_alignment_job(job_id, "transient error", can_retry=True)
    row = repo.get_book(book_id)
    job_conn = sqlite3.connect(str(db))
    job_conn.row_factory = sqlite3.Row
    job = dict(job_conn.execute("SELECT * FROM alignment_jobs WHERE id=?", (job_id,)).fetchone())
    job_conn.close()
    assert job["status"] == "QUEUED_WAITING_FOR_WORKER"
    assert int(job["retry_count"]) == 1
    assert row is not None


def test_fine_artifact_verification_is_title_and_id_agnostic(tmp_path: Path):
    fine_map = tmp_path / "fine-map.json"
    fine_table = tmp_path / "fine-table.json"
    fine_map.write_text('{"sentences": []}', encoding="utf-8")
    fine_table.write_text('{"locators": []}', encoding="utf-8")

    _verify_fine_artifacts(
        {
            "fine_map_path": str(fine_map),
            "fine_locator_table_path": str(fine_table),
        }
    )


def test_fine_artifact_verification_rejects_missing_files(tmp_path: Path):
    with pytest.raises(RuntimeError, match="fine_map_path"):
        _verify_fine_artifacts(
            {
                "fine_map_path": str(tmp_path / "missing-map.json"),
                "fine_locator_table_path": str(tmp_path / "missing-table.json"),
            }
        )
