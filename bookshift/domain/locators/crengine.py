"""CREngine XPointer parsing and generation."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

_SKIP_TAGS = frozenset(
    {"script", "style", "head", "meta", "link", "title", "svg", "template"}
)

_DF_RE = re.compile(r"^/body/DocFragment\[(\d+)\](?P<rest>/.*)?$", re.IGNORECASE)
_SEG_RE = re.compile(
    r"/(?P<kind>text\(\)|[A-Za-z][\w:-]*)(?:\[(?P<index>\d+)\])?"
)


def normalize_for_search(text: str) -> str:
    """Collapse whitespace for text matching (BookOrbit-compatible)."""
    text = unicodedata.normalize("NFC", text or "")
    return re.sub(r"\s+", " ", text).strip()


def local_tag(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


@dataclass(frozen=True)
class ParsedXPointer:
    doc_fragment_index: int
    element_steps: tuple[tuple[str, int | None], ...]
    text_index: int | None
    char_offset: int

    @property
    def chapter_index(self) -> int:
        return self.doc_fragment_index - 1


@dataclass(frozen=True)
class TextSegment:
    host_element: ET.Element
    text_index: int
    raw_text: str
    norm_text: str
    collapsed_start: int
    collapsed_end: int


@dataclass
class ChapterDom:
    href: str
    root: ET.Element
    body: ET.Element
    collapsed: str
    collapsed_length: int
    segments: list[TextSegment]
    _parent_map: dict[int, ET.Element]

    @classmethod
    def from_xhtml(cls, href: str, xhtml: str | bytes) -> ChapterDom:
        if isinstance(xhtml, bytes):
            xhtml = xhtml.decode("utf-8", errors="replace")
        cleaned = re.sub(r"<!DOCTYPE[^>]*>", "", xhtml, flags=re.IGNORECASE)
        cleaned = re.sub(r"<\?xml[^?]*\?>", "", cleaned, flags=re.IGNORECASE)
        root = ET.fromstring(cleaned)
        body = _find_body(root)
        parent_map = _build_parent_map(body)
        collapsed, segments = _build_collapsed_index(body, parent_map)
        return cls(
            href=href,
            root=root,
            body=body,
            collapsed=collapsed,
            collapsed_length=len(collapsed),
            segments=segments,
            _parent_map=parent_map,
        )


def parse_xpointer(xpointer: str) -> ParsedXPointer | None:
    xp = (xpointer or "").strip()
    m = _DF_RE.match(xp)
    if not m:
        return None
    doc_fragment = int(m.group(1))
    rest = m.group("rest") or ""
    if not rest:
        return ParsedXPointer(doc_fragment, (), 1, 0)
    offset_match = re.search(r"\.(\d+)$", rest)
    if not offset_match:
        return None
    char_offset = int(offset_match.group(1))
    path = rest[: offset_match.start()]
    steps: list[tuple[str, int | None]] = []
    text_index: int | None = None
    for seg in _SEG_RE.finditer(path):
        kind = seg.group("kind")
        idx_raw = seg.group("index")
        idx = int(idx_raw) if idx_raw else None
        if kind.lower() == "text()":
            text_index = idx if idx is not None else 1
        else:
            steps.append((kind, idx))
    return ParsedXPointer(doc_fragment, tuple(steps), text_index or 1, char_offset)


def build_xpointer(
    *,
    doc_fragment_index: int,
    element_steps: list[tuple[str, int | None]],
    text_index: int | None = None,
    char_offset: int = 0,
) -> str:
    parts = [f"/body/DocFragment[{doc_fragment_index}]"]
    for tag, idx in element_steps:
        if idx is None:
            parts.append(f"/{tag}")
        else:
            parts.append(f"/{tag}[{idx}]")
    ti = text_index or 1
    if ti <= 1:
        parts.append("/text()")
    else:
        parts.append(f"/text()[{ti}]")
    parts.append(f".{int(char_offset)}")
    return "".join(parts)


def collapsed_point_to_xpointer(chapter: ChapterDom, chapter_index: int, target_cp: int) -> str | None:
    resolved = _location_at_collapsed_cp(chapter, int(target_cp))
    if resolved is None:
        return None
    seg, text_index, char_offset = resolved
    steps = _element_steps(chapter, seg.host_element)
    return build_xpointer(
        doc_fragment_index=chapter_index + 1,
        element_steps=steps,
        text_index=text_index,
        char_offset=char_offset,
    )


def xpointer_point_to_collapsed(chapter: ChapterDom, xpointer: str) -> int | None:
    parsed = parse_xpointer(xpointer)
    if parsed is None:
        return None
    host = _resolve_host_element(chapter, parsed)
    if host is None:
        return None
    for seg in chapter.segments:
        if seg.host_element is host and seg.text_index == parsed.text_index:
            raw_offset = _norm_offset_to_raw_offset(seg.raw_text, parsed.char_offset)
            norm_prefix = normalize_for_search(seg.raw_text[:raw_offset])
            return seg.collapsed_start + len(norm_prefix)
    return None


def search_forward(chapter: ChapterDom, needle: str, from_cp: int | None = None) -> dict[str, int] | None:
    normalized = normalize_for_search(needle)
    if not normalized:
        return None
    tokens = [re.escape(t) for t in normalized.split(" ") if t]
    if not tokens:
        return None
    pattern = re.compile(r"\s+".join(tokens))
    for match in pattern.finditer(chapter.collapsed):
        start_cp = match.start()
        if from_cp is not None and start_cp < from_cp:
            continue
        return {"startCp": start_cp, "endCp": match.end()}
    return None


def search_nearest(chapter: ChapterDom, needle: str, hint_cp: int | None = None) -> dict[str, int] | None:
    normalized = normalize_for_search(needle)
    if not normalized or not chapter.collapsed:
        return None
    if hint_cp is not None:
        fwd = search_forward(chapter, needle, from_cp=hint_cp)
        if fwd:
            return fwd
    idx = chapter.collapsed.find(normalized)
    if idx >= 0:
        return {"startCp": idx, "endCp": idx + len(normalized)}
    return search_forward(chapter, needle, from_cp=0)


def _find_body(root: ET.Element) -> ET.Element:
    if local_tag(root.tag).lower() == "body":
        return root
    for el in root.iter():
        if local_tag(el.tag).lower() == "body":
            return el
    return root


def _build_parent_map(body: ET.Element) -> dict[int, ET.Element]:
    parent_map: dict[int, ET.Element] = {}
    for parent in body.iter():
        for child in list(parent):
            parent_map[id(child)] = parent
    return parent_map


def _build_collapsed_index(
    body: ET.Element, parent_map: dict[int, ET.Element]
) -> tuple[str, list[TextSegment]]:
    chunks: list[str] = []
    segments: list[TextSegment] = []
    cp = 0

    def append_segment(raw: str, host: ET.Element, text_index: int) -> None:
        nonlocal cp
        norm = normalize_for_search(raw)
        if not norm:
            return
        start = cp
        chunks.append(norm)
        cp += len(norm)
        segments.append(
            TextSegment(
                host_element=host,
                text_index=text_index,
                raw_text=raw,
                norm_text=norm,
                collapsed_start=start,
                collapsed_end=cp,
            )
        )

    def text_nodes_for(element: ET.Element) -> list[tuple[str, ET.Element, int]]:
        nodes: list[tuple[str, ET.Element, int]] = []
        idx = 0
        if element.text:
            idx += 1
            nodes.append((element.text, element, idx))
        for child in list(element):
            if child.tail:
                idx += 1
                nodes.append((child.tail, element, idx))
        return nodes

    def walk(element: ET.Element) -> None:
        tag = local_tag(element.tag).lower()
        if tag in _SKIP_TAGS:
            return
        for text, host, text_index in text_nodes_for(element):
            append_segment(text, host, text_index)
        for child in list(element):
            walk(child)

    walk(body)
    return "".join(chunks), segments


def _segment_at_cp(chapter: ChapterDom, cp: int) -> tuple[TextSegment, int] | None:
    if not chapter.segments:
        return None
    for seg in chapter.segments:
        if seg.collapsed_start <= cp < seg.collapsed_end:
            return seg, cp - seg.collapsed_start
    if cp >= chapter.segments[-1].collapsed_end:
        seg = chapter.segments[-1]
        return seg, max(0, seg.collapsed_end - seg.collapsed_start - 1)
    return chapter.segments[0], 0


def _norm_offset_to_raw_offset(raw: str, target_norm_offset: int) -> int:
    if target_norm_offset <= 0:
        return 0
    best = 0
    for i in range(len(raw) + 1):
        prefix_norm = normalize_for_search(raw[:i])
        if len(prefix_norm) <= target_norm_offset:
            best = i
        else:
            break
    return best


def _raw_offset_for_norm_offset(raw: str, norm_offset: int) -> int:
    return _norm_offset_to_raw_offset(raw, norm_offset)


def _location_at_collapsed_cp(chapter: ChapterDom, cp: int) -> tuple[TextSegment, int, int] | None:
    hit = _segment_at_cp(chapter, cp)
    if hit is None:
        return None
    seg, local_norm_offset = hit
    raw_offset = _raw_offset_for_norm_offset(seg.raw_text, local_norm_offset)
    # CREngine char offset uses normalized length within text node when whitespace collapsed
    char_offset = len(normalize_for_search(seg.raw_text[:raw_offset]))
    return seg, seg.text_index, char_offset


def _element_steps(chapter: ChapterDom, target: ET.Element) -> list[tuple[str, int | None]]:
    chain: list[ET.Element] = []
    node: ET.Element | None = target
    while node is not None:
        chain.append(node)
        if node is chapter.body:
            break
        node = chapter._parent_map.get(id(node))
    chain.reverse()
    steps: list[tuple[str, int | None]] = []
    for el in chain:
        if el is chapter.body:
            steps.append(("body", None))
            continue
        parent = chapter._parent_map.get(id(el))
        if parent is None:
            continue
        tag = local_tag(el.tag)
        idx = _element_index(parent, el)
        steps.append((tag, idx))
    return steps


def _element_index(parent: ET.Element, target: ET.Element) -> int | None:
    tag = local_tag(target.tag)
    count = 0
    index = 1
    for child in list(parent):
        if local_tag(child.tag) != tag:
            continue
        count += 1
        if child is target:
            index = count
    if count <= 1 and not _has_following_same_tag(parent, target):
        return None
    return index


def _has_following_same_tag(parent: ET.Element, target: ET.Element) -> bool:
    tag = local_tag(target.tag)
    seen = False
    for child in list(parent):
        if child is target:
            seen = True
            continue
        if seen and local_tag(child.tag) == tag:
            return True
    return False


def _resolve_host_element(chapter: ChapterDom, parsed: ParsedXPointer) -> ET.Element | None:
    node: ET.Element = chapter.body
    for tag, idx in parsed.element_steps:
        if local_tag(node.tag).lower() == "body" and tag.lower() == "body":
            continue
        found = _find_child_element(node, tag, idx or 1)
        if found is None:
            return None
        node = found
    return node


def _find_child_element(parent: ET.Element, tag: str, index: int) -> ET.Element | None:
    count = 0
    for child in list(parent):
        if local_tag(child.tag).lower() != tag.lower():
            continue
        count += 1
        if count == index:
            return child
    return None


def _point_at_collapsed_cp(chapter: ChapterDom, cp: int) -> TextSegment | None:
    hit = _segment_at_cp(chapter, cp)
    return hit[0] if hit else None
