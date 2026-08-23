"""SQLite state repository for pipeline_state.db (schema v10)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from bookshift.config import Settings, get_settings
from bookshift.domain.models import AlignmentJob


class SQLiteStateRepository:
    """Encapsulates raw SQL for pipeline_state.db."""

    def __init__(self, db_path: Path | None = None, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self.db_path = Path(db_path or self._settings.db_path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _table_columns(self, conn: sqlite3.Connection, table: str) -> set[str]:
        if not self._table_exists(conn, table):
            return set()
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return row is not None

    def _schema_version(self, conn: sqlite3.Connection) -> int:
        try:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row:
                return int(row[0])
        except sqlite3.OperationalError:
            pass
        return 0

    def _ensure_column(
        self, conn: sqlite3.Connection, table: str, col: str, decl: str
    ) -> None:
        cols = self._table_columns(conn, table)
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    def _migrate_to_v7(self, conn: sqlite3.Connection) -> None:
        cols = self._table_columns(conn, "logical_books")
        for col, decl in (
            ("fine_status", "TEXT"),
            ("active_sync_mode", "TEXT"),
            ("koreader_endpoint_status", "TEXT"),
            ("koreader_endpoint_url", "TEXT"),
            ("alignment_worker_status", "TEXT"),
            ("alignment_job_status", "TEXT"),
            ("fine_locator_table_path", "TEXT"),
            ("fine_map_path", "TEXT"),
            ("coarse_map_path", "TEXT"),
            ("coarse_status", "TEXT"),
            ("coarse_confidence", "REAL"),
        ):
            if col not in cols:
                conn.execute(f"ALTER TABLE logical_books ADD COLUMN {col} {decl}")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS alignment_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                logical_book_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                m1_status TEXT,
                whisper_server_url TEXT,
                last_error TEXT,
                lifecycle_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT,
                completed_at TEXT,
                FOREIGN KEY (logical_book_id) REFERENCES logical_books(id)
            );
            CREATE INDEX IF NOT EXISTS idx_alignment_jobs_status
                ON alignment_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_alignment_jobs_book
                ON alignment_jobs(logical_book_id);
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('alignment_worker', '1')"
        )

    def _migrate_to_v8(self, conn: sqlite3.Connection) -> None:
        for col, decl in (
            ("generation_id", "TEXT DEFAULT ''"),
            ("epub_fingerprint", "TEXT DEFAULT ''"),
            ("audio_fingerprint", "TEXT DEFAULT ''"),
            ("coarse_confidence_score", "REAL DEFAULT 0.0"),
            ("last_sync_source", "TEXT DEFAULT ''"),
            ("last_sync_timestamp", "REAL DEFAULT 0.0"),
            ("last_sync_progress", "REAL DEFAULT 0.0"),
            ("fine_sentence_count", "INTEGER DEFAULT 0"),
        ):
            self._ensure_column(conn, "logical_books", col, decl)

        for col, decl in (
            ("job_token", "TEXT"),
            ("lease_expires_at", "REAL DEFAULT 0.0"),
            ("target_generation_id", "TEXT DEFAULT ''"),
            ("input_epub_fingerprint", "TEXT DEFAULT ''"),
            ("input_audio_fingerprint", "TEXT DEFAULT ''"),
            ("retry_count", "INTEGER DEFAULT 0"),
        ):
            if self._table_exists(conn, "alignment_jobs"):
                self._ensure_column(conn, "alignment_jobs", col, decl)

        if self._table_exists(conn, "alignment_jobs"):
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_alignment_jobs_job_token
                    ON alignment_jobs(job_token) WHERE job_token IS NOT NULL
                """
            )
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('correctness_hardening', '1')"
        )

    def _migrate_to_v9(self, conn: sqlite3.Connection) -> None:
        for col in ("last_orbit_progress_signature", "last_orbit_sync_mode"):
            self._ensure_column(conn, "logical_books", col, "TEXT DEFAULT ''")

    def _migrate_to_v10(self, conn: sqlite3.Connection) -> None:
        self._ensure_column(
            conn, "logical_books", "last_abs_progress_signature", "TEXT DEFAULT ''"
        )
        self._ensure_column(
            conn, "logical_books", "last_abs_last_update", "INTEGER DEFAULT 0"
        )

    def _migrate_to_v11(self, conn: sqlite3.Connection) -> None:
        self._ensure_column(
            conn, "logical_books", "last_orbit_updated_at", "INTEGER DEFAULT 0"
        )

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        version = self._schema_version(conn)
        if version < 7:
            self._migrate_to_v7(conn)
        if version < 8:
            self._migrate_to_v8(conn)
        if version < 9:
            self._migrate_to_v9(conn)
        if version < 10:
            self._migrate_to_v10(conn)
        if version < 11:
            self._migrate_to_v11(conn)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '11')"
        )

    def migrate_schema(self) -> None:
        conn = self.connect()
        try:
            self._run_migrations(conn)
            conn.commit()
        finally:
            conn.close()

    def get_schema_version(self) -> int:
        conn = self.connect()
        try:
            return self._schema_version(conn)
        finally:
            conn.close()

    def get_book(self, logical_book_id: int) -> dict[str, Any] | None:
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT * FROM logical_books WHERE id = ?", (logical_book_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def save_book(self, logical_book_id: int, **fields: Any) -> dict[str, Any]:
        if not fields:
            row = self.get_book(logical_book_id)
            if row is None:
                raise KeyError(f"logical_book_id={logical_book_id} not found")
            return row
        allowed = {k: v for k, v in fields.items() if k != "id"}
        sets = ", ".join(f"{k} = ?" for k in allowed)
        vals = list(allowed.values()) + [logical_book_id]
        conn = self.connect()
        try:
            conn.execute(
                f"""
                UPDATE logical_books
                SET {sets}, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                vals,
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM logical_books WHERE id = ?", (logical_book_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"logical_book_id={logical_book_id} not found")
            return dict(row)
        finally:
            conn.close()

    def update_sync_metadata(
        self,
        logical_book_id: int,
        *,
        source: str,
        progress_seconds: float,
        timestamp: float | None = None,
    ) -> None:
        ts = float(timestamp if timestamp is not None else time.time())
        conn = self.connect()
        try:
            conn.execute(
                """
                UPDATE logical_books
                SET last_sync_source = ?,
                    last_sync_timestamp = ?,
                    last_sync_progress = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (source, ts, float(progress_seconds), logical_book_id),
            )
            conn.commit()
        finally:
            conn.close()

    def update_orbit_observation(
        self,
        logical_book_id: int,
        *,
        signature: str,
        sync_mode: str,
        updated_at: int = 0,
    ) -> None:
        conn = self.connect()
        try:
            conn.execute(
                """
                UPDATE logical_books
                SET last_orbit_progress_signature = ?,
                    last_orbit_sync_mode = ?,
                    last_orbit_updated_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (signature, sync_mode, int(updated_at), logical_book_id),
            )
            conn.commit()
        finally:
            conn.close()

    def update_abs_observation(
        self, logical_book_id: int, *, signature: str, last_update: int
    ) -> None:
        conn = self.connect()
        try:
            conn.execute(
                """
                UPDATE logical_books
                SET last_abs_progress_signature = ?,
                    last_abs_last_update = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (signature, int(last_update), logical_book_id),
            )
            conn.commit()
        finally:
            conn.close()

    def list_logical_books(self) -> list[dict[str, Any]]:
        conn = self.connect()
        try:
            cols = self._table_columns(conn, "logical_books")
            select_cols = ["id", "title", "state", "active_sync_mode"]
            for optional in (
                "fine_locator_table_path",
                "fine_map_path",
                "coarse_map_path",
                "coarse_status",
                "fine_status",
                "coarse_confidence_score",
                "last_sync_source",
                "last_sync_timestamp",
                "last_sync_progress",
                "last_orbit_progress_signature",
                "last_orbit_sync_mode",
                "last_orbit_updated_at",
                "last_abs_progress_signature",
                "last_abs_last_update",
            ):
                if optional in cols:
                    select_cols.append(optional)
            rows = conn.execute(
                f"SELECT {', '.join(select_cols)} FROM logical_books ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def load_sync_cache_bundles(self) -> list[dict[str, Any]]:
        return self.list_logical_books()

    def list_active_books(self) -> list[dict[str, Any]]:
        if not self.db_path.is_file():
            raise FileNotFoundError(f"pipeline_state.db missing: {self.db_path}")
        conn = self.connect()
        try:
            cols = self._table_columns(conn, "logical_books")
            fine_map_col = "fine_map_path" if "fine_map_path" in cols else "NULL AS fine_map_path"
            coarse_map_col = (
                "coarse_map_path" if "coarse_map_path" in cols else "NULL AS coarse_map_path"
            )
            mode_col = (
                "active_sync_mode"
                if "active_sync_mode" in cols
                else "'NONE' AS active_sync_mode"
            )
            last_src = (
                "l.last_sync_source"
                if "last_sync_source" in cols
                else "'' AS last_sync_source"
            )
            last_ts = (
                "l.last_sync_timestamp"
                if "last_sync_timestamp" in cols
                else "0.0 AS last_sync_timestamp"
            )
            last_prog = (
                "l.last_sync_progress"
                if "last_sync_progress" in cols
                else "0.0 AS last_sync_progress"
            )
            last_orbit_sig = (
                "l.last_orbit_progress_signature"
                if "last_orbit_progress_signature" in cols
                else "'' AS last_orbit_progress_signature"
            )
            last_orbit_mode = (
                "l.last_orbit_sync_mode"
                if "last_orbit_sync_mode" in cols
                else "'' AS last_orbit_sync_mode"
            )
            last_orbit_update = (
                "l.last_orbit_updated_at"
                if "last_orbit_updated_at" in cols
                else "0 AS last_orbit_updated_at"
            )
            last_abs_sig = (
                "l.last_abs_progress_signature"
                if "last_abs_progress_signature" in cols
                else "'' AS last_abs_progress_signature"
            )
            last_abs_update = (
                "l.last_abs_last_update"
                if "last_abs_last_update" in cols
                else "0 AS last_abs_last_update"
            )
            rows = conn.execute(
                f"""
                SELECT
                    l.id AS logical_book_id,
                    l.id,
                    l.title,
                    l.author,
                    {mode_col},
                    {fine_map_col},
                    {coarse_map_col},
                    {last_src},
                    {last_ts},
                    {last_prog},
                    {last_orbit_sig},
                    {last_orbit_mode},
                    {last_orbit_update},
                    {last_abs_sig},
                    {last_abs_update},
                    e.bookorbit_book_id,
                    e.bookorbit_file_id,
                    a.abs_library_item_id
                FROM logical_books l
                LEFT JOIN ebook_files e ON e.logical_book_id = l.id
                LEFT JOIN audiobook_files a ON a.logical_book_id = l.id
                WHERE UPPER(COALESCE(l.active_sync_mode, '')) IN ('FINE', 'COARSE')
                ORDER BY l.id
                """
            ).fetchall()
        finally:
            conn.close()

        books: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            if not d.get("bookorbit_book_id") or not d.get("bookorbit_file_id"):
                continue
            if not d.get("abs_library_item_id"):
                continue
            books.append(d)
        return books

    def eligible_coarse_books(self) -> list[dict[str, Any]]:
        conn = self.connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM logical_books
                WHERE UPPER(COALESCE(active_sync_mode, '')) = 'COARSE'
                  AND UPPER(COALESCE(fine_status, '')) != 'READY'
                ORDER BY id
                """
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def acquire_alignment_job(
        self,
        worker_token: str,
        lease_duration_sec: int = 300,
        *,
        exclude_ids: set[int] | None = None,
    ) -> AlignmentJob | None:
        now = time.time()
        expires = now + float(lease_duration_sec)
        skip = sorted(exclude_ids or ())
        exclude_sql = ""
        exclude_params: list[Any] = []
        if skip:
            placeholders = ",".join("?" for _ in skip)
            exclude_sql = f" AND id NOT IN ({placeholders})"
            exclude_params = list(skip)
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"""
                SELECT * FROM alignment_jobs
                WHERE (
                    status IN ('QUEUED', 'QUEUED_WAITING_FOR_WORKER', 'PENDING')
                    OR (status = 'IN_PROGRESS' AND COALESCE(lease_expires_at, 0) < ?)
                ){exclude_sql}
                ORDER BY id ASC
                LIMIT 1
                """,
                (now, *exclude_params),
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            job_id = int(row["id"])
            cur = conn.execute(
                """
                UPDATE alignment_jobs
                SET status = 'IN_PROGRESS',
                    job_token = ?,
                    lease_expires_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND (
                    status IN ('QUEUED', 'QUEUED_WAITING_FOR_WORKER', 'PENDING')
                    OR (status = 'IN_PROGRESS' AND COALESCE(lease_expires_at, 0) < ?)
                  )
                """,
                (worker_token, expires, job_id, now),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return None
            book_cols = self._table_columns(conn, "logical_books")
            if "alignment_job_status" in book_cols:
                conn.execute(
                    """
                    UPDATE logical_books
                    SET alignment_job_status = 'IN_PROGRESS',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (int(row["logical_book_id"]),),
                )
            conn.commit()
            updated = conn.execute(
                "SELECT * FROM alignment_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return AlignmentJob.from_row(dict(updated)) if updated else None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def heartbeat_alignment_job(
        self,
        job_id: int,
        worker_token: str,
        extend_sec: int = 300,
    ) -> bool:
        expires = time.time() + float(extend_sec)
        conn = self.connect()
        try:
            cur = conn.execute(
                """
                UPDATE alignment_jobs
                SET lease_expires_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND job_token = ?
                  AND status = 'IN_PROGRESS'
                """,
                (expires, job_id, worker_token),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def promote_fine_alignment_cas(
        self,
        book_id: int,
        target_generation_id: str,
        input_epub_fp: str,
        input_audio_fp: str,
        fine_map_path: str,
        fine_table_path: str,
        sentence_count: int,
    ) -> bool:
        conn = self.connect()
        try:
            cur = conn.execute(
                """
                UPDATE logical_books
                SET active_sync_mode = 'FINE',
                    fine_status = 'READY',
                    state = 'ALIGNED',
                    koreader_endpoint_status = 'READY',
                    fine_map_path = ?,
                    fine_locator_table_path = ?,
                    fine_sentence_count = ?,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND COALESCE(generation_id, '') = ?
                  AND COALESCE(epub_fingerprint, '') = ?
                  AND COALESCE(audio_fingerprint, '') = ?
                """,
                (
                    fine_map_path,
                    fine_table_path,
                    int(sentence_count),
                    book_id,
                    target_generation_id,
                    input_epub_fp,
                    input_audio_fp,
                ),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def fail_alignment_job(
        self,
        job_id: int,
        error_message: str,
        can_retry: bool = True,
    ) -> None:
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT * FROM alignment_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return
            job = dict(row)
            retry = int(job.get("retry_count") or 0)
            if can_retry and retry < 5:
                new_status = "QUEUED_WAITING_FOR_WORKER"
                retry += 1
            else:
                new_status = "FAILED"
            conn.execute(
                """
                UPDATE alignment_jobs
                SET status = ?,
                    last_error = ?,
                    retry_count = ?,
                    job_token = NULL,
                    lease_expires_at = 0,
                    completed_at = CASE WHEN ? = 'FAILED' THEN CURRENT_TIMESTAMP ELSE completed_at END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (new_status, error_message, retry, new_status, job_id),
            )
            book_cols = self._table_columns(conn, "logical_books")
            if "alignment_job_status" in book_cols:
                conn.execute(
                    """
                    UPDATE logical_books
                    SET alignment_job_status = ?,
                        error_message = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (new_status, error_message, int(job["logical_book_id"])),
                )
            conn.commit()
        finally:
            conn.close()

    def mark_job_stale_rejected(self, job_id: int, reason: str) -> None:
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT logical_book_id FROM alignment_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            conn.execute(
                """
                UPDATE alignment_jobs
                SET status = 'STALE_REJECTED',
                    last_error = ?,
                    job_token = NULL,
                    lease_expires_at = 0,
                    completed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (reason, job_id),
            )
            if row:
                book_cols = self._table_columns(conn, "logical_books")
                if "alignment_job_status" in book_cols:
                    conn.execute(
                        """
                        UPDATE logical_books
                        SET alignment_job_status = 'STALE_REJECTED',
                            error_message = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (reason, int(row["logical_book_id"])),
                    )
            conn.commit()
        finally:
            conn.close()

    def mark_koreader_endpoint_ready(self, book_id: int, endpoint_url: str) -> None:
        conn = self.connect()
        try:
            self._ensure_column(conn, "logical_books", "koreader_endpoint_status", "TEXT")
            self._ensure_column(conn, "logical_books", "koreader_endpoint_url", "TEXT")
            conn.execute(
                """
                UPDATE logical_books
                SET koreader_endpoint_status = 'READY',
                    koreader_endpoint_url = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (endpoint_url, book_id),
            )
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('koreader_endpoint', '1')"
            )
            conn.commit()
        finally:
            conn.close()

    def transition_state(
        self,
        logical_book_id: int,
        new_state: str,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        conn = self.connect()
        try:
            existing = conn.execute(
                "SELECT id FROM logical_books WHERE id = ?", (logical_book_id,)
            ).fetchone()
            if existing is None:
                raise KeyError(f"logical_book_id={logical_book_id} not found")
            conn.execute(
                """
                UPDATE logical_books
                SET state = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (new_state, error_message, logical_book_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM logical_books WHERE id = ?", (logical_book_id,)
            ).fetchone()
            return dict(row)
        finally:
            conn.close()

    def get_book_state(self, logical_book_id: int) -> dict[str, Any] | None:
        conn = self.connect()
        try:
            book = conn.execute(
                "SELECT * FROM logical_books WHERE id = ?", (logical_book_id,)
            ).fetchone()
            if book is None:
                return None
            ebooks = conn.execute(
                "SELECT * FROM ebook_files WHERE logical_book_id = ? ORDER BY id",
                (logical_book_id,),
            ).fetchall()
            audios = conn.execute(
                "SELECT * FROM audiobook_files WHERE logical_book_id = ? ORDER BY id",
                (logical_book_id,),
            ).fetchall()
            alignment = conn.execute(
                "SELECT * FROM alignments WHERE logical_book_id = ?",
                (logical_book_id,),
            ).fetchone()
            return {
                **dict(book),
                "ebook_files": [dict(r) for r in ebooks],
                "audiobook_files": [dict(r) for r in audios],
                "alignment": dict(alignment) if alignment else None,
            }
        finally:
            conn.close()
