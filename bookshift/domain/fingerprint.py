"""Content fingerprinting for versioned alignment generations."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

OPF_NS = {"opf": "http://www.idpf.org/2007/opf"}


def generate_generation_id() -> str:
    return str(uuid4())


def _sha256_hex(parts: list[str]) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def compute_epub_fingerprint(epub_path: Path) -> str:
    """SHA-256 over size, OPF identifiers, and sorted spine filenames + lengths."""
    path = Path(epub_path)
    if not path.is_file():
        raise FileNotFoundError(f"EPUB not found: {path}")

    parts: list[str] = [str(path.name), str(path.stat().st_size)]
    with zipfile.ZipFile(path, "r") as zf:
        opf_name = _find_opf_path(zf)
        opf_data = zf.read(opf_name)
        parts.append(opf_name)
        parts.append(hashlib.sha256(opf_data).hexdigest())

        root = ET.fromstring(opf_data)
        for item in root.findall(".//opf:manifest/opf:item", OPF_NS):
            ident = item.get("id") or ""
            href = item.get("href") or ""
            if ident or href:
                parts.append(f"{ident}:{href}")

        spine_items: list[tuple[str, int]] = []
        manifest = {
            el.get("id"): el.get("href")
            for el in root.findall(".//opf:manifest/opf:item", OPF_NS)
        }
        for itemref in root.findall(".//opf:spine/opf:itemref", OPF_NS):
            idref = itemref.get("idref") or ""
            href = manifest.get(idref) or idref
            try:
                info = zf.getinfo(href)
                spine_items.append((href, int(info.file_size)))
            except KeyError:
                spine_items.append((href, 0))

        for href, size in sorted(spine_items):
            parts.append(f"{href}:{size}")

    return _sha256_hex(parts)


def _find_opf_path(zf: zipfile.ZipFile) -> str:
    try:
        container = zf.read("META-INF/container.xml")
    except KeyError as exc:
        raise ValueError("EPUB missing META-INF/container.xml") from exc
    root = ET.fromstring(container)
    for el in root.iter():
        if el.tag.endswith("rootfile"):
            full = el.get("full-path")
            if full:
                return full
    raise ValueError("EPUB container.xml has no rootfile")


def compute_audio_fingerprint(
    audio_path: Path,
    chapters: list[dict[str, Any]] | None = None,
    *,
    duration: float | None = None,
) -> str:
    """SHA-256 over file size, duration, and chapter boundary list."""
    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {path}")

    parts: list[str] = [str(path.name), str(path.stat().st_size)]

    total_duration = duration
    if total_duration is None and chapters:
        ends = [float(c.get("end") or 0) for c in chapters]
        total_duration = max(ends) if ends else 0.0
    if total_duration is None:
        total_duration = 0.0
    parts.append(f"duration:{total_duration:.6f}")

    if chapters:
        bounds: list[tuple[str, float, float]] = []
        for c in chapters:
            title = str(c.get("title") or c.get("abs_title") or "")
            start = float(c.get("start") if c.get("start") is not None else c.get("abs_start") or 0)
            end = float(c.get("end") if c.get("end") is not None else c.get("abs_end") or start)
            bounds.append((title, start, end))
        for title, start, end in sorted(bounds, key=lambda x: (x[1], x[2], x[0])):
            parts.append(f"{title}|{start:.6f}|{end:.6f}")

    return _sha256_hex(parts)
