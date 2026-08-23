"""EPUB Canonical Fragment Identifier (CFI) helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

from bookshift.domain.locators.crengine import ChapterDom, local_tag

_CFI_RE = re.compile(
    r"^epubcfi\((?P<body>/[^)]+?)(?::(?P<offset>\d+))?(?:,(?P<range>[^)]+))?\)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedCfi:
    spine_steps: tuple[int, ...]
    content_steps: tuple[tuple[str, int], ...]
    char_offset: int
    indirection: bool


def parse_cfi(cfi: str) -> ParsedCfi | None:
    raw = (cfi or "").strip()
    m = _CFI_RE.match(raw)
    if not m:
        return None
    body = m.group("body")
    offset = int(m.group("offset") or 0)
    parts = body.split("!")
    spine_part = parts[0]
    content_part = parts[1] if len(parts) > 1 else ""
    spine_steps = tuple(int(x) for x in re.findall(r"/(\d+)", spine_part))
    content_steps: list[tuple[str, int]] = []
    for step in re.finditer(r"/(\d+)(?:\[([^\]]+)\])?", content_part):
        content_steps.append(("node", int(step.group(1))))
    return ParsedCfi(
        spine_steps=spine_steps,
        content_steps=tuple(content_steps),
        char_offset=offset,
        indirection="!" in body,
    )


def _cfi_child_index(parent: ET.Element, target: ET.Element) -> int:
    """CFI even index for an element child (IDPF child sequence)."""
    index = 0
    for child in parent:
        if child is target:
            return index + 2 if index else 2
        if child.tag is not ET.Comment:
            index += 1
    return 2


def _cfi_path_for_element(body: ET.Element, target: ET.Element) -> list[int]:
    chain: list[ET.Element] = []
    node: ET.Element | None = target
    while node is not None:
        chain.append(node)
        if node is body:
            break
        node = _parent(body, node)
    chain.reverse()
    steps: list[int] = []
    for i in range(1, len(chain)):
        parent = chain[i - 1]
        steps.append(_cfi_child_index(parent, chain[i]))
    return steps


def _parent(body: ET.Element, node: ET.Element) -> ET.Element | None:
    for parent in body.iter():
        for child in list(parent):
            if child is node:
                return parent
    return None


def point_to_cfi(
    *,
    spine_cfi_index: int,
    package_path_steps: tuple[int, ...] = (6,),
    chapter: ChapterDom,
    element: ET.Element,
    char_offset: int = 0,
) -> str:
    """Build intra-publication CFI for a point in a spine chapter."""
    content_steps = _cfi_path_for_element(chapter.body, element)
    # CFI text offset uses odd step before final char offset when needed.
    if content_steps:
        content_steps = content_steps + [1]
    spine = "/".join(str(s) for s in (*package_path_steps, spine_cfi_index))
    content = "/".join(str(s) for s in content_steps)
    return f"epubcfi(/{spine}!/{content}:{int(char_offset)})"


def spine_cfi_index_for_href(spine_hrefs: list[str], href: str) -> int:
    """Map spine href to CFI manifest index (even numbers, 2-based itemrefs)."""
    target = href.split("/")[-1].lower()
    for i, h in enumerate(spine_hrefs):
        if h.split("/")[-1].lower() == target:
            return (i + 1) * 2
    for i, h in enumerate(spine_hrefs):
        if target in h.lower() or h.lower().endswith(target):
            return (i + 1) * 2
    return 2


def cfi_for_collapsed_point(
    *,
    spine_hrefs: list[str],
    chapter: ChapterDom,
    chapter_index: int,
    collapsed_cp: int,
) -> str | None:
    from bookshift.domain.locators.crengine import _location_at_collapsed_cp

    resolved = _location_at_collapsed_cp(chapter, collapsed_cp)
    if resolved is None:
        return None
    seg, _text_index, char_offset = resolved
    spine_idx = spine_cfi_index_for_href(spine_hrefs, chapter.href)
    return point_to_cfi(
        spine_cfi_index=spine_idx,
        chapter=chapter,
        element=seg.host_element,
        char_offset=char_offset,
    )
