#!/usr/bin/env python3
"""Gate 19 — Background alignment worker & M1 auto-offload orchestrator.

Polls pipeline_state.db for COARSE books that are not yet FINE-ready, probes
the configured M1 whisper-server, and either dispatches Storyteller processing
or holds the job as QUEUED_WAITING_FOR_WORKER (COARSE sync stays live).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from bookshift.adapters.alignment import AlignmentAdapter
from bookshift.config import REPO_ROOT, get_settings
from bookshift.storage.state_repo import SQLiteStateRepository

_settings = get_settings()
_alignment = AlignmentAdapter(_settings)
ANALYSIS = _settings.analysis_dir
POC = REPO_ROOT
DEFAULT_DB = _settings.db_path
STORYTELLER_DB = _settings.storyteller_db_path
DEFAULT_M1 = _settings.m1_whisper_url
STORYTELLER_API = _settings.storyteller_url
COMPILE_SCRIPT = ANALYSIS / "compile_locators.py"
VALIDATE_SCRIPT = ANALYSIS / "gate16_bidirectional_validation.py"

JOB_PENDING = "PENDING"
JOB_WAITING = "QUEUED_WAITING_FOR_WORKER"
JOB_IN_PROGRESS = "IN_PROGRESS"
JOB_PROMOTING = "PROMOTING"
JOB_COMPLETED = "COMPLETED"
JOB_FAILED = "FAILED"
JOB_STALE_REJECTED = "STALE_REJECTED"

TERMINAL_JOBS = {JOB_COMPLETED, JOB_FAILED, JOB_STALE_REJECTED}
_LAST_STORYTELLER_RETRY: dict[int, float] = {}
STORYTELLER_RETRY_COOLDOWN_S = 120.0
_WORKER_INSTANCE_TOKEN: str | None = None


def worker_token() -> str:
    global _WORKER_INSTANCE_TOKEN
    if _WORKER_INSTANCE_TOKEN is None:
        _WORKER_INSTANCE_TOKEN = str(uuid4())
    return _WORKER_INSTANCE_TOKEN


def reset_worker_token() -> None:
    global _WORKER_INSTANCE_TOKEN
    _WORKER_INSTANCE_TOKEN = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    return SQLiteStateRepository(db_path, _settings).connect()


def migrate_schema(db_path: Path) -> None:
    SQLiteStateRepository(db_path, _settings).migrate_schema()


def probe_m1(url: str = DEFAULT_M1, timeout: float = 2.0) -> dict[str, Any]:
    return _alignment.probe_worker(url, timeout=timeout)


def configure_whisper_server_url(
    storyteller_db: Path, url: str, *, dry_run: bool
) -> dict[str, Any]:
    return _alignment.configure_whisper_url(
        url, dry_run=dry_run, storyteller_db=storyteller_db
    )


def eligible_books(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM logical_books
        WHERE UPPER(COALESCE(active_sync_mode, '')) = 'COARSE'
          AND UPPER(COALESCE(fine_status, '')) != 'READY'
        ORDER BY id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def open_job(conn: sqlite3.Connection, book_id: int) -> dict[str, Any]:
    existing = conn.execute(
        """
        SELECT * FROM alignment_jobs
        WHERE logical_book_id = ?
          AND status NOT IN (?, ?)
        ORDER BY id DESC LIMIT 1
        """,
        (book_id, JOB_COMPLETED, JOB_FAILED),
    ).fetchone()
    if existing:
        return dict(existing)
    conn.execute(
        """
        INSERT INTO alignment_jobs (logical_book_id, status, lifecycle_json, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (book_id, JOB_PENDING, json.dumps({"events": []}), utc_now()),
    )
    conn.execute(
        """
        UPDATE logical_books
        SET alignment_job_status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (JOB_PENDING, book_id),
    )
    row = conn.execute(
        "SELECT * FROM alignment_jobs WHERE logical_book_id = ? ORDER BY id DESC LIMIT 1",
        (book_id,),
    ).fetchone()
    return dict(row)


def append_lifecycle(conn: sqlite3.Connection, job_id: int, event: dict[str, Any]) -> None:
    row = conn.execute(
        "SELECT lifecycle_json FROM alignment_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    blob = json.loads(row["lifecycle_json"] or '{"events":[]}') if row else {"events": []}
    blob.setdefault("events", []).append({"at": utc_now(), **event})
    conn.execute(
        "UPDATE alignment_jobs SET lifecycle_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(blob), utc_now(), job_id),
    )


def set_job(
    conn: sqlite3.Connection,
    job: dict[str, Any],
    *,
    status: str,
    m1_status: str | None = None,
    error: str | None = None,
    whisper_url: str | None = None,
    event: dict[str, Any] | None = None,
) -> None:
    fields = ["status = ?", "updated_at = ?"]
    vals: list[Any] = [status, utc_now()]
    if m1_status is not None:
        fields.append("m1_status = ?")
        vals.append(m1_status)
    if error is not None:
        fields.append("last_error = ?")
        vals.append(error)
    if whisper_url is not None:
        fields.append("whisper_server_url = ?")
        vals.append(whisper_url)
    if status == JOB_IN_PROGRESS:
        fields.append("started_at = COALESCE(started_at, ?)")
        vals.append(utc_now())
    if status in TERMINAL_JOBS:
        fields.append("completed_at = ?")
        vals.append(utc_now())
    if status != JOB_IN_PROGRESS:
        fields.append("job_token = NULL")
        fields.append("lease_expires_at = 0")
    vals.append(job["id"])
    conn.execute(
        f"UPDATE alignment_jobs SET {', '.join(fields)} WHERE id = ?", vals
    )
    conn.execute(
        """
        UPDATE logical_books
        SET alignment_job_status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, job["logical_book_id"]),
    )
    if event:
        append_lifecycle(conn, int(job["id"]), event)
    job["status"] = status


def promote_book(
    conn: sqlite3.Connection,
    book_id: int,
    *,
    dry_run: bool,
    run_compile: Callable[..., int] | None = None,
    run_validate: Callable[..., int] | None = None,
    job_id: int | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Gate 15 compile + Gate 16 validate + FINE promotion."""
    log: dict[str, Any] = {"book_id": book_id, "dry_run": dry_run, "steps": []}
    book = conn.execute("SELECT * FROM logical_books WHERE id = ?", (book_id,)).fetchone()
    if book is None:
        raise KeyError(f"book {book_id} missing")
    book_row = dict(book)

    # Keep COARSE available until promotion commits.
    if dry_run:
        log["steps"].append("compile_locators.py (skipped dry-run)")
        log["steps"].append("gate16_bidirectional_validation.py (skipped dry-run)")
    else:
        compile_rc = (run_compile or _run_compile)(book_id)
        log["steps"].append(f"compile_locators rc={compile_rc}")
        if compile_rc != 0:
            raise RuntimeError(f"compile_locators failed rc={compile_rc}")
        validate_rc = (run_validate or _run_validate)(book_id)
        log["steps"].append(f"gate16_validate rc={validate_rc}")
        if validate_rc != 0:
            raise RuntimeError(f"gate16 validation failed rc={validate_rc}")
        refreshed = conn.execute(
            "SELECT * FROM logical_books WHERE id = ?", (book_id,)
        ).fetchone()
        if refreshed is not None:
            book_row = dict(refreshed)

    if dry_run:
        conn.execute(
            """
            UPDATE logical_books
            SET active_sync_mode = 'FINE',
                fine_status = 'READY',
                state = 'ALIGNED',
                koreader_endpoint_status = 'READY',
                error_message = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (book_id,),
        )
        log["promoted"] = True
        log["active_sync_mode"] = "FINE"
        log["fine_status"] = "READY"
        return log

    repo = SQLiteStateRepository(db_path or DEFAULT_DB, _settings)
    promoted = _alignment.promote_fine_cas(
        repo,
        book_id=book_id,
        target_generation_id=str(book_row.get("generation_id") or ""),
        input_epub_fp=str(book_row.get("epub_fingerprint") or ""),
        input_audio_fp=str(book_row.get("audio_fingerprint") or ""),
        fine_map_path=str(book_row.get("fine_map_path") or ""),
        fine_table_path=str(book_row.get("fine_locator_table_path") or ""),
        sentence_count=int(book_row.get("fine_sentence_count") or 0),
    )
    if not promoted:
        log["promoted"] = False
        log["cas_rejected"] = True
        if job_id is not None:
            repo.mark_job_stale_rejected(
                job_id,
                "CAS promotion rejected: stale generation or fingerprint mismatch",
            )
        return log

    log["promoted"] = True
    log["active_sync_mode"] = "FINE"
    log["fine_status"] = "READY"
    return log


def find_readaloud_epub(title: str) -> Path | None:
    raw = _alignment.find_readaloud_epub(title)
    return Path(raw) if raw else None


def _run_compile(book_id: int) -> int:
    if book_id == 13:
        cmd = [
            sys.executable,
            str(COMPILE_SCRIPT),
            "--skip-extract",
            "--skip-state-update",
        ]
    elif book_id == 1:
        readaloud = find_readaloud_epub("Example Book")
        if readaloud is None:
            print("ERROR: Example Book readaloud EPUB not found", flush=True)
            return 2
        cmd = [
            sys.executable,
            str(COMPILE_SCRIPT),
            "--logical-book-id",
            "1",
            "--readaloud-epub",
            str(readaloud),
            "--skip-state-update",
        ]
    else:
        print(f"ERROR: compile not allowlisted for book_id={book_id}", flush=True)
        return 2
    proc = subprocess.run(cmd, cwd=str(ANALYSIS), check=False)
    return int(proc.returncode)


def _run_validate(book_id: int) -> int:
    if book_id == 13:
        cmd = [sys.executable, str(VALIDATE_SCRIPT)]
    elif book_id == 1:
        cmd = [
            sys.executable,
            str(VALIDATE_SCRIPT),
            "--table",
            str(ANALYSIS / "exact_locator_table_atob.json"),
            "--map",
            str(ANALYSIS / "alignment_map_full_atob.json"),
            "--results",
            str(ANALYSIS / "gate16_validation_results_atob.json"),
            "--report",
            str(ANALYSIS / "GATE_16_ATOB_REPORT.md"),
            "--logical-book-id",
            "1",
            "--no-require-fixture-xp",
            "--skip-state-update",
        ]
    else:
        print(f"ERROR: validate not allowlisted for book_id={book_id}", flush=True)
        return 2
    proc = subprocess.run(cmd, cwd=str(ANALYSIS), check=False)
    return int(proc.returncode)


def _job_to_dict(job: Any) -> dict[str, Any]:
    if isinstance(job, dict):
        return job
    return {
        "id": job.id,
        "logical_book_id": job.logical_book_id,
        "status": job.status,
        "m1_status": job.m1_status,
        "whisper_server_url": job.whisper_server_url,
        "last_error": job.last_error,
        "lifecycle_json": job.lifecycle_json,
        "job_token": job.job_token,
        "lease_expires_at": job.lease_expires_at,
        "target_generation_id": job.target_generation_id,
        "input_epub_fingerprint": job.input_epub_fingerprint,
        "input_audio_fingerprint": job.input_audio_fingerprint,
        "retry_count": job.retry_count,
    }


def tick(
    db_path: Path,
    *,
    dry_run: bool = False,
    m1_url: str = DEFAULT_M1,
    probe: Callable[[], dict[str, Any]] | None = None,
    simulate_smil_complete: bool = False,
    storyteller_db: Path = STORYTELLER_DB,
    log: Callable[[str], None] | None = None,
    worker_token_override: str | None = None,
    lease_duration_sec: int = 300,
) -> dict[str, Any]:
    """One worker iteration. Safe to call from tests."""
    emit = log or (lambda s: print(s, flush=True))
    migrate_schema(db_path)
    repo = SQLiteStateRepository(db_path, _settings)
    token = worker_token_override or worker_token()
    summary: dict[str, Any] = {
        "at": utc_now(),
        "dry_run": dry_run,
        "worker_token": token,
        "enqueued": [],
        "actions": [],
        "jobs": [],
    }
    m1 = probe() if probe else probe_m1(m1_url)
    summary["m1"] = m1
    emit(f"M1 probe online={m1['online']} url={m1['url']} err={m1.get('error')}")

    conn = connect(db_path)
    try:
        books = eligible_books(conn)
        for b in books:
            job = open_job(conn, int(b["id"]))
            summary["enqueued"].append({"book_id": b["id"], "title": b["title"], "job_id": job["id"]})
            emit(f"enqueue book={b['id']} {b['title']!r} job={job['id']} status={job['status']}")

        conn.commit()

        def _iter_jobs() -> list[dict[str, Any]]:
            if dry_run:
                rows = conn.execute(
                    """
                    SELECT * FROM alignment_jobs
                    WHERE status NOT IN (?, ?, ?)
                    ORDER BY id
                    """,
                    (JOB_COMPLETED, JOB_FAILED, JOB_STALE_REJECTED),
                ).fetchall()
                return [dict(r) for r in rows]
            pending: list[dict[str, Any]] = []
            processed_job_ids: set[int] = set()
            while True:
                acquired = repo.acquire_alignment_job(
                    token,
                    lease_duration_sec=lease_duration_sec,
                    exclude_ids=processed_job_ids,
                )
                if acquired is None:
                    break
                processed_job_ids.add(int(acquired.id))
                pending.append(_job_to_dict(acquired))
                if not m1["online"]:
                    continue
                break
            return pending

        for job in _iter_jobs():
            book_id = int(job["logical_book_id"])
            status = job["status"]

            if not m1["online"]:
                set_job(
                    conn,
                    job,
                    status=JOB_WAITING,
                    m1_status="OFFLINE",
                    event={"kind": "defer", "reason": "m1_offline", "keep_coarse": True},
                )
                summary["actions"].append(
                    {
                        "book_id": book_id,
                        "job_id": job["id"],
                        "action": "defer_waiting_for_worker",
                        "keep_coarse": True,
                    }
                )
                emit(f"defer job={job['id']} book={book_id} QUEUED_WAITING_FOR_WORKER")
                conn.commit()
                continue

            active_m1_url = str(m1.get("url") or m1_url)
            cfg = configure_whisper_server_url(storyteller_db, active_m1_url, dry_run=dry_run)
            summary["actions"].append(
                {"book_id": book_id, "job_id": job["id"], "action": "configure_whisper", "result": cfg}
            )

            if status in (JOB_PENDING, JOB_WAITING, JOB_IN_PROGRESS):
                if not job.get("whisper_server_url"):
                    if not dry_run:
                        repo.heartbeat_alignment_job(int(job["id"]), token, extend_sec=lease_duration_sec)
                    set_job(
                        conn,
                        job,
                        status=JOB_IN_PROGRESS,
                        m1_status="ONLINE",
                        whisper_url=active_m1_url,
                        event={
                            "kind": "dispatch",
                            "engine": "whisper-server",
                            "whisperServerUrl": active_m1_url,
                            "dry_run": dry_run,
                            "storyteller_api": STORYTELLER_API,
                        },
                    )
                    summary["actions"].append(
                        {
                            "book_id": book_id,
                            "job_id": job["id"],
                            "action": "dispatch_m1",
                            "dry_run": dry_run,
                        }
                    )
                    emit(f"dispatch job={job['id']} book={book_id} → M1 {active_m1_url}")
                    title = pipeline_book_title(conn, book_id)
                    dispatched = storyteller_dispatch_process(title, dry_run=dry_run)
                    summary["actions"].append(
                        {
                            "book_id": book_id,
                            "job_id": job["id"],
                            "action": "storyteller_process",
                            **dispatched,
                        }
                    )
                    append_lifecycle(
                        conn, int(job["id"]), {"kind": "storyteller_process", **dispatched}
                    )
                    emit(f"storyteller_process book={book_id} {dispatched}")

                elif status == JOB_IN_PROGRESS and not dry_run:
                    repo.heartbeat_alignment_job(int(job["id"]), token, extend_sec=lease_duration_sec)
                    title = pipeline_book_title(conn, book_id)
                    ra = storyteller_ra_status(title)
                    if ra.get("status") in {"ERROR", "FAILED", "CREATED", ""}:
                        now = time.time()
                        last = _LAST_STORYTELLER_RETRY.get(book_id, 0.0)
                        if now - last < STORYTELLER_RETRY_COOLDOWN_S:
                            emit(
                                f"retry_process deferred book={book_id} "
                                f"cooldown={STORYTELLER_RETRY_COOLDOWN_S - (now - last):.0f}s"
                            )
                        else:
                            _LAST_STORYTELLER_RETRY[book_id] = now
                            dispatched = storyteller_dispatch_process(title, dry_run=False)
                            append_lifecycle(
                                conn,
                                int(job["id"]),
                                {"kind": "storyteller_retry", "ra": ra, **dispatched},
                            )
                            summary["actions"].append(
                                {
                                    "book_id": book_id,
                                    "job_id": job["id"],
                                    "action": "storyteller_retry",
                                    "ra": ra,
                                    **dispatched,
                                }
                            )
                            emit(f"retry_process book={book_id} ra={ra.get('status')} {dispatched}")

                if simulate_smil_complete or (not dry_run and _smil_ready(book_id)):
                    if not dry_run:
                        repo.heartbeat_alignment_job(
                            int(job["id"]), token, extend_sec=lease_duration_sec
                        )
                    set_job(
                        conn,
                        job,
                        status=JOB_PROMOTING,
                        m1_status="ONLINE",
                        event={"kind": "smil_complete"},
                    )
                    conn.commit()
                    try:
                        promo = promote_book(
                            conn,
                            book_id,
                            dry_run=dry_run,
                            job_id=int(job["id"]),
                            db_path=db_path,
                        )
                        if promo.get("cas_rejected"):
                            set_job(
                                conn,
                                job,
                                status=JOB_STALE_REJECTED,
                                error=str(promo.get("last_error") or "CAS stale rejected"),
                                event={"kind": "stale_rejected", **promo},
                            )
                            summary["actions"].append(
                                {
                                    "book_id": book_id,
                                    "job_id": job["id"],
                                    "action": "stale_rejected",
                                    "keep_coarse": True,
                                    **promo,
                                }
                            )
                            emit(f"STALE_REJECTED book={book_id} — COARSE sync preserved")
                        else:
                            set_job(
                                conn,
                                job,
                                status=JOB_COMPLETED,
                                event={"kind": "promoted", **promo},
                            )
                            summary["actions"].append(
                                {"book_id": book_id, "job_id": job["id"], "action": "promote_fine", **promo}
                            )
                            emit(f"promoted book={book_id} → FINE READY")
                    except Exception as e:  # noqa: BLE001
                        set_job(
                            conn,
                            job,
                            status=JOB_FAILED,
                            error=str(e),
                            event={"kind": "promote_failed", "error": str(e)},
                        )
                        conn.execute(
                            """
                            UPDATE logical_books
                            SET error_message = ?,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (f"fine_promotion_failed: {e}", book_id),
                        )
                        summary["actions"].append(
                            {
                                "book_id": book_id,
                                "job_id": job["id"],
                                "action": "promote_failed",
                                "error": str(e),
                                "keep_coarse": True,
                            }
                        )
                        emit(f"promote FAILED book={book_id}: {e}")

            conn.commit()

        conn.commit()
        summary["jobs"] = [
            dict(r)
            for r in conn.execute(
                "SELECT id, logical_book_id, status, m1_status, last_error FROM alignment_jobs ORDER BY id"
            ).fetchall()
        ]
        summary["books"] = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, title, active_sync_mode, fine_status, alignment_job_status, coarse_status
                FROM logical_books ORDER BY id
                """
            ).fetchall()
        ]
    finally:
        conn.close()
    return summary


def storyteller_token(api_base: str = STORYTELLER_API) -> str | None:
    adapter = AlignmentAdapter(_settings)
    if api_base.rstrip("/") != _settings.storyteller_url.rstrip("/"):
        # Legacy override: temporary adapter with custom URL
        from dataclasses import replace

        custom = replace(_settings, storyteller_url=api_base.rstrip("/"))
        return AlignmentAdapter(custom).storyteller_token()
    return adapter.storyteller_token()


def storyteller_find_book(title: str, api_base: str = STORYTELLER_API) -> dict[str, Any] | None:
    from dataclasses import replace

    adapter = AlignmentAdapter(_settings)
    if api_base.rstrip("/") != _settings.storyteller_url.rstrip("/"):
        adapter = AlignmentAdapter(replace(_settings, storyteller_url=api_base.rstrip("/")))
    return adapter.find_book(title)


def storyteller_ra_status(title: str) -> dict[str, Any]:
    return _alignment.readaloud_status(title)


def storyteller_dispatch_process(title: str, *, dry_run: bool, restart: str | None = "full") -> dict[str, Any]:
    return _alignment.dispatch_alignment(title, dry_run=dry_run, restart=restart)


def pipeline_book_title(conn: sqlite3.Connection, book_id: int) -> str:
    row = conn.execute("SELECT title FROM logical_books WHERE id = ?", (book_id,)).fetchone()
    return str(row["title"]) if row else ""


def _smil_ready(book_id: int) -> bool:
    """True when Storyteller has ALIGNED readaloud for this pipeline book title."""
    conn = connect(DEFAULT_DB)
    try:
        title = pipeline_book_title(conn, book_id)
    finally:
        conn.close()
    if not title:
        return False
    found = storyteller_find_book(title)
    if not found:
        return False
    ra = found.get("readaloud") or found.get("readAloud") or {}
    status = str(ra.get("status") or "").upper()
    if status == "ALIGNED":
        return True
    # SQLite fallback already nested; also check DB directly
    if not STORYTELLER_DB.is_file():
        return False
    st = sqlite3.connect(str(STORYTELLER_DB))
    try:
        row = st.execute(
            """
            SELECT r.status FROM readaloud r
            JOIN book b ON b.uuid = r.book_uuid
            WHERE b.title = ? COLLATE NOCASE
            ORDER BY r.updated_at DESC LIMIT 1
            """,
            (title,),
        ).fetchone()
        return bool(row and str(row[0]).upper() == "ALIGNED")
    finally:
        st.close()


def mark_worker_ready(db_path: Path, book_id: int = 13) -> None:
    migrate_schema(db_path)
    conn = connect(db_path)
    try:
        conn.execute(
            """
            UPDATE logical_books
            SET gate_19_status = 'READY',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (book_id,),
        )
        conn.commit()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--m1-url", default=DEFAULT_M1)
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--simulate-m1", choices=("auto", "online", "offline"), default="auto")
    ap.add_argument("--simulate-smil-complete", action="store_true")
    ap.add_argument("--mark-ready", action="store_true")
    args = ap.parse_args(argv)

    def probe() -> dict[str, Any]:
        if args.simulate_m1 == "online":
            return {
                "url": args.m1_url,
                "online": True,
                "http_status": 200,
                "error": None,
                "latency_ms": 0.0,
                "simulated": True,
            }
        if args.simulate_m1 == "offline":
            return {
                "url": args.m1_url,
                "online": False,
                "http_status": None,
                "error": "simulated_offline",
                "latency_ms": 0.0,
                "simulated": True,
            }
        return probe_m1(args.m1_url)

    if args.mark_ready:
        mark_worker_ready(args.db)
        print("pipeline_state: gate_19_status=READY", flush=True)

    while True:
        summary = tick(
            args.db,
            dry_run=args.dry_run,
            m1_url=args.m1_url,
            probe=probe,
            simulate_smil_complete=args.simulate_smil_complete,
        )
        print(json.dumps({"jobs": summary["jobs"], "m1": summary["m1"]}, default=str), flush=True)
        if args.once:
            return 0
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
