"""KOReader / CREngine XPointer helpers."""

from __future__ import annotations

import re
from urllib.parse import quote, urlencode

from bookshift.config import get_settings

_DF_RE = re.compile(r"DocFragment\[(\d+)\]")
POSITION_PATH = "/api/v1/sync/position"


def parse_doc_fragment_index(xpointer: str) -> int | None:
    m = _DF_RE.search(xpointer or "")
    return int(m.group(1)) if m else None


def is_valid_crengine_xpointer(xpointer: str) -> bool:
    return isinstance(xpointer, str) and xpointer.startswith("/body/DocFragment[")


def build_xpointer_url(
    xpointer: str,
    *,
    book_id: int,
    base: str | None = None,
) -> str:
    settings = get_settings()
    base = (base or settings.sync_api_base()).rstrip("/")
    q = urlencode({"book_id": int(book_id), "xpointer": xpointer}, quote_via=quote)
    return f"{base}{POSITION_PATH}?{q}"


def build_timestamp_url(
    timestamp: float,
    *,
    book_id: int,
    base: str | None = None,
) -> str:
    settings = get_settings()
    base = (base or settings.sync_api_base()).rstrip("/")
    q = urlencode({"book_id": int(book_id), "timestamp": timestamp}, quote_via=quote)
    return f"{base}{POSITION_PATH}?{q}"


def format_bookorbit_progress_payload(
    *,
    percentage: float,
    xpointer: str | None,
    cfi: str | None = None,
) -> dict:
    locator = xpointer
    if isinstance(xpointer, str) and xpointer.startswith("/body/DocFragment"):
        locator = xpointer
    payload: dict = {
        "percentage": percentage,
        "cfi": cfi,
        "pageNumber": None,
        "positionSeconds": None,
        "koreaderProgress": locator,
        "koboLocationSource": None,
        "koboLocationType": None,
        "koboLocationValue": None,
        "koboContentSourceProgressPercent": None,
    }
    return payload
