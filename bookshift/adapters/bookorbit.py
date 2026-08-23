"""BookOrbit REST adapter (no container coupling)."""

from __future__ import annotations

import urllib.parse
from typing import Any

from bookshift.adapters.http_util import http_json
from bookshift.config import Settings, get_settings


class BookOrbitAdapter:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._token: str | None = None

    @property
    def base_url(self) -> str:
        return self.settings.bookorbit_url.rstrip("/")

    def authenticate(
        self, username: str | None = None, password: str | None = None
    ) -> str:
        """Login and cache bearer token."""
        self._token = self.login(username=username, password=password)
        return self._token

    def login(self, username: str | None = None, password: str | None = None) -> str:
        user = username or self.settings.bookorbit_username
        pwd = password or self.settings.bookorbit_password
        code, payload = http_json(
            "POST",
            f"{self.base_url}/api/v1/auth/login",
            body={"username": user, "password": pwd},
        )
        if code != 200 or not isinstance(payload, dict) or not payload.get("accessToken"):
            raise RuntimeError(f"BookOrbit login failed HTTP {code}: {payload}")
        return str(payload["accessToken"])

    def _auth_headers(self, token: str | None = None) -> dict[str, str]:
        tok = token or self._token
        if not tok:
            tok = self.authenticate()
        return {"Authorization": f"Bearer {tok}"}

    def find_book(
        self, token: str, title: str, author_hint: str | None = None
    ) -> dict[str, Any] | None:
        q = urllib.parse.quote(title)
        headers = self._auth_headers(token)
        code, results = http_json(
            "GET",
            f"{self.base_url}/api/v1/books/search?q={q}",
            headers=headers,
        )
        if code != 200 or not isinstance(results, list):
            raise RuntimeError(f"BookOrbit search failed HTTP {code}: {results}")
        scored: list[tuple[int, dict[str, Any]]] = []
        for row in results:
            t = (row.get("title") or "").strip()
            authors = " ".join(row.get("authors") or [])
            score = 0
            if t.lower() == title.lower():
                score += 12
            elif title.lower() in t.lower():
                score += 4
            if author_hint and author_hint.lower() in authors.lower():
                score += 3
            scored.append((score, row))
        if not scored:
            return None
        scored.sort(key=lambda x: (-x[0], len(x[1].get("title") or "")))
        book_id = scored[0][1].get("id")
        code, detail = http_json(
            "GET",
            f"{self.base_url}/api/v1/books/{book_id}",
            headers=headers,
        )
        if code != 200 or not isinstance(detail, dict):
            return scored[0][1]
        return detail

    def get_book_progress(
        self, book_id: int, file_id: int | None = None, *, token: str | None = None
    ) -> dict[str, Any]:
        headers = self._auth_headers(token)
        if file_id is not None:
            code, payload = http_json(
                "GET",
                f"{self.base_url}/api/v1/books/files/{file_id}/progress",
                headers=headers,
            )
            return {"http_code": code, "file_id": file_id, "progress": payload}
        code, payload = http_json(
            "GET",
            f"{self.base_url}/api/v1/books/{book_id}/progress",
            headers=headers,
        )
        if code != 200:
            raise RuntimeError(f"BookOrbit progress failed HTTP {code}: {payload}")
        return {"http_code": code, "book_id": book_id, "progress": payload}

    def get_progress(self, token: str, book_id: int) -> list[dict[str, Any]]:
        result = self.get_book_progress(book_id, token=token)
        payload = result.get("progress")
        return payload if isinstance(payload, list) else []

    def get_file_progress(self, token: str, file_id: int) -> tuple[int, Any]:
        headers = self._auth_headers(token)
        return http_json(
            "GET",
            f"{self.base_url}/api/v1/books/files/{file_id}/progress",
            headers=headers,
        )

    def update_book_progress(
        self,
        book_id: int,
        file_id: int,
        progress_data: dict[str, Any],
        *,
        token: str | None = None,
    ) -> tuple[int, Any]:
        """POST progress payload for a primary ebook file."""
        _ = book_id  # REST API scopes by file id
        headers = self._auth_headers(token)
        return http_json(
            "POST",
            f"{self.base_url}/api/v1/books/files/{file_id}/progress",
            headers=headers,
            body=progress_data,
        )

    def update_progress(
        self, token: str, file_id: int, payload: dict[str, Any]
    ) -> tuple[int, Any]:
        return self.update_book_progress(0, file_id, payload, token=token)
