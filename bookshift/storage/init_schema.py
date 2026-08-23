"""Base SQLite schema initialization for pipeline_state.db."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS logical_books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    series TEXT,
    series_position REAL,
    state TEXT NOT NULL DEFAULT 'UNPAIRED',
    error_message TEXT,
    coarse_status TEXT,
    active_sync_mode TEXT,
    coarse_map_path TEXT,
    coarse_confidence REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ebook_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    logical_book_id INTEGER NOT NULL,
    canonical_path TEXT UNIQUE NOT NULL,
    bookorbit_book_id INTEGER,
    bookorbit_file_id INTEGER,
    file_hash TEXT,
    partial_md5 TEXT,
    file_size INTEGER,
    FOREIGN KEY (logical_book_id) REFERENCES logical_books(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audiobook_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    logical_book_id INTEGER NOT NULL,
    canonical_path TEXT UNIQUE NOT NULL,
    abs_library_item_id TEXT UNIQUE,
    duration_seconds REAL,
    format TEXT,
    FOREIGN KEY (logical_book_id) REFERENCES logical_books(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS alignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    logical_book_id INTEGER UNIQUE NOT NULL,
    alignment_map_path TEXT,
    aligned_sentence_count INTEGER,
    whisper_model TEXT,
    compiled_at TIMESTAMP,
    FOREIGN KEY (logical_book_id) REFERENCES logical_books(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ebook_files_bookorbit_file_id
    ON ebook_files(bookorbit_file_id);
CREATE INDEX IF NOT EXISTS idx_ebook_files_logical_book_id
    ON ebook_files(logical_book_id);
CREATE INDEX IF NOT EXISTS idx_audiobook_files_logical_book_id
    ON audiobook_files(logical_book_id);
CREATE INDEX IF NOT EXISTS idx_logical_books_state
    ON logical_books(state);

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', '1');
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
