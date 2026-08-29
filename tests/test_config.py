"""Settings loading and override tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from bookshift.config import Settings, get_settings, reset_settings_cache


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    reset_settings_cache()
    yield
    reset_settings_cache()


def test_default_settings_use_repo_relative_paths():
    s = Settings.load()
    assert s.db_path.name == "pipeline_state.db"
    assert "analysis" in str(s.db_path)
    assert s.sync_port == 18001
    assert s.sync_host == "0.0.0.0"
    assert s.abs_url == "http://localhost:13378"
    assert s.bookorbit_url == "http://localhost:3010"
    assert s.m1_whisper_url == "http://localhost:8000"
    assert s.storyteller_url == "http://localhost:18002"


def test_env_override(monkeypatch):
    monkeypatch.setenv("BOOKSHIFT_SYNC_PORT", "19999")
    monkeypatch.setenv("BOOKSHIFT_M1_WHISPER_URL", "http://whisper.test:9000")
    monkeypatch.setenv("BOOKSHIFT_STORYTELLER_USERNAME", "fixture-user")
    monkeypatch.setenv("BOOKSHIFT_STORYTELLER_PASSWORD", "fixture-password")
    reset_settings_cache()
    s = Settings.load()
    assert s.sync_port == 19999
    assert s.m1_whisper_url == "http://whisper.test:9000"
    assert s.storyteller_username == "fixture-user"
    assert s.storyteller_password == "fixture-password"


def test_legacy_abs_token_env(monkeypatch):
    monkeypatch.setenv("ABS_TOKEN", "legacy-token-value")
    reset_settings_cache()
    s = Settings.load()
    assert s.abs_token == "legacy-token-value"


def test_sync_api_base_localhost_when_bind_all():
    s = Settings.load()
    assert s.sync_api_base() == "http://127.0.0.1:18001"


def test_get_settings_singleton():
    reset_settings_cache()
    a = get_settings()
    b = get_settings()
    assert a is b
