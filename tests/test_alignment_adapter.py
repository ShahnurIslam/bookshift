"""Storyteller alignment adapter authentication tests."""

from __future__ import annotations

import urllib.parse

from bookshift.adapters.alignment import AlignmentAdapter
from bookshift.config import Settings


class _TokenResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return b'{"access_token":"fixture-token"}'


def test_storyteller_credentials_are_sent_from_settings(monkeypatch):
    submitted: dict[str, list[str]] = {}

    def fake_urlopen(request, timeout):
        assert timeout == 15
        submitted.update(urllib.parse.parse_qs(request.data.decode()))
        return _TokenResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = AlignmentAdapter(
        Settings(
            storyteller_url="https://storyteller.example.invalid",
            storyteller_username="fixture-user",
            storyteller_password="fixture-password",
        )
    )

    assert adapter.storyteller_token() == "fixture-token"
    assert submitted == {
        "username": ["fixture-user"],
        "password": ["fixture-password"],
    }


def test_storyteller_token_requires_explicit_credentials(monkeypatch):
    calls = 0

    def unexpected_urlopen(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _TokenResponse()

    monkeypatch.setattr("urllib.request.urlopen", unexpected_urlopen)
    adapter = AlignmentAdapter(
        Settings(storyteller_username="", storyteller_password="")
    )

    assert adapter.storyteller_token() is None
    assert calls == 0

