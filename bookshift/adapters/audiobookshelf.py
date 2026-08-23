"""Audiobookshelf REST adapter."""

from __future__ import annotations

import sqlite3
import urllib.parse
from typing import Any

from bookshift.adapters.http_util import http_json
from bookshift.config import Settings, get_settings


class AudiobookshelfAdapter:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def resolve_token(self) -> str:
        token = (self.settings.abs_token or "").strip()
        if token:
            return token
        abs_db = self.settings.abs_db_path
        if abs_db and abs_db.is_file():
            con = sqlite3.connect(f"file:{abs_db}?mode=ro", uri=True)
            try:
                row = con.execute(
                    "SELECT token FROM users WHERE token IS NOT NULL AND token != '' "
                    "ORDER BY CASE username WHEN 'root' THEN 0 ELSE 1 END LIMIT 1"
                ).fetchone()
            finally:
                con.close()
            if row and row[0]:
                return str(row[0])
        raise RuntimeError(
            "No Audiobookshelf API token. Set ABS_TOKEN / BOOKSHIFT_ABS_TOKEN "
            "or configure ABS_DB_PATH."
        )

    def get_progress(self, library_item_id: str, token: str | None = None) -> dict[str, Any] | None:
        tok = token or self.resolve_token()
        base = self.settings.abs_url.rstrip("/")
        headers = {"Authorization": f"Bearer {tok}"}
        code, payload = http_json(
            "GET",
            f"{base}/api/me/progress/{library_item_id}",
            headers=headers,
        )
        if code == 200 and isinstance(payload, dict) and payload.get("libraryItemId"):
            return payload
        code, me = http_json("GET", f"{base}/api/me", headers=headers)
        if code != 200:
            return None
        user = me.get("user") if isinstance(me, dict) else None
        user = user or (me if isinstance(me, dict) else {})
        for prog in user.get("mediaProgress") or []:
            if prog.get("libraryItemId") == library_item_id:
                return prog
        return None

    def update_progress(
        self,
        library_item_id: str,
        progress: dict[str, Any],
        token: str | None = None,
    ) -> tuple[int, Any]:
        """Persist progress through Audiobookshelf's public progress endpoint."""
        tok = token or self.resolve_token()
        return http_json(
            "PATCH",
            f"{self.settings.abs_url.rstrip('/')}/api/me/progress/{library_item_id}",
            headers={"Authorization": f"Bearer {tok}"},
            body=progress,
        )

    def get_chapters(
        self, item_id: str, token: str | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        tok = token or self.resolve_token()
        base = self.settings.abs_url.rstrip("/")
        code, item = http_json(
            "GET",
            f"{base}/api/items/{item_id}?expanded=1",
            headers={"Authorization": f"Bearer {tok}"},
        )
        if code != 200 or not isinstance(item, dict):
            raise RuntimeError(f"ABS item fetch failed HTTP {code}: {item}")
        media = item.get("media") or {}
        raw = media.get("chapters") or item.get("chapters") or []
        if not raw:
            for af in media.get("audioFiles") or []:
                embedded = af.get("chapters") or []
                if embedded:
                    raw = embedded
                    break
        chapters: list[dict[str, Any]] = []
        for i, c in enumerate(raw):
            start = float(c.get("start") if c.get("start") is not None else 0.0)
            end = c.get("end")
            if end is None:
                if i + 1 < len(raw) and raw[i + 1].get("start") is not None:
                    end = float(raw[i + 1]["start"])
                else:
                    end = start
            chapters.append(
                {
                    "index": i,
                    "id": c.get("id", i),
                    "title": str(c.get("title") or f"chapter-{i}"),
                    "start": start,
                    "end": float(end),
                }
            )
        duration = float(media.get("duration") or item.get("duration") or 0.0)
        if not chapters and duration > 0:
            chapters = [
                {"index": 0, "id": 0, "title": "full", "start": 0.0, "end": duration}
            ]
        meta = {
            "item_id": item_id,
            "title": ((media.get("metadata") or {}).get("title") or item.get("title")),
            "duration": duration or media.get("duration") or item.get("duration"),
            "num_chapters": len(chapters),
        }
        return chapters, meta

    def search_item(
        self, title: str, author_hint: str | None = None, token: str | None = None
    ) -> dict[str, Any] | None:
        tok = token or self.resolve_token()
        base = self.settings.abs_url.rstrip("/")
        headers = {"Authorization": f"Bearer {tok}"}
        code, libs = http_json("GET", f"{base}/api/libraries", headers=headers)
        if code != 200:
            raise RuntimeError(f"ABS /api/libraries -> HTTP {code}: {libs}")
        library_list = libs if isinstance(libs, list) else (libs or {}).get("libraries") or []
        q = urllib.parse.quote(title)
        candidates: list[dict[str, Any]] = []
        for lib in library_list:
            lib_id = lib.get("id")
            if not lib_id:
                continue
            code, result = http_json(
                "GET",
                f"{base}/api/libraries/{lib_id}/search?q={q}",
                headers=headers,
            )
            if code != 200 or not isinstance(result, dict):
                continue
            for entry in result.get("book") or []:
                item = entry.get("libraryItem") or entry
                meta = (item.get("media") or {}).get("metadata") or {}
                item_title = (meta.get("title") or item.get("title") or "").strip()
                authors = meta.get("authors") or []
                author_names = " ".join(
                    a.get("name", "") if isinstance(a, dict) else str(a) for a in authors
                )
                score = 0
                if item_title.lower() == title.lower():
                    score += 10
                elif title.lower() in item_title.lower():
                    score += 5
                if author_hint and author_hint.lower() in author_names.lower():
                    score += 3
                candidates.append({"score": score, "item": item})
        if not candidates:
            return None
        candidates.sort(key=lambda c: c["score"], reverse=True)
        item = candidates[0]["item"]
        item_id = item.get("id")
        code, expanded = http_json(
            "GET",
            f"{base}/api/items/{item_id}?expanded=1",
            headers=headers,
        )
        if code == 200 and isinstance(expanded, dict):
            return expanded
        return item
