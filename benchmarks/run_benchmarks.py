#!/usr/bin/env python3
"""BookShift Gate 5 — progressive sync benchmark runner."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bookshift.benchmarks.fixtures import load_catalog, select_fixtures  # noqa: E402
from bookshift.benchmarks.harness import run_all  # noqa: E402
from bookshift.benchmarks.report import (  # noqa: E402
    render_results_table,
    render_spot_check,
    write_benchmark_results,
    write_spot_check_results,
)
from bookshift.benchmarks.spot_check import DEFAULT_SAMPLE_COUNT, run_spot_checks  # noqa: E402
from bookshift.config import REPO_ROOT as PKG_ROOT  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--catalog",
        type=Path,
        default=PKG_ROOT / "data" / "benchmarks" / "catalog.json",
    )
    ap.add_argument(
        "--books",
        default="",
        help="Comma-separated fixture keys (default: all available)",
    )
    ap.add_argument(
        "--skip-transcription",
        action="store_true",
        default=True,
        help="Use existing alignment maps; do not invoke Whisper (default: true)",
    )
    ap.add_argument(
        "--no-skip-transcription",
        action="store_true",
        help="Run live Whisper transcription against BOOKSHIFT_M1_WHISPER_URL",
    )
    ap.add_argument(
        "--skip-compile",
        action="store_true",
        help="Use pre-built fine locator tables; skip compile timing",
    )
    ap.add_argument(
        "--skip-disruption",
        action="store_true",
        help="Skip concurrent-session disruption test",
    )
    ap.add_argument(
        "--spot-check",
        type=int,
        nargs="?",
        const=DEFAULT_SAMPLE_COUNT,
        default=None,
        metavar="N",
        help=(
            "Run N-point bidirectional FINE/COARSE spot-check instead of the "
            f"progressive-sync harness (default N={DEFAULT_SAMPLE_COUNT})"
        ),
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=42,
        help="RNG seed for spot-check sampling (default: 42)",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Markdown report path (default: BENCHMARK_RESULTS.md, or "
            "data/benchmarks/artifacts/spot_check_results.md with --spot-check)"
        ),
    )
    return ap


def _load_fixtures(args: argparse.Namespace):
    keys = [k.strip() for k in args.books.split(",") if k.strip()] or None
    catalog = load_catalog(args.catalog)
    return select_fixtures(catalog, keys=keys)


def _run_spot_check(args: argparse.Namespace) -> int:
    fixtures = _load_fixtures(args)
    if not fixtures:
        print("ERROR: no runnable fixtures (check EPUB paths in catalog)", file=sys.stderr)
        return 2
    n = int(args.spot_check)
    print(f"Running bidirectional spot-check N={n} for {len(fixtures)} title(s)…", flush=True)
    try:
        results = run_spot_checks(fixtures, sample_count=n, seed=args.seed, progress=print)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    table = render_spot_check(results)
    out = args.output or (PKG_ROOT / "data" / "benchmarks" / "artifacts" / "spot_check_results.md")
    write_spot_check_results(str(out), results)
    print("\n" + table, flush=True)
    print(f"\nWrote {out}", flush=True)
    print(f"Wrote {Path(out).with_suffix('.json')}", flush=True)

    ok = all(r.passed() for r in results)
    print(f"SPOT_CHECK_ACCEPT={'PASS' if ok else 'FAIL'}", flush=True)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.spot_check is not None:
        return _run_spot_check(args)

    skip_transcription = not args.no_skip_transcription
    if not skip_transcription:
        from bookshift.benchmarks.transcribe import probe_whisper
        from bookshift.config import get_settings

        whisper_url = get_settings().m1_whisper_url
        print(f"Probing Whisper at {whisper_url} …", flush=True)
        health = probe_whisper(whisper_url)
        if not health.get("online"):
            print(
                f"ERROR: Whisper endpoint not reachable: {health.get('error') or health}",
                file=sys.stderr,
            )
            return 2
        print(
            f"Whisper health OK status={health.get('http_status')} "
            f"latency_ms={health.get('latency_ms')}",
            flush=True,
        )

    fixtures = _load_fixtures(args)
    if not fixtures:
        print("ERROR: no runnable fixtures (check EPUB paths in catalog)", file=sys.stderr)
        return 2

    print(f"Running benchmarks for {len(fixtures)} title(s)…", flush=True)
    results = run_all(
        fixtures,
        skip_transcription=skip_transcription,
        skip_compile=args.skip_compile,
        skip_disruption=args.skip_disruption,
        progress=print,
    )

    table = render_results_table(results)
    out = args.output or (PKG_ROOT / "BENCHMARK_RESULTS.md")
    write_benchmark_results(str(out), results)
    print("\n" + table, flush=True)
    print(f"\nWrote {out}", flush=True)

    ok = all(r.passed() for r in results)
    print(f"BENCHMARK_ACCEPT={'PASS' if ok else 'FAIL'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
