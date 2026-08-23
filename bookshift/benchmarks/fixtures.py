"""Benchmark fixture catalog loading."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bookshift.config import REPO_ROOT

DEFAULT_CATALOG = REPO_ROOT / "data" / "benchmarks" / "catalog.json"


@dataclass(frozen=True)
class BookFixture:
    key: str
    title: str
    tier: int
    epub: Path
    audio_duration_seconds: float
    coarse_map: Path | None = None
    fine_alignment_map: Path | None = None
    fine_locator_table: Path | None = None
    storyteller_extract: Path | None = None
    logical_book_id: int | None = None
    compile_logical_book_id: int | None = None
    synthetic_abs: bool = False
    public_domain: bool = False
    audio_dir: Path | None = None

    def epub_exists(self) -> bool:
        return self.epub.is_file()

    def has_fine_ground_truth(self) -> bool:
        return self.fine_locator_table is not None and self.fine_locator_table.is_file()


def _resolve(raw: str | None) -> Path | None:
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_absolute() else (REPO_ROOT / p).resolve()


def load_catalog(path: Path | None = None) -> list[BookFixture]:
    catalog_path = path or DEFAULT_CATALOG
    doc = json.loads(catalog_path.read_text(encoding="utf-8"))
    out: list[BookFixture] = []
    for row in doc.get("books") or []:
        epub = _resolve(row.get("epub"))
        if epub is None:
            continue
        out.append(
            BookFixture(
                key=str(row["key"]),
                title=str(row["title"]),
                tier=int(row.get("tier") or 1),
                epub=epub,
                audio_duration_seconds=float(row.get("audio_duration_seconds") or 0),
                coarse_map=_resolve(row.get("coarse_map")),
                fine_alignment_map=_resolve(row.get("fine_alignment_map")),
                fine_locator_table=_resolve(row.get("fine_locator_table")),
                storyteller_extract=_resolve(row.get("storyteller_extract")),
                logical_book_id=row.get("logical_book_id"),
                compile_logical_book_id=row.get("compile_logical_book_id"),
                synthetic_abs=bool(row.get("synthetic_abs")),
                public_domain=bool(row.get("public_domain")),
                audio_dir=_resolve(row.get("audio_dir")),
            )
        )
    return out


def select_fixtures(
    catalog: list[BookFixture],
    *,
    keys: list[str] | None = None,
    tier: int | None = None,
) -> list[BookFixture]:
    selected = catalog
    if keys:
        keyset = set(keys)
        selected = [f for f in selected if f.key in keyset]
    if tier is not None:
        selected = [f for f in selected if f.tier == tier]
    return [f for f in selected if f.epub_exists()]
