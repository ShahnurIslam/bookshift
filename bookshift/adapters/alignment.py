"""Storyteller alignment / M1 Whisper adapter."""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from uuid import uuid4

from bookshift.config import REPO_ROOT, Settings, get_settings


class AlignmentAdapter:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def probe_worker(self, url: str | None = None, timeout: float = 2.0) -> dict[str, Any]:
        target = (url or self.settings.m1_whisper_url).rstrip("/")
        t0 = time.perf_counter()
        result: dict[str, Any] = {
            "url": target,
            "online": False,
            "http_status": None,
            "error": None,
            "latency_ms": None,
        }
        probe_url = target + "/health"
        try:
            req = urllib.request.Request(probe_url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result["http_status"] = int(resp.status)
                result["online"] = 200 <= int(resp.status) < 500
                result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                result["path"] = "/health"
        except urllib.error.HTTPError as e:
            result["http_status"] = int(e.code)
            result["online"] = True
            result["error"] = f"HTTP {e.code}"
            result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        except Exception as e:  # noqa: BLE001
            result["error"] = f"{type(e).__name__}: {e}"
            result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return result

    def configure_whisper_url(
        self, url: str, *, dry_run: bool = False, storyteller_db: Path | None = None
    ) -> dict[str, Any]:
        action: dict[str, Any] = {
            "setting": "whisperServerUrl",
            "value": url,
            "applied": False,
            "dry_run": dry_run,
        }
        db_path = storyteller_db or self.settings.storyteller_db_path
        if dry_run:
            action["note"] = "dry-run: would UPDATE settings SET value=whisperServerUrl"
            return action
        if not db_path.is_file():
            action["error"] = "storyteller_db_missing"
            return action
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT uuid, value FROM settings WHERE name = ?", ("whisperServerUrl",)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                    (json.dumps(url), "whisperServerUrl"),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO settings (uuid, name, value, created_at, updated_at)
                    VALUES (?, 'whisperServerUrl', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (str(uuid4()), json.dumps(url)),
                )
            conn.commit()
            action["applied"] = True
        finally:
            conn.close()
        return action

    def storyteller_token(self) -> str | None:
        data = urllib.parse.urlencode(
            {"username": "gate4poc", "password": "Gate4Poc!Align2026"}
        ).encode()
        api_base = self.settings.storyteller_url.rstrip("/")
        req = urllib.request.Request(
            f"{api_base}/api/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r).get("access_token")
        except Exception:
            return None

    def find_book(self, title: str) -> dict[str, Any] | None:
        tok = self.storyteller_token()
        db_path = self.settings.storyteller_db_path
        if not tok:
            if not db_path.is_file():
                return None
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT uuid, title FROM book WHERE title = ? COLLATE NOCASE", (title,)
                ).fetchone()
                if not row:
                    return None
                ra = conn.execute(
                    "SELECT status, current_stage, stage_progress, filepath "
                    "FROM readaloud WHERE book_uuid = ?",
                    (row["uuid"],),
                ).fetchone()
                return {
                    "uuid": row["uuid"],
                    "title": row["title"],
                    "readaloud": dict(ra) if ra else None,
                }
            finally:
                conn.close()
        api_base = self.settings.storyteller_url.rstrip("/")
        req = urllib.request.Request(
            f"{api_base}/api/v2/books",
            headers={"Authorization": f"Bearer {tok}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                payload = json.load(r)
        except Exception:
            return None
        books = (
            payload
            if isinstance(payload, list)
            else (payload.get("books") or payload.get("items") or [])
        )
        needle = title.strip().lower()
        for b in books:
            if str(b.get("title") or "").strip().lower() == needle:
                return b
        return None

    def readaloud_status(self, title: str) -> dict[str, Any]:
        found = self.find_book(title)
        ra = (found or {}).get("readaloud") or (found or {}).get("readAloud") or {}
        return {
            "found": bool(found),
            "uuid": (found or {}).get("uuid"),
            "status": str(ra.get("status") or "").upper(),
            "stage": ra.get("currentStage") or ra.get("current_stage"),
            "progress": ra.get("stageProgress")
            if ra.get("stageProgress") is not None
            else ra.get("stage_progress"),
            "aligned_at": (found or {}).get("alignedAt") or (found or {}).get("aligned_at"),
        }

    def dispatch_alignment(
        self, title: str, *, dry_run: bool = False, restart: str | None = "full"
    ) -> dict[str, Any]:
        found = self.find_book(title)
        if dry_run:
            return {"dry_run": True, "found": bool(found), "uuid": (found or {}).get("uuid")}
        if not found or not found.get("uuid"):
            return {"ok": False, "error": "storyteller_book_not_found", "title": title}
        uuid = found["uuid"]
        tok = self.storyteller_token()
        if not tok:
            return {"ok": False, "error": "storyteller_auth_failed", "uuid": uuid}
        path = f"/api/v2/books/{uuid}/process"
        if restart:
            path += f"?restart={urllib.parse.quote(str(restart))}"
        url = f"{self.settings.storyteller_url.rstrip('/')}{path}"
        req = urllib.request.Request(
            url, data=b"", headers={"Authorization": f"Bearer {tok}"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8", errors="replace")
                return {"ok": True, "uuid": uuid, "http": r.status, "body": body[:300]}
        except urllib.error.HTTPError as e:
            return {"ok": False, "uuid": uuid, "http": e.code, "error": e.reason}
        except Exception as e:
            return {"ok": False, "uuid": uuid, "error": str(e)}

    def find_readaloud_epub(self, title: str) -> str | None:
        lib = self.settings.books_dir / title
        if lib.is_dir():
            hits = sorted(lib.glob("*readaloud*.epub"))
            if hits:
                return str(hits[0])
        db_path = self.settings.storyteller_db_path
        if not db_path.is_file():
            return None
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                """
                SELECT r.filepath FROM readaloud r
                JOIN book b ON b.uuid = r.book_uuid
                WHERE b.title = ? COLLATE NOCASE
                ORDER BY r.updated_at DESC LIMIT 1
                """,
                (title,),
            ).fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return None
        raw = str(row[0])
        if raw.startswith("/library/"):
            candidate = REPO_ROOT / raw.lstrip("/")
        else:
            candidate = Path(raw)
        return str(candidate) if candidate.is_file() else None

    def worker_token(self) -> str:
        return str(uuid4())

    def promote_fine_cas(
        self,
        repo: Any,
        *,
        book_id: int,
        target_generation_id: str,
        input_epub_fp: str,
        input_audio_fp: str,
        fine_map_path: str,
        fine_table_path: str,
        sentence_count: int,
    ) -> bool:
        return repo.promote_fine_alignment_cas(
            book_id,
            target_generation_id,
            input_epub_fp,
            input_audio_fp,
            fine_map_path,
            fine_table_path,
            sentence_count,
        )
