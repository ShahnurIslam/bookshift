"""Live FINE transcription against a remote whisper.cpp server."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from bookshift.domain.locators.compiler import load_chapter, read_epub_spine
from bookshift.domain.locators.crengine import (
    collapsed_point_to_xpointer,
    normalize_for_search,
    search_forward,
)

DEFAULT_TRANSCRIBE_PATH = "/audio/transcriptions"


def probe_whisper(base_url: str, timeout: float = 10.0) -> dict[str, Any]:
    """Confirm the whisper.cpp HTTP server is reachable.

    whisper.cpp serves a UI at `/` and often has no `/health` route.
    """
    target = base_url.rstrip("/")
    t0 = time.perf_counter()
    out: dict[str, Any] = {"url": target, "online": False, "http_status": None, "error": None}
    last_error = None
    for path in ("/", "/health"):
        try:
            req = urllib.request.Request(target + path, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                out["http_status"] = int(resp.status)
                out["online"] = 200 <= int(resp.status) < 500
                out["body"] = body.strip()[:200]
                out["probe_path"] = path
                out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                return out
        except urllib.error.HTTPError as exc:
            out["http_status"] = int(exc.code)
            out["probe_path"] = path
            # An HTTP response means the process is up (e.g. 404 on /health).
            if 400 <= int(exc.code) < 600:
                out["online"] = True
                out["error"] = f"HTTP {exc.code} on {path}"
                out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                if int(exc.code) != 404:
                    return out
                last_error = out["error"]
                continue
            last_error = f"HTTPError: {exc}"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
    out["error"] = last_error
    out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def _multipart(fields: dict[str, str], file_field: str, filename: str, data: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = "----BookShiftWhisperBoundary"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(value.encode() + b"\r\n")
    chunks.append(f"--{boundary}\r\n".encode())
    chunks.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode()
    )
    chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode())
    chunks.append(data)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def transcribe_file(
    audio_path: Path,
    *,
    base_url: str,
    timeout: float = 7200.0,
    language: str = "en",
) -> dict[str, Any]:
    """POST one audio file to whisper.cpp `/audio/transcriptions` (verbose JSON)."""
    target = base_url.rstrip("/") + DEFAULT_TRANSCRIBE_PATH
    payload, content_type = _multipart(
        {
            "response_format": "verbose_json",
            "language": language,
            "temperature": "0",
        },
        "file",
        audio_path.name,
        audio_path.read_bytes(),
        "audio/mpeg" if audio_path.suffix.lower() == ".mp3" else "application/octet-stream",
    )
    req = urllib.request.Request(
        target,
        data=payload,
        method="POST",
        headers={"Content-Type": content_type},
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            elapsed = time.perf_counter() - t0
            doc = json.loads(raw.decode("utf-8"))
            doc["_elapsed_s"] = elapsed
            doc["_http_status"] = int(resp.status)
            doc["_file"] = audio_path.name
            return doc
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"whisper HTTP {exc.code} for {audio_path.name}: {body}") from exc


def _id3v2_size(data: bytes) -> int:
    if len(data) >= 10 and data[:3] == b"ID3":
        return 10 + (data[6] << 21 | data[7] << 14 | data[8] << 7 | data[9])
    return 0


def _next_frame_sync(data: bytes, start: int) -> int:
    i = max(0, start)
    end = len(data) - 1
    while i < end:
        if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
            return i
        i += 1
    return len(data)


def _collapsed_texts(segments: list[dict[str, Any]]) -> bool:
    texts = []
    for seg in segments:
        t = re.sub(r"\s+", " ", str(seg.get("text") or seg.get("text_snippet") or "")).strip()
        if t:
            texts.append(t.upper())
    if len(texts) < 8:
        return False
    uniq = set(texts)
    if len(uniq) <= 3 and all(t.startswith("CHAPTER") for t in uniq):
        return True
    # Dominant-loop: one phrase is >80% of segments.
    from collections import Counter

    counts = Counter(texts)
    _, n = counts.most_common(1)[0]
    return n / len(texts) >= 0.8


def transcribe_file_chunked(
    audio_path: Path,
    *,
    base_url: str,
    chunk_seconds: float = 60.0,
    timeout: float = 7200.0,
    language: str = "en",
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Split a CBR MP3 into windows so whisper.cpp cannot lock onto a title loop."""
    emit = progress or (lambda s: None)
    data = audio_path.read_bytes()
    header = _id3v2_size(data)
    payload = data[header:]
    bitrate = 64000
    bps = bitrate / 8.0
    duration_guess = len(payload) / bps if bps else 0.0
    chunk_bytes = int(chunk_seconds * bps)
    merged_segments: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    offset_s = 0.0
    pos = 0
    idx = 0
    tmp_dir = Path("/tmp/bookshift_whisper_chunks")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    while pos < len(payload):
        start = _next_frame_sync(payload, pos)
        end = min(len(payload), start + chunk_bytes)
        if end < len(payload):
            end = _next_frame_sync(payload, end)
        chunk = payload[start:end]
        if len(chunk) < 4000:
            break
        idx += 1
        tmp = tmp_dir / f"{audio_path.stem}_part{idx:03d}.mp3"
        tmp.write_bytes(chunk)
        emit(f"    chunk {idx} @{offset_s:.0f}s ({len(chunk)} bytes)")
        doc = transcribe_file(tmp, base_url=base_url, timeout=timeout, language=language)
        try:
            tmp.unlink()
        except OSError:
            pass
        for seg in doc.get("segments") or []:
            rec = dict(seg)
            rec["start"] = float(seg.get("start") or 0.0) + offset_s
            rec["end"] = float(seg.get("end") or rec["start"]) + offset_s
            merged_segments.append(rec)
        chunk_dur = float(doc.get("duration") or (end - start) / bps)
        offset_s += chunk_dur
        pos = end
    elapsed = time.perf_counter() - t0
    return {
        "text": " ".join(str(s.get("text") or "").strip() for s in merged_segments),
        "segments": merged_segments,
        "duration": offset_s or duration_guess,
        "language": language,
        "_elapsed_s": elapsed,
        "_file": audio_path.name,
        "_chunked": True,
        "_chunks": idx,
    }


def _segments_from_file_doc(doc: dict[str, Any], offset: float, filename: str) -> tuple[list[dict[str, Any]], float]:
    dur = float(doc.get("duration") or 0.0)
    out: list[dict[str, Any]] = []
    for seg in doc.get("segments") or []:
        start = float(seg.get("start") or 0.0) + offset
        end = float(seg.get("end") or start) + offset
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        out.append(
            {
                "audio_seconds": start,
                "audio_end_seconds": end,
                "text_snippet": text,
                "source_file": filename,
            }
        )
    return out, dur


def transcribe_directory(
    audio_dir: Path,
    *,
    base_url: str,
    progress: Callable[[str], None] | None = None,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    emit = progress or (lambda s: None)
    files = sorted(
        p for p in audio_dir.iterdir() if p.suffix.lower() in {".mp3", ".wav", ".ogg", ".m4a", ".flac"}
    )
    if not files:
        raise FileNotFoundError(f"no audio files in {audio_dir}")
    cached_files: dict[str, dict[str, Any]] = {}
    if cache_path and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            for row in cached.get("files") or []:
                name = str(row.get("file") or "")
                if name and row.get("raw_segments") is not None:
                    cached_files[name] = row
        except (OSError, json.JSONDecodeError):
            cached_files = {}

    segments: list[dict[str, Any]] = []
    offset = 0.0
    file_results: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    for path in files:
        if path.name in cached_files:
            row = cached_files[path.name]
            dur = float(row.get("duration") or 0.0)
            fake_doc = {"duration": dur, "segments": row.get("raw_segments") or []}
            file_segs, dur = _segments_from_file_doc(fake_doc, offset, path.name)
            segments.extend(file_segs)
            file_results.append(
                {
                    "file": path.name,
                    "duration": dur,
                    "segments": len(file_segs),
                    "elapsed_s": row.get("elapsed_s"),
                    "language": row.get("language"),
                    "cached": True,
                    "raw_segments": row.get("raw_segments") or [],
                }
            )
            emit(f"  transcribe {path.name} (cached) duration={dur:.1f}s segments={len(file_segs)}")
            offset += dur if dur > 0 else 0.0
            continue
        emit(f"  transcribe {path.name} …")
        doc = transcribe_file(path, base_url=base_url)
        file_segs, dur = _segments_from_file_doc(doc, offset, path.name)
        segments.extend(file_segs)
        file_results.append(
            {
                "file": path.name,
                "duration": dur,
                "segments": len(file_segs),
                "elapsed_s": doc.get("_elapsed_s"),
                "language": doc.get("language") or doc.get("detected_language"),
                "cached": False,
                "raw_segments": list(doc.get("segments") or []),
            }
        )
        emit(
            f"    duration={dur:.1f}s segments={len(file_segs)} whisper_s={float(doc.get('_elapsed_s') or 0):.1f}"
        )
        offset += dur if dur > 0 else 0.0
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "whisper_url": base_url.rstrip("/"),
                        "files": file_results,
                        "audio_duration_seconds": offset,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
    result = {
        "segments": segments,
        "files": file_results,
        "audio_duration_seconds": offset,
        "elapsed_s": time.perf_counter() - t0,
        "whisper_url": base_url.rstrip("/"),
    }
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({**result, "segments": segments}, indent=2) + "\n", encoding="utf-8")
    return result


def segments_to_locator_rows(
    segments: list[dict[str, Any]],
    epub: Path,
    *,
    min_chars: int = 12,
) -> list[dict[str, Any]]:
    """Map Whisper segments onto EPUB XPointers via sequential collapsed-text search."""
    hrefs = read_epub_spine(epub)
    chapters = []
    for i, href in enumerate(hrefs):
        try:
            chapters.append(load_chapter(epub, href))
        except Exception:
            chapters.append(None)
    rows: list[dict[str, Any]] = []
    ch_i = 0
    from_cp = 0
    for seg in segments:
        needle = normalize_for_search(str(seg.get("text_snippet") or ""))
        if len(needle) < min_chars:
            continue
        match = None
        used_i = ch_i
        for j in range(ch_i, len(chapters)):
            chapter = chapters[j]
            if chapter is None:
                continue
            start_from = from_cp if j == ch_i else None
            match = search_forward(chapter, needle, from_cp=start_from)
            if match is None and start_from:
                match = search_forward(chapter, needle, from_cp=None)
            if match is not None:
                used_i = j
                break
        if match is None:
            continue
        chapter = chapters[used_i]
        assert chapter is not None
        xp = collapsed_point_to_xpointer(chapter, used_i, int(match["startCp"]))
        if not xp:
            continue
        ch_i = used_i
        from_cp = int(match["endCp"])
        rows.append(
            {
                "audio_seconds": float(seg["audio_seconds"]),
                "audio_end_seconds": float(seg["audio_end_seconds"]),
                "xpointer": xp,
                "text_snippet": needle[:180],
                "chapter_index": used_i,
                "doc_fragment_index": used_i + 1,
            }
        )
    rows.sort(key=lambda r: r["audio_seconds"])
    return rows
