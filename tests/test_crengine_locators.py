"""CREngine XPointer unit tests."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from bookshift.domain.locators.compiler import load_chapter, read_epub_spine
from bookshift.domain.locators.crengine import (
    build_xpointer,
    collapsed_point_to_xpointer,
    normalize_for_search,
    parse_xpointer,
    search_forward,
    xpointer_point_to_collapsed,
)

FIXTURE_XP = "/body/DocFragment[8]/body/p[11]/span/text().0"
# Optional local EPUB used for golden XPointer mapping; not shipped in public export.
EPUB = Path(__file__).resolve().parent.parent / "library" / "sample_book.epub"
TABLE = Path(__file__).resolve().parent.parent / "analysis" / "exact_locator_table.json"


def _chapter_8():
    if not EPUB.is_file():
        pytest.skip("optional sample EPUB not available")
    spine = read_epub_spine(EPUB)
    href = spine[7]
    return 7, load_chapter(EPUB, href)


def test_parse_xpointer_fixture():
    parsed = parse_xpointer(FIXTURE_XP)
    assert parsed is not None
    assert parsed.doc_fragment_index == 8
    assert parsed.chapter_index == 7
    assert parsed.char_offset == 0
    assert parsed.element_steps[-1] == ("span", None)


def test_build_xpointer_roundtrip_format():
    xp = build_xpointer(
        doc_fragment_index=8,
        element_steps=[("body", None), ("p", 11), ("span", None)],
        text_index=1,
        char_offset=0,
    )
    assert xp == FIXTURE_XP


def test_normalize_for_search_collapses_whitespace():
    assert normalize_for_search("  hello   world \n") == "hello world"


def test_fixture_snippet_maps_to_expected_xpointer():
    idx, chapter = _chapter_8()
    match = search_forward(chapter, "No, please no, please don\u2019t, he said.")
    assert match is not None
    xp = collapsed_point_to_xpointer(chapter, idx, match["startCp"])
    assert xp == FIXTURE_XP


def test_golden_xpointer_parse_from_locator_table():
    if not TABLE.is_file():
        pytest.skip("exact_locator_table.json missing")
    locators = json.loads(TABLE.read_text())["locators"][:50]
    for row in locators:
        parsed = parse_xpointer(row["xpointer"])
        assert parsed is not None
        assert parsed.doc_fragment_index == int(row["doc_fragment_index"])


def test_minimal_xhtml_dom_mapping(tmp_path: Path):
    xhtml = """<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>
    <p id="a">Hello <span>world</span>!</p>
    </body></html>"""
    epub = tmp_path / "t.epub"
    with zipfile.ZipFile(epub, "w") as zf:
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>',
        )
        zf.writestr(
            "content.opf",
            """<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">
            <manifest><item id="c1" href="ch.xhtml" media-type="application/xhtml+xml"/></manifest>
            <spine><itemref idref="c1"/></spine></package>""",
        )
        zf.writestr("ch.xhtml", xhtml)
    chapter = load_chapter(epub, "ch.xhtml")
    match = search_forward(chapter, "Hello")
    assert match is not None
    xp = collapsed_point_to_xpointer(chapter, 0, match["startCp"])
    assert xp.startswith("/body/DocFragment[1]/body/")
    assert "/text()." in xp


def test_xpointer_to_collapsed_cp_non_negative():
    idx, chapter = _chapter_8()
    cp = xpointer_point_to_collapsed(chapter, FIXTURE_XP)
    assert cp is not None
    assert cp >= 0
