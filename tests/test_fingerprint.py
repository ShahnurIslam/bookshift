"""Content fingerprint determinism and change detection."""

from __future__ import annotations

import zipfile
from pathlib import Path

from bookshift.domain.fingerprint import (
    compute_audio_fingerprint,
    compute_epub_fingerprint,
    generate_generation_id,
)


def _minimal_epub(path: Path, *, spine_href: str = "chapter1.xhtml", spine_size: int = 100) -> None:
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
    opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">test-book-001</dc:identifier>
  </metadata>
  <manifest>
    <item id="c1" href="{href}" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="c1"/>
  </spine>
</package>""".format(href=spine_href)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("content.opf", opf)
        zf.writestr(spine_href, "x" * spine_size)


def test_generate_generation_id_unique():
    a = generate_generation_id()
    b = generate_generation_id()
    assert a != b
    assert len(a) == 36


def test_epub_fingerprint_deterministic(tmp_path: Path):
    epub = tmp_path / "book.epub"
    _minimal_epub(epub)
    fp1 = compute_epub_fingerprint(epub)
    fp2 = compute_epub_fingerprint(epub)
    assert fp1 == fp2
    assert len(fp1) == 64


def test_epub_fingerprint_detects_content_change(tmp_path: Path):
    epub_a = tmp_path / "a.epub"
    epub_b = tmp_path / "b.epub"
    _minimal_epub(epub_a, spine_size=100)
    _minimal_epub(epub_b, spine_size=200)
    assert compute_epub_fingerprint(epub_a) != compute_epub_fingerprint(epub_b)


def test_audio_fingerprint_deterministic(tmp_path: Path):
    audio = tmp_path / "book.m4b"
    audio.write_bytes(b"\x00" * 128)
    chapters = [{"title": "Ch1", "start": 0.0, "end": 120.0}]
    fp1 = compute_audio_fingerprint(audio, chapters, duration=120.0)
    fp2 = compute_audio_fingerprint(audio, chapters, duration=120.0)
    assert fp1 == fp2


def test_audio_fingerprint_detects_chapter_change(tmp_path: Path):
    audio = tmp_path / "book.m4b"
    audio.write_bytes(b"\x00" * 128)
    ch_a = [{"title": "Ch1", "start": 0.0, "end": 120.0}]
    ch_b = [{"title": "Ch1", "start": 0.0, "end": 130.0}]
    assert compute_audio_fingerprint(audio, ch_a, duration=120.0) != compute_audio_fingerprint(
        audio, ch_b, duration=130.0
    )
