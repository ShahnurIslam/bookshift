"""Bidirectional spot-check harness tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from bookshift.benchmarks.spot_check import (
    SpotCheckRunner,
    fine_timestamp_for_xpointer,
)
from bookshift.domain.locator_index import FineLocatorIndex


def _coarse_map() -> dict:
    return {
        "pairs": [
            {
                "abs_index": 0,
                "abs_start": 0.0,
                "abs_end": 100.0,
                "abs_duration": 100.0,
                "chapter_index": 1,
                "doc_fragment_index": 2,
                "epub_title": "Ch1",
            },
            {
                "abs_index": 1,
                "abs_start": 100.0,
                "abs_end": 200.0,
                "abs_duration": 100.0,
                "chapter_index": 2,
                "doc_fragment_index": 3,
                "epub_title": "Ch2",
            },
        ],
        "confidence": 0.9,
    }


def _fine_rows(n_per_chapter: int = 10) -> list[dict]:
    rows: list[dict] = []
    for df, chapter, base in ((2, 1, 0.0), (3, 2, 100.0)):
        for i in range(n_per_chapter):
            t = base + i * 10.0
            rows.append(
                {
                    "audio_seconds": t,
                    "audio_end_seconds": t + 10.0,
                    "xpointer": f"/body/DocFragment[{df}]/body/p[{i + 1}]/text().0",
                    "chapter_index": chapter,
                    "doc_fragment_index": df,
                    "epub_cfi": f"epubcfi(/6/{df * 2}!/4/{2 * (i + 1)}/1:0)",
                    "text_snippet": f"s{chapter}-{i}",
                }
            )
    return rows


@pytest.fixture
def runner() -> SpotCheckRunner:
    return SpotCheckRunner(
        coarse_map=_coarse_map(),
        fine_rows=_fine_rows(),
        sample_count=100,
        seed=42,
        audio_duration=200.0,
        title="fixture-book",
    )


def test_fine_timestamp_for_chapter_level_xpointer():
    fine = FineLocatorIndex(_fine_rows())
    assert fine_timestamp_for_xpointer(fine, "/body/DocFragment[2]/body/text().0") == 0.0
    assert fine_timestamp_for_xpointer(fine, "/body/DocFragment[3]/body/text().0") == 100.0
    xp = "/body/DocFragment[2]/body/p[3]/text().0"
    assert fine_timestamp_for_xpointer(fine, xp) == 20.0


def test_spot_check_fine_exact_and_roundtrip(runner: SpotCheckRunner):
    result = runner.run()
    assert result.sample_count == 100
    assert result.fine_precision.sample_count == 200  # both directions
    assert result.fine_precision.exact_match_rate == 1.0
    assert result.fine_precision.mean_error_s == 0.0
    assert result.fine_precision.max_error_s == 0.0
    assert result.round_trip.ebook_to_audio_to_ebook_exact_rate == 1.0
    assert result.round_trip.audio_to_ebook_to_audio_same_sentence_rate == 1.0
    assert result.passed()


def test_spot_check_coarse_audio_to_book_error_formula(runner: SpotCheckRunner):
    # t=25 → COARSE DocFragment[2] → FINE chapter start 0s → |25-0|=25
    a2b = runner._audio_to_book([25.0])
    assert a2b["fine_hits"] == 1
    assert a2b["coarse_errors"] == pytest.approx([25.0])


def test_spot_check_coarse_book_to_audio_error_formula(runner: SpotCheckRunner):
    # sentence at 40s in ch1; COARSE reverse interpolates within chapter
    xp = "/body/DocFragment[2]/body/p[5]/text().0"
    b2a = runner._book_to_audio([xp])
    assert b2a["fine_hits"] == 1
    assert b2a["fine_errors"] == pytest.approx([0.0])
    assert b2a["coarse_errors"][0] < 40.0, "interpolation should reduce error vs snap-to-start"


def test_spot_check_coarse_aggregates(runner: SpotCheckRunner):
    result = runner.run()
    cp = result.coarse_precision
    assert cp.sample_count == 200
    assert cp.mean_error_s > 0.0
    assert cp.median_error_s > 0.0
    assert cp.p90_error_s >= cp.median_error_s
    assert cp.max_error_s >= cp.p90_error_s
    assert result.audio_to_book.coarse_mean_error_s > 0.0
    assert result.book_to_audio.coarse_mean_error_s > 0.0


def test_spot_check_latency_metrics(runner: SpotCheckRunner):
    result = runner.run()
    lat = result.latency
    assert lat.sample_count == 400  # 100 trials × 2 dirs × 2 modes
    assert lat.fine_mean_ms >= 0.0
    assert lat.fine_p95_ms >= lat.fine_mean_ms * 0.0
    assert lat.fine_p95_ms < 50.0
    assert lat.coarse_p95_ms < 50.0


def test_spot_check_to_dict_schema(runner: SpotCheckRunner):
    payload = runner.run().to_dict()
    assert payload["schema"] == "bookshift-spot-check/v1"
    assert payload["passed"] is True
    assert "exact_match_percent" in payload["fine_precision"]
    assert payload["fine_precision"]["exact_match_percent"] == 100.0


def test_run_benchmarks_parses_spot_check_flag():
    from benchmarks.run_benchmarks import build_parser

    args = build_parser().parse_args(["--spot-check", "100", "--books", "dr_jekyll"])
    assert args.spot_check == 100
    assert args.books == "dr_jekyll"
    args_default = build_parser().parse_args(["--spot-check", "--books", "dr_jekyll"])
    assert args_default.spot_check == 100


def test_bookshift_cli_forwards_spot_check():
    from bookshift.cli import _build_parser

    args = _build_parser().parse_args(
        ["benchmark", "--spot-check", "100", "--books", "dr_jekyll"]
    )
    assert args.command == "benchmark"
    assert args.spot_check == 100


def test_render_spot_check_markdown(runner: SpotCheckRunner, tmp_path: Path):
    from bookshift.benchmarks.report import render_spot_check, write_spot_check_results

    result = runner.run()
    md = render_spot_check([result])
    assert "FINE precision" in md
    assert "COARSE precision" in md
    assert "Lookup latency" in md
    assert "Round-trip" in md
    out = tmp_path / "spot.md"
    write_spot_check_results(str(out), [result])
    assert out.is_file()
    assert out.with_suffix(".json").is_file()
