"""Shared HTTP JSON helpers."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> tuple[int, Any]:
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            code = resp.getcode() or 200
            if not raw:
                return code, None
            try:
                return code, json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return code, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except json.JSONDecodeError:
            payload = raw.decode("utf-8", errors="replace") if raw else None
        return exc.code, payload
    except urllib.error.URLError as exc:
        raise RuntimeError(f"request failed: {url}: {exc}") from exc
