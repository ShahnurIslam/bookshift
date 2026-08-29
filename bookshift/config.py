"""Centralized typed configuration for BookShift."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _first_nonempty(*values: str | None) -> str:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _resolve_path(raw: str, default: Path) -> Path:
    if not raw:
        return default.resolve()
    p = Path(raw)
    return p.resolve() if p.is_absolute() else (REPO_ROOT / p).resolve()


@dataclass(frozen=True)
class Settings:
    """Runtime configuration loaded from defaults, dotenv file, and environment."""

    db_path: Path = field(default_factory=lambda: REPO_ROOT / "analysis" / "pipeline_state.db")
    sync_host: str = "0.0.0.0"
    sync_port: int = 18001
    abs_url: str = "http://localhost:13378"
    abs_token: str = ""
    abs_db_path: Path | None = None
    bookorbit_url: str = "http://localhost:3010"
    bookorbit_username: str = ""
    bookorbit_password: str = ""
    m1_whisper_url: str = "http://localhost:8000"
    storyteller_url: str = "http://localhost:18002"
    storyteller_username: str = ""
    storyteller_password: str = ""
    storyteller_db_path: Path = field(default_factory=lambda: REPO_ROOT / "data" / "storyteller.db")
    books_dir: Path = field(default_factory=lambda: REPO_ROOT / "library")
    audiobooks_dir: Path = field(default_factory=lambda: REPO_ROOT / "library")
    analysis_dir: Path = field(default_factory=lambda: REPO_ROOT / "analysis")
    config_path: Path | None = None

    @classmethod
    def load(cls, *, config_path: Path | None = None) -> Settings:
        cfg_path = config_path
        if cfg_path is None:
            env_cfg = os.environ.get("BOOKSHIFT_CONFIG_PATH", "").strip()
            cfg_path = Path(env_cfg) if env_cfg else REPO_ROOT / ".env"
        if not cfg_path.is_absolute():
            cfg_path = (REPO_ROOT / cfg_path).resolve()
        dotenv = _load_dotenv(cfg_path) if cfg_path.is_file() else {}

        def from_sources(*env_keys: str, dot_key: str = "", default: str = "") -> str:
            env_vals = [os.environ.get(k) for k in env_keys]
            dot_val = dotenv.get(dot_key) if dot_key else None
            return _first_nonempty(*env_vals, dot_val, default)

        abs_db_raw = from_sources(
            "BOOKSHIFT_ABS_DB_PATH",
            "ABS_DB_PATH",
            dot_key="ABS_DB_PATH",
        )
        abs_db_path = Path(abs_db_raw).resolve() if abs_db_raw else None

        sync_port_raw = from_sources("BOOKSHIFT_SYNC_PORT", default="18001")
        try:
            sync_port = int(sync_port_raw)
        except ValueError:
            sync_port = 18001

        return cls(
            db_path=_resolve_path(
                from_sources("BOOKSHIFT_DB_PATH"),
                REPO_ROOT / "analysis" / "pipeline_state.db",
            ),
            sync_host=from_sources("BOOKSHIFT_SYNC_HOST", default="0.0.0.0"),
            sync_port=sync_port,
            abs_url=from_sources(
                "BOOKSHIFT_ABS_URL",
                "ABS_URL",
                dot_key="ABS_URL",
                default="http://localhost:13378",
            ),
            abs_token=from_sources(
                "BOOKSHIFT_ABS_TOKEN",
                "ABS_TOKEN",
                "ABS_API_TOKEN",
                dot_key="ABS_API_TOKEN",
            )
            or from_sources(dot_key="ABS_TOKEN"),
            abs_db_path=abs_db_path,
            bookorbit_url=from_sources(
                "BOOKSHIFT_BOOKORBIT_URL",
                "BOOKORBIT_URL",
                dot_key="BOOKORBIT_URL",
                default="http://localhost:3010",
            ),
            bookorbit_username=from_sources(
                "BOOKSHIFT_BOOKORBIT_USERNAME",
                "BOOKORBIT_USERNAME",
                dot_key="BOOKORBIT_USERNAME",
            ),
            bookorbit_password=from_sources(
                "BOOKSHIFT_BOOKORBIT_PASSWORD",
                "BOOKORBIT_PASSWORD",
                dot_key="BOOKORBIT_PASSWORD",
            ),
            m1_whisper_url=from_sources(
                "BOOKSHIFT_M1_WHISPER_URL",
                "M1_WHISPER_URL",
                dot_key="M1_WHISPER_URL",
                default="http://localhost:8000",
            ),
            storyteller_url=from_sources(
                "BOOKSHIFT_STORYTELLER_URL",
                "STORYTELLER_URL",
                dot_key="STORYTELLER_URL",
                default="http://localhost:18002",
            ),
            storyteller_username=from_sources(
                "BOOKSHIFT_STORYTELLER_USERNAME",
                "STORYTELLER_USERNAME",
                dot_key="BOOKSHIFT_STORYTELLER_USERNAME",
            )
            or from_sources(dot_key="STORYTELLER_USERNAME"),
            storyteller_password=from_sources(
                "BOOKSHIFT_STORYTELLER_PASSWORD",
                "STORYTELLER_PASSWORD",
                dot_key="BOOKSHIFT_STORYTELLER_PASSWORD",
            )
            or from_sources(dot_key="STORYTELLER_PASSWORD"),
            storyteller_db_path=_resolve_path(
                from_sources("BOOKSHIFT_STORYTELLER_DB_PATH"),
                REPO_ROOT / "data" / "storyteller.db",
            ),
            books_dir=_resolve_path(
                from_sources("BOOKSHIFT_BOOKS_DIR"),
                REPO_ROOT / "library",
            ),
            audiobooks_dir=_resolve_path(
                from_sources("BOOKSHIFT_AUDIOBOOKS_DIR"),
                REPO_ROOT / "library",
            ),
            analysis_dir=_resolve_path(
                from_sources("BOOKSHIFT_ANALYSIS_DIR"),
                REPO_ROOT / "analysis",
            ),
            config_path=cfg_path if cfg_path.is_file() else None,
        )

    def sync_api_base(self) -> str:
        """Public base URL for sync API (127.0.0.1 when bound to 0.0.0.0)."""
        host = self.sync_host
        if host in ("0.0.0.0", "::", ""):
            host = "127.0.0.1"
        return f"http://{host}:{self.sync_port}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "db_path": str(self.db_path),
            "sync_host": self.sync_host,
            "sync_port": self.sync_port,
            "abs_url": self.abs_url,
            "bookorbit_url": self.bookorbit_url,
            "m1_whisper_url": self.m1_whisper_url,
            "storyteller_url": self.storyteller_url,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.load()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
