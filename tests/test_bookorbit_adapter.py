"""BookOrbit REST adapter tests (mocked HTTP)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bookshift.adapters.bookorbit import BookOrbitAdapter
from bookshift.config import Settings


@pytest.fixture
def adapter():
    settings = Settings(
        bookorbit_url="http://bookorbit.test:3010",
        bookorbit_username="user",
        bookorbit_password="pass",
    )
    return BookOrbitAdapter(settings)


def test_authenticate(adapter: BookOrbitAdapter):
    with patch(
        "bookshift.adapters.bookorbit.http_json",
        return_value=(200, {"accessToken": "tok-123"}),
    ):
        token = adapter.authenticate()
    assert token == "tok-123"
    assert adapter._token == "tok-123"


def test_get_book_progress_file(adapter: BookOrbitAdapter):
    adapter._token = "tok-123"
    with patch(
        "bookshift.adapters.bookorbit.http_json",
        return_value=(200, {"percentage": 0.42}),
    ) as mocked:
        result = adapter.get_book_progress(226, file_id=356)
    assert result["http_code"] == 200
    assert result["progress"]["percentage"] == 0.42
    args = mocked.call_args[0]
    assert "/files/356/progress" in args[1]


def test_update_book_progress(adapter: BookOrbitAdapter):
    adapter._token = "tok-123"
    payload = {
        "percentage": 0.5,
        "cfi": "epubcfi(/6/16!/4/2/1:0)",
        "koreaderProgress": "/body/DocFragment[8]/body/p[1]/text().0",
    }
    with patch(
        "bookshift.adapters.bookorbit.http_json",
        return_value=(201, {"ok": True}),
    ) as mocked:
        code, body = adapter.update_book_progress(226, 356, payload)
    assert code == 201
    assert body == {"ok": True}
    assert mocked.call_args.kwargs["body"] == payload


def test_adapter_has_no_docker_helpers(adapter: BookOrbitAdapter):
    assert not hasattr(adapter, "run_position_converter")
