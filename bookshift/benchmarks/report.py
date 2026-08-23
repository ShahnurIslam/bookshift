"""Markdown benchmark report generation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterable

from bookshift.benchmarks.harness import BenchmarkTimings
from bookshift.benchmarks.spot_check import SpotCheckResult


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes = int(seconds // 60)
    rem = seconds - minutes * 60
    if minutes < 60:
        return f"{minutes}m {rem:.0f}s"
    hours = minutes // 60
    minutes = minutes % 60
    return f"{hours}h {minutes:02d}m {rem:.0f}s"


def _fmt_audio(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _fmt_promotion(ms: float | None) -> str:
    if ms is None:
        return "—"
    return f"{int(round(ms))}ms"


def _fmt_error(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    return f"± {seconds:.1f}s"


def render_results_table(results: Iterable[BenchmarkTimings]) -> str:
    lines = [
        "# BookShift Benchmark Results",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "| Title | Audio Duration | Ingest → COARSE Ready | First Usable Sync | FINE Ready | Atomic Promotion | Mean COARSE Error | Session Disrupted |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        disrupted = "Yes" if r.session_disrupted else "No"
        lines.append(
            "| "
            + " | ".join(
                [
                    r.title,
                    _fmt_audio(r.audio_duration_seconds),
                    _fmt_duration(r.ingest_to_coarse_ready_s),
                    _fmt_duration(r.first_usable_sync_s),
                    _fmt_duration(r.fine_ready_s),
                    _fmt_promotion(r.atomic_promotion_ms),
                    _fmt_error(r.coarse_mean_error_s),
                    disrupted,
                ]
            )
            + " |"
        )
    lines.append("")
    notes = [n for r in results for n in (r.notes or [])]
    if notes:
        lines.append("## Run notes")
        lines.append("")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")
    lines.append("## Acceptance Criteria")
    lines.append("")
    lines.append("- COARSE readiness must be **< 2.0s** from ingest start for every title.")
    lines.append("- Atomic promotion must complete without session disruption (0 failed requests, latency < 5ms p99 during switch).")
    lines.append("- Mean COARSE error is measured against FINE ground truth when available (N=50 samples).")
    lines.append("")
    return "\n".join(lines)


def write_benchmark_results(path: str, results: list[BenchmarkTimings]) -> str:
    content = render_results_table(results)
    from pathlib import Path

    out = Path(path)
    out.write_text(content, encoding="utf-8")
    return content


def _pct(rate: float) -> str:
    return f"{rate * 100.0:.1f}%"


def _err(seconds: float) -> str:
    return f"{seconds:.3f}s"


def _ms(ms: float) -> str:
    return f"{ms:.3f}ms"


def render_spot_check(results: Iterable[SpotCheckResult]) -> str:
    rows = list(results)
    n = rows[0].sample_count if rows else 0
    lines = [
        "# BookShift Bidirectional Spot-Check",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"N = {n} randomized trials per direction (audio ➔ book, book ➔ audio, round-trip).",
        "",
        "## FINE precision",
        "",
        "| Title | Exact sentence match | Mean error | Max error | Locators |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        fp = r.fine_precision
        lines.append(
            "| "
            + " | ".join(
                [
                    r.title,
                    _pct(fp.exact_match_rate),
                    _err(fp.mean_error_s),
                    _err(fp.max_error_s),
                    str(r.fine_locator_count),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## COARSE precision",
            "",
            "| Title | Median error | Mean error | p90 error | Max error |",
            "|---|---|---|---|---|",
        ]
    )
    for r in rows:
        cp = r.coarse_precision
        lines.append(
            "| "
            + " | ".join(
                [
                    r.title,
                    _err(cp.median_error_s),
                    _err(cp.mean_error_s),
                    _err(cp.p90_error_s),
                    _err(cp.max_error_s),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Lookup latency",
            "",
            "| Title | FINE mean | FINE p95 | COARSE mean | COARSE p95 |",
            "|---|---|---|---|---|",
        ]
    )
    for r in rows:
        lat = r.latency
        lines.append(
            "| "
            + " | ".join(
                [
                    r.title,
                    _ms(lat.fine_mean_ms),
                    _ms(lat.fine_p95_ms),
                    _ms(lat.coarse_mean_ms),
                    _ms(lat.coarse_p95_ms),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Round-trip consistency",
            "",
            "| Title | Audio ➔ Ebook ➔ Audio (same sentence) | Mean drift | Max drift | Ebook ➔ Audio ➔ Ebook (exact XPointer) |",
            "|---|---|---|---|---|",
        ]
    )
    for r in rows:
        rt = r.round_trip
        lines.append(
            "| "
            + " | ".join(
                [
                    r.title,
                    _pct(rt.audio_to_ebook_to_audio_same_sentence_rate),
                    _err(rt.audio_to_ebook_to_audio_mean_drift_s),
                    _err(rt.audio_to_ebook_to_audio_max_drift_s),
                    _pct(rt.ebook_to_audio_to_ebook_exact_rate),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Per-direction breakdown",
            "",
            "| Title | Direction | FINE exact | FINE mean err | COARSE median | COARSE mean | COARSE p90 |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for r in rows:
        for label, d in (("audio ➔ book", r.audio_to_book), ("book ➔ audio", r.book_to_audio)):
            lines.append(
                "| "
                + " | ".join(
                    [
                        r.title,
                        label,
                        _pct(d.fine_exact_match_rate),
                        _err(d.fine_mean_error_s),
                        _err(d.coarse_median_error_s),
                        _err(d.coarse_mean_error_s),
                        _err(d.coarse_p90_error_s),
                    ]
                )
                + " |"
            )
    notes = [n for r in rows for n in (r.notes or [])]
    if notes:
        lines.extend(["", "## Run notes", ""])
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")
    lines.extend(
        [
            "## Acceptance",
            "",
            "- FINE exact sentence match rate must be **100%** against the compiled index.",
            "- Ebook ➔ audio ➔ ebook must return the **same XPointer**.",
            "- Audio ➔ ebook ➔ audio must return the **same sentence** (drift is distance to sentence start).",
            "- COARSE errors are chapter-level vs FINE ground truth (informational; not a pass/fail gate).",
            "",
        ]
    )
    return "\n".join(lines)


def write_spot_check_results(path: str, results: list[SpotCheckResult]) -> str:
    from pathlib import Path

    content = render_spot_check(results)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    json_path = out.with_suffix(".json")
    payload = {
        "schema": "bookshift-spot-check-suite/v1",
        "generated": datetime.now(timezone.utc).isoformat(),
        "results": [r.to_dict() for r in results],
    }
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return content
