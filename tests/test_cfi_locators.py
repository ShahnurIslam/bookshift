"""EPUB CFI locator tests."""

from __future__ import annotations

from bookshift.domain.locators.cfi import parse_cfi

FIXTURE_CFI = "epubcfi(/6/16!/4/30/2/1:0)"


def test_parse_fixture_cfi():
    parsed = parse_cfi(FIXTURE_CFI)
    assert parsed is not None
    assert parsed.indirection is True
    assert parsed.char_offset == 0
    assert 16 in parsed.spine_steps


def test_parse_cfi_without_offset():
    parsed = parse_cfi("epubcfi(/6/4!/4/2)")
    assert parsed is not None
    assert parsed.char_offset == 0
