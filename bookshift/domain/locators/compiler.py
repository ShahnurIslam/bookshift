"""In-process EPUB locator compiler (replaces BookOrbit container Node scripts)."""

from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from bookshift.domain.locators.cfi import cfi_for_collapsed_point, spine_cfi_index_for_href
from bookshift.domain.locators.crengine import (
    ChapterDom,
    collapsed_point_to_xpointer,
    local_tag,
    normalize_for_search,
    parse_xpointer,
    search_forward,
    search_nearest,
    xpointer_point_to_collapsed,
)

OPF_NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}


def read_epub_spine(epub_path: Path) -> list[str]:
    with zipfile.ZipFile(epub_path) as zf:
        container = zf.read("META-INF/container.xml")
        root = ET.fromstring(container)
        opf_path = None
        for el in root.iter():
            if el.tag.endswith("rootfile"):
                opf_path = el.get("full-path")
                break
        if not opf_path:
            raise ValueError("EPUB missing OPF rootfile")
        opf_data = zf.read(opf_path)
        opf_root = ET.fromstring(opf_data)
        opf_dir = str(Path(opf_path).parent)
        if opf_dir == ".":
            opf_dir = ""

        manifest: dict[str, str] = {}
        for item in opf_root.findall(".//opf:manifest/opf:item", OPF_NS):
            ident = item.get("id") or ""
            href = item.get("href") or ""
            if ident and href:
                manifest[ident] = href

        hrefs: list[str] = []
        for itemref in opf_root.findall(".//opf:spine/opf:itemref", OPF_NS):
            idref = itemref.get("idref") or ""
            href = manifest.get(idref, idref)
            if opf_dir:
                href = f"{opf_dir}/{href}".replace("//", "/")
            hrefs.append(href)
        return hrefs


def load_chapter(epub_path: Path, href: str) -> ChapterDom:
    with zipfile.ZipFile(epub_path) as zf:
        candidates = [href, href.lstrip("/")]
        if not href.startswith("OEBPS/"):
            candidates.append(f"OEBPS/{Path(href).name}")
        data = None
        used = href
        for cand in candidates:
            try:
                data = zf.read(cand)
                used = cand
                break
            except KeyError:
                continue
        if data is None:
            # basename fallback
            base = Path(href).name.lower()
            for name in zf.namelist():
                if name.lower().endswith(base):
                    data = zf.read(name)
                    used = name
                    break
        if data is None:
            raise FileNotFoundError(f"chapter not found in EPUB: {href}")
        return ChapterDom.from_xhtml(used, data)


def _basename_norm(path: str) -> str:
    return Path(str(path or "")).name.split("#")[0].lower()


def resolve_chapter_index(spine_hrefs: list[str], href: str) -> int:
    target = _basename_norm(href)
    for i, h in enumerate(spine_hrefs):
        if _basename_norm(h) == target:
            return i
    for i, h in enumerate(spine_hrefs):
        if h.lower().endswith("/" + target) or href.lower().endswith("/" + _basename_norm(h)):
            return i
    return -1


def _parse_ncx(ncx_text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for m in re.finditer(
        r"<navPoint\b[\s\S]*?<text>([\s\S]*?)</text>[\s\S]*?<content\s+src=\"([^\"]+)\"",
        ncx_text,
        flags=re.IGNORECASE,
    ):
        title = re.sub(r"<[^>]+>", " ", m.group(1))
        title = re.sub(r"\s+", " ", title).strip()
        src = m.group(2).split("#")[0].strip()
        if title and src:
            entries.append({"title": title, "href": src})
    return entries


def _is_narrative_title(title: str) -> bool:
    t = (title or "").strip().lower()
    return (
        t.startswith("prologue")
        or t.startswith("chapter")
        or t.startswith("epilogue")
        or bool(re.search(r"\bchapter\s+\d+\b", t))
        or bool(re.search(r"\bpart\s+\d+\b", t))
        or bool(re.fullmatch(r"\d{1,4}", t))
    )


def _is_narrative_spine_href(href: str) -> bool:
    b = Path(href).name
    return bool(
        re.search(r"_c\d+_Chapter_\d+\.xhtml$", b, re.I)
        or re.search(r"(?:^|_)Chapter_\d+\.xhtml$", b, re.I)
        or re.search(r"chapter[_\s-]?\d+", b, re.I)
    )


def build_chapters(epub_path: Path | str) -> dict[str, Any]:
    path = Path(epub_path)
    spine_hrefs = read_epub_spine(path)
    spine_rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as zf:
        ncx_text = None
        for cand in ("toc.ncx", "OEBPS/toc.ncx"):
            try:
                ncx_text = zf.read(cand).decode("utf-8", errors="replace")
                break
            except KeyError:
                continue
    toc_all = _parse_ncx(ncx_text) if ncx_text else []
    toc_narrative = [e for e in toc_all if _is_narrative_title(e["title"])]

    for i, href in enumerate(spine_hrefs):
        try:
            chapter = load_chapter(path, href)
            total = chapter.collapsed_length
            heading = ""
            for tag in ("h1", "h2", "h3"):
                for el in chapter.body.iter():
                    if local_tag(el.tag).lower() == tag:
                        heading = normalize_for_search("".join(el.itertext()))[:120]
                        break
                if heading:
                    break
        except Exception:
            total = 0
            heading = ""
        spine_rows.append(
            {
                "chapter_index": i,
                "doc_fragment_index": i + 1,
                "href": href,
                "total_code_points": total,
                "heading": heading,
            }
        )

    if not toc_narrative:
        toc_narrative = [
            {"title": Path(h).name, "href": h}
            for h in spine_hrefs
            if _is_narrative_spine_href(h)
        ]
    if not toc_narrative:
        toc_narrative = [
            {"title": r.get("heading") or Path(r["href"]).name, "href": r["href"]}
            for r in spine_rows
            if int(r.get("total_code_points") or 0) >= 200
        ] or [
            {"title": r.get("heading") or Path(r["href"]).name, "href": r["href"]}
            for r in spine_rows
            if int(r.get("total_code_points") or 0) > 0
        ]

    narrative: list[dict[str, Any]] = []
    for narrative_index, entry in enumerate(toc_narrative):
        chapter_index = resolve_chapter_index(spine_hrefs, entry["href"])
        spine_row = spine_rows[chapter_index] if 0 <= chapter_index < len(spine_rows) else None
        narrative.append(
            {
                "narrative_index": narrative_index,
                "title": entry["title"],
                "href": entry["href"],
                "chapter_index": chapter_index,
                "doc_fragment_index": chapter_index + 1 if chapter_index >= 0 else None,
                "total_code_points": int(spine_row["total_code_points"]) if spine_row else 0,
            }
        )

    return {
        "epub_path": str(path),
        "spine_count": len(spine_hrefs),
        "spine": spine_rows,
        "toc_all": toc_all,
        "toc_narrative": narrative,
        "ncx_path": "toc.ncx" if ncx_text else None,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "compiler": "bookshift-python-locators",
    }


def audio_to_xpointer(
    epub_path: Path | str,
    chapter_index: int,
    target_cp: float,
) -> dict[str, Any]:
    path = Path(epub_path)
    spine = read_epub_spine(path)
    if chapter_index < 0 or chapter_index >= len(spine):
        return {"ok": False, "reason": "chapter_index_out_of_range", "chapter_index": chapter_index}
    href = spine[chapter_index]
    chapter = load_chapter(path, href)
    total = chapter.collapsed_length
    cp = max(0, min(int(target_cp), max(0, total - 1) if total else 0))
    xpointer = collapsed_point_to_xpointer(chapter, chapter_index, cp)
    cfi = cfi_for_collapsed_point(
        spine_hrefs=spine,
        chapter=chapter,
        chapter_index=chapter_index,
        collapsed_cp=cp,
    )
    return {
        "ok": bool(xpointer),
        "chapter_index": chapter_index,
        "doc_fragment_index": chapter_index + 1,
        "href": href,
        "total_code_points": total,
        "target_cp_requested": target_cp,
        "target_cp_clamped": cp,
        "koreader_xpointer": xpointer,
        "epub_cfi": cfi,
    }


def xpointer_to_collapsed_cp(epub_path: Path | str, xpointer: str) -> dict[str, Any]:
    parsed = parse_xpointer(xpointer)
    if parsed is None:
        return {"ok": False, "reason": "xpointer_parse_failed", "xpointer": xpointer}
    chapter_index = parsed.chapter_index
    path = Path(epub_path)
    spine = read_epub_spine(path)
    if chapter_index < 0 or chapter_index >= len(spine):
        return {"ok": False, "reason": "doc_fragment_out_of_range", "chapter_index": chapter_index}
    href = spine[chapter_index]
    chapter = load_chapter(path, href)
    cp = xpointer_point_to_collapsed(chapter, xpointer)
    return {
        "ok": cp is not None,
        "chapter_index": chapter_index,
        "doc_fragment_index": chapter_index + 1,
        "href": href,
        "total_code_points": chapter.collapsed_length,
        "collapsed_cp": cp,
        "koreader_xpointer": xpointer,
    }


def compile_alignment_map(
    epub_path: Path | str,
    map_path: Path | str,
    *,
    sequential: bool = True,
    skip_assert: bool = False,
    storyteller_extract: Path | None = None,
) -> dict[str, Any]:
    path = Path(epub_path)
    alignment = json.loads(Path(map_path).read_text(encoding="utf-8"))
    spine_hrefs = read_epub_spine(path)
    chapter_cache: dict[int, ChapterDom] = {}
    cursor_by_chapter: dict[int, int] = {}
    ok = 0
    fail = 0
    failures: list[dict[str, Any]] = []
    sentences = alignment.get("sentences") or []

    def get_chapter(idx: int) -> ChapterDom | None:
        if idx in chapter_cache:
            return chapter_cache[idx]
        if idx < 0 or idx >= len(spine_hrefs):
            return None
        try:
            doc = load_chapter(path, spine_hrefs[idx])
        except Exception:
            doc = None
        chapter_cache[idx] = doc
        return doc

    for sentence in sentences:
        chapter_index = resolve_chapter_index(spine_hrefs, str(sentence.get("spine_href") or ""))
        sentence["bookorbit_chapter_index"] = chapter_index
        sentence["doc_fragment_index"] = chapter_index + 1 if chapter_index >= 0 else None

        if not skip_assert and storyteller_extract:
            st = _assert_storyteller_element(
                storyteller_extract,
                str(sentence.get("spine_href") or ""),
                str(sentence.get("element_id") or ""),
                str(sentence.get("text_snippet") or ""),
            )
            sentence["storyteller_element_assert"] = "ok" if st.get("ok") else st.get("reason")
        else:
            sentence["storyteller_element_assert"] = "skipped"

        if chapter_index < 0:
            sentence["koreader_xpointer"] = None
            sentence["epub_cfi"] = None
            sentence["locator_status"] = "spine_href_unresolved"
            fail += 1
            continue

        chapter = get_chapter(chapter_index)
        if chapter is None:
            sentence["koreader_xpointer"] = None
            sentence["epub_cfi"] = None
            sentence["locator_status"] = "chapter_unavailable"
            fail += 1
            continue

        needle = normalize_for_search(str(sentence.get("text_snippet") or ""))
        if not needle:
            sentence["koreader_xpointer"] = None
            sentence["epub_cfi"] = None
            sentence["locator_status"] = "empty_snippet"
            fail += 1
            continue

        hint = cursor_by_chapter.get(chapter_index, 0) if sequential else None
        match = search_forward(chapter, needle, hint) if sequential else None
        if not match and sequential:
            match = search_nearest(chapter, needle, hint)
        if not match:
            match = search_nearest(chapter, needle, None)

        if not match:
            sentence["koreader_xpointer"] = None
            sentence["epub_cfi"] = None
            sentence["locator_status"] = "text_not_found_in_bookorbit_epub"
            fail += 1
            if len(failures) < 50:
                failures.append(
                    {
                        "element_id": sentence.get("element_id"),
                        "reason": "text_not_found_in_bookorbit_epub",
                        "snippet": needle[:60],
                    }
                )
            continue

        start_cp = int(match["startCp"])
        xpointer = collapsed_point_to_xpointer(chapter, chapter_index, start_cp)
        cfi = cfi_for_collapsed_point(
            spine_hrefs=spine_hrefs,
            chapter=chapter,
            chapter_index=chapter_index,
            collapsed_cp=start_cp,
        )
        if not xpointer:
            sentence["koreader_xpointer"] = None
            sentence["epub_cfi"] = cfi
            sentence["locator_status"] = "converter_failed"
            fail += 1
            continue

        sentence["koreader_xpointer"] = xpointer
        sentence["epub_cfi"] = cfi
        sentence["collapsed_start_cp"] = start_cp
        sentence["locator_status"] = "ok"
        ok += 1
        if sequential:
            cursor_by_chapter[chapter_index] = max(start_cp + 1, int(match["endCp"]) - 1)

    alignment["format"] = "storyteller-alignment-map/v3-xpointer"
    alignment["bookorbit_spine_hrefs"] = spine_hrefs
    alignment["locator_compile"] = {
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "epub_path": str(path),
        "ok": ok,
        "fail": fail,
        "sequential": sequential,
        "converter": "bookshift-python-locators",
        "sentence_count": len(sentences),
        "sample_failures": failures[:8],
    }
    return alignment


def _assert_storyteller_element(
    extract_dir: Path,
    spine_href: str,
    element_id: str,
    text_snippet: str,
) -> dict[str, Any]:
    if not element_id:
        return {"ok": False, "reason": "missing_extract_or_id"}
    candidates = [
        extract_dir / spine_href,
        extract_dir / "OEBPS" / "Text" / Path(spine_href).name,
        extract_dir / "Text" / Path(spine_href).name,
    ]
    xhtml = None
    used = None
    for cand in candidates:
        if cand.is_file():
            xhtml = cand.read_text(encoding="utf-8", errors="replace")
            used = str(cand)
            break
    if not xhtml:
        return {"ok": False, "reason": "storyteller_xhtml_missing"}
    if not re.search(
        rf'<(?:span|[A-Za-z][\w:-]*)\b[^>]*\bid="{re.escape(element_id)}"',
        xhtml,
        flags=re.I,
    ):
        return {"ok": False, "reason": "element_id_absent", "file": used}
    snippet = normalize_for_search(text_snippet)
    if len(snippet) >= 8:
        pos = xhtml.find(f'id="{element_id}"')
        window = xhtml[max(0, pos - 200) : pos + 800]
        plain = normalize_for_search(re.sub(r"<[^>]+>", " ", window))
        if snippet[: min(40, len(snippet))] not in plain:
            return {"ok": False, "reason": "text_snippet_mismatch", "file": used}
    return {"ok": True, "file": used}
