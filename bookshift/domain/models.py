"""Pure domain models for BookShift sync pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SyncMode(str, Enum):
    COARSE = "COARSE"
    FINE = "FINE"
    NONE = "NONE"


@dataclass
class Position:
    book_id: int
    timestamp: float | None = None
    xpointer: str | None = None
    cfi: str | None = None
    progress_pct: float | None = None
    sync_mode: SyncMode = SyncMode.NONE
    approximate: bool = False
    audio_seconds: float | None = None
    audio_end_seconds: float | None = None
    chapter_index: int | None = None
    doc_fragment_index: int | None = None
    text_snippet: str = ""
    chapter_title: str | None = None
    element_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class CoarseChapter:
    chapter_index: int
    doc_fragment_index: int
    abs_start: float
    abs_end: float
    abs_duration: float
    abs_title: str = ""
    epub_title: str = ""


@dataclass
class FineLocator:
    audio_seconds: float
    audio_end_seconds: float
    xpointer: str
    chapter_index: int | None = None
    doc_fragment_index: int | None = None
    text_snippet: str = ""
    chapter_title: str | None = None
    element_id: str | None = None
    epub_cfi: str | None = None


@dataclass
class AlignmentJob:
    id: int
    logical_book_id: int
    status: str
    m1_status: str | None = None
    whisper_server_url: str | None = None
    last_error: str | None = None
    lifecycle_json: str | None = None
    job_token: str | None = None
    lease_expires_at: float = 0.0
    target_generation_id: str = ""
    input_epub_fingerprint: str = ""
    input_audio_fingerprint: str = ""
    retry_count: int = 0

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> AlignmentJob:
        return cls(
            id=int(row["id"]),
            logical_book_id=int(row["logical_book_id"]),
            status=str(row["status"]),
            m1_status=row.get("m1_status"),
            whisper_server_url=row.get("whisper_server_url"),
            last_error=row.get("last_error"),
            lifecycle_json=row.get("lifecycle_json"),
            job_token=row.get("job_token"),
            lease_expires_at=float(row.get("lease_expires_at") or 0.0),
            target_generation_id=str(row.get("target_generation_id") or ""),
            input_epub_fingerprint=str(row.get("input_epub_fingerprint") or ""),
            input_audio_fingerprint=str(row.get("input_audio_fingerprint") or ""),
            retry_count=int(row.get("retry_count") or 0),
        )


@dataclass
class LogicalBook:
    id: int
    title: str
    author: str
    state: str
    active_sync_mode: SyncMode = SyncMode.NONE
    coarse_status: str | None = None
    fine_status: str | None = None
    coarse_map_path: str | None = None
    fine_map_path: str | None = None
    fine_locator_table_path: str | None = None
    bookorbit_book_id: int | None = None
    bookorbit_file_id: int | None = None
    abs_library_item_id: str | None = None
    alignment_job_status: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> LogicalBook:
        mode_raw = (row.get("active_sync_mode") or "NONE").upper()
        try:
            mode = SyncMode(mode_raw)
        except ValueError:
            mode = SyncMode.NONE
        known = {
            "id",
            "title",
            "author",
            "state",
            "active_sync_mode",
            "coarse_status",
            "fine_status",
            "coarse_map_path",
            "fine_map_path",
            "fine_locator_table_path",
            "bookorbit_book_id",
            "bookorbit_file_id",
            "abs_library_item_id",
            "alignment_job_status",
        }
        extra = {k: v for k, v in row.items() if k not in known}
        return cls(
            id=int(row["id"] if "id" in row else row.get("logical_book_id", 0)),
            title=str(row.get("title") or ""),
            author=str(row.get("author") or ""),
            state=str(row.get("state") or ""),
            active_sync_mode=mode,
            coarse_status=row.get("coarse_status"),
            fine_status=row.get("fine_status"),
            coarse_map_path=row.get("coarse_map_path"),
            fine_map_path=row.get("fine_map_path"),
            fine_locator_table_path=row.get("fine_locator_table_path"),
            bookorbit_book_id=row.get("bookorbit_book_id"),
            bookorbit_file_id=row.get("bookorbit_file_id"),
            abs_library_item_id=row.get("abs_library_item_id"),
            alignment_job_status=row.get("alignment_job_status"),
            extra=extra,
        )
