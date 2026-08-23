"""Bidirectional 100-point FINE / COARSE spot-check harness.

Samples randomized audio timestamps and sentence XPointers against an
active FINE index (ground truth) and the COARSE chapter map, measuring
exact precision, drift, and lookup latency in both directions.
"""

from __future__ import annotations

import json
import random
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Any

from bookshift.benchmarks.fixtures import BookFixture
from bookshift.config import REPO_ROOT
from bookshift.domain.locator_index import CoarseChapterIndex, FineLocatorIndex
from bookshift.domain.sync_cache import BookBundle, resolve_position

_DF_RE = re.compile(r"DocFragment\[(\d+)\]")
_ARTIFACTS = REPO_ROOT / "data" / "benchmarks" / "artifacts"

# Audio→ebook→audio lands on the sentence start, not the query time.
# "Same sentence" identity is the pass condition; drift is reported separately.
DEFAULT_SAMPLE_COUNT = 100
DEFAULT_SEED = 42


def _quantile(values: list[float], q: float) -> float:
    """Linear-interpolated quantile; q in [0, 1]."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    q = min(1.0, max(0.0, q))
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return float(ordered[lo]) * (1.0 - frac) + float(ordered[hi]) * frac


def _mean(values: list[float]) -> float:
    return float(statistics.mean(values)) if values else 0.0


def _max(values: list[float]) -> float:
    return float(max(values)) if values else 0.0


def load_locator_rows(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = list(doc.get("locators") or doc.get("sentences") or [])
    rows.sort(key=lambda r: float(r["audio_seconds"]))
    return rows


def discover_fine_locator_table(fixture: BookFixture) -> Path | None:
    """Catalog path, then benchmark artifacts produced by a prior FINE run."""
    candidates: list[Path | None] = [
        fixture.fine_locator_table,
        _ARTIFACTS / f"{fixture.key}_fine_table.json",
        _ARTIFACTS / f"compiled_{fixture.key}.enriched.json",
    ]
    for path in candidates:
        if path is not None and path.is_file():
            return path
    return None


def discover_coarse_map_path(fixture: BookFixture) -> Path | None:
    candidates: list[Path | None] = [
        fixture.coarse_map,
        _ARTIFACTS / f"coarse_{fixture.key}.json",
    ]
    for path in candidates:
        if path is not None and path.is_file():
            return path
    return None


def load_or_build_coarse_map(fixture: BookFixture) -> dict[str, Any]:
    cached = discover_coarse_map_path(fixture)
    if cached is not None:
        return json.loads(cached.read_text(encoding="utf-8"))
    from bookshift.benchmarks.harness import BenchmarkRunner

    runner = BenchmarkRunner(fixture, skip_compile=True, skip_disruption=True)
    cmap, _elapsed = runner._build_coarse_map()
    return cmap


def doc_fragment_of(xp: str) -> int | None:
    m = _DF_RE.search(xp or "")
    return int(m.group(1)) if m else None


def fine_timestamp_for_xpointer(fine: FineLocatorIndex, xp: str) -> float | None:
    """Ground-truth audio time of a (possibly chapter-level) XPointer.

    Exact sentence hits use the FINE row. Chapter-level COARSE locators
    (``/body/DocFragment[N]/body/text().0``) resolve to the first FINE
    sentence in that DocFragment.
    """
    row = fine.by_xpointer(xp)
    if row is not None:
        return float(row["audio_seconds"])
    df = doc_fragment_of(xp)
    if df is None:
        return None
    for r in fine.rows:
        rdf = r.get("doc_fragment_index")
        if rdf is None:
            rdf = doc_fragment_of(str(r.get("xpointer") or ""))
        if rdf is not None and int(rdf) == df:
            return float(r["audio_seconds"])
    return None


@dataclass
class FinePrecision:
    exact_match_rate: float
    mean_error_s: float
    max_error_s: float
    sample_count: int

    def as_percent(self) -> float:
        return self.exact_match_rate * 100.0


@dataclass
class CoarsePrecision:
    median_error_s: float
    mean_error_s: float
    p90_error_s: float
    max_error_s: float
    sample_count: int


@dataclass
class LookupLatency:
    fine_mean_ms: float
    fine_p95_ms: float
    coarse_mean_ms: float
    coarse_p95_ms: float
    sample_count: int


@dataclass
class RoundTripMetrics:
    sample_count: int
    audio_to_ebook_to_audio_same_sentence_rate: float
    audio_to_ebook_to_audio_mean_drift_s: float
    audio_to_ebook_to_audio_max_drift_s: float
    ebook_to_audio_to_ebook_exact_rate: float


@dataclass
class DirectionBreakdown:
    sample_count: int
    fine_exact_match_rate: float
    fine_mean_error_s: float
    fine_max_error_s: float
    coarse_median_error_s: float
    coarse_mean_error_s: float
    coarse_p90_error_s: float
    coarse_max_error_s: float
    fine_mean_latency_ms: float
    fine_p95_latency_ms: float
    coarse_mean_latency_ms: float
    coarse_p95_latency_ms: float


@dataclass
class SpotCheckResult:
    title: str
    sample_count: int
    seed: int
    coverage_start_s: float
    coverage_end_s: float
    fine_locator_count: int
    fine_precision: FinePrecision
    coarse_precision: CoarsePrecision
    latency: LookupLatency
    round_trip: RoundTripMetrics
    audio_to_book: DirectionBreakdown
    book_to_audio: DirectionBreakdown
    notes: list[str] = field(default_factory=list)

    def passed(self) -> bool:
        if self.sample_count <= 0 or self.fine_locator_count <= 0:
            return False
        return (
            self.fine_precision.exact_match_rate >= 1.0 - 1e-12
            and self.round_trip.ebook_to_audio_to_ebook_exact_rate >= 1.0 - 1e-12
            and self.round_trip.audio_to_ebook_to_audio_same_sentence_rate >= 1.0 - 1e-12
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = "bookshift-spot-check/v1"
        payload["passed"] = self.passed()
        payload["fine_precision"]["exact_match_percent"] = self.fine_precision.as_percent()
        return payload


def _breakdown(
    *,
    fine_hits: int,
    fine_n: int,
    fine_errors: list[float],
    coarse_errors: list[float],
    fine_ms: list[float],
    coarse_ms: list[float],
) -> DirectionBreakdown:
    n = max(fine_n, len(coarse_errors))
    return DirectionBreakdown(
        sample_count=n,
        fine_exact_match_rate=(fine_hits / fine_n) if fine_n else 0.0,
        fine_mean_error_s=_mean(fine_errors),
        fine_max_error_s=_max(fine_errors),
        coarse_median_error_s=float(statistics.median(coarse_errors)) if coarse_errors else 0.0,
        coarse_mean_error_s=_mean(coarse_errors),
        coarse_p90_error_s=_quantile(coarse_errors, 0.90),
        coarse_max_error_s=_max(coarse_errors),
        fine_mean_latency_ms=_mean(fine_ms),
        fine_p95_latency_ms=_quantile(fine_ms, 0.95),
        coarse_mean_latency_ms=_mean(coarse_ms),
        coarse_p95_latency_ms=_quantile(coarse_ms, 0.95),
    )


def _timed_resolve(
    bundle: BookBundle,
    *,
    timestamp: float | None,
    xpointer: str | None,
) -> tuple[dict[str, Any] | None, float, str | None]:
    t0 = time.perf_counter()
    try:
        pos = resolve_position(bundle, timestamp=timestamp, xpointer=xpointer)
    except (KeyError, ValueError) as exc:
        ms = (time.perf_counter() - t0) * 1000.0
        return None, ms, str(exc)
    ms = (time.perf_counter() - t0) * 1000.0
    return pos, ms, None


class SpotCheckRunner:
    """Randomized bidirectional FINE vs COARSE evaluation against a FINE index."""

    def __init__(
        self,
        *,
        coarse_map: dict[str, Any],
        fine_rows: list[dict[str, Any]],
        sample_count: int = DEFAULT_SAMPLE_COUNT,
        seed: int = DEFAULT_SEED,
        audio_duration: float | None = None,
        title: str = "",
        book_id: int = 0,
    ):
        if sample_count <= 0:
            raise ValueError("sample_count must be positive")
        rows = sorted(fine_rows, key=lambda r: float(r["audio_seconds"]))
        if not rows:
            raise ValueError("FINE locator table is empty")
        self.title = title or "spot-check"
        self.sample_count = sample_count
        self.seed = seed
        self.book_id = book_id
        self.fine_rows = rows
        self.fine = FineLocatorIndex(rows)
        self.coarse = CoarseChapterIndex(coarse_map)
        self.fine_bundle = BookBundle(
            book_id=book_id,
            title=self.title,
            active_sync_mode="FINE",
            fine=self.fine,
            coarse=self.coarse,
        )
        self.coarse_bundle = BookBundle(
            book_id=book_id,
            title=self.title,
            active_sync_mode="COARSE",
            coarse=self.coarse,
            fine=self.fine,
        )
        first = float(rows[0]["audio_seconds"])
        last_end = float(rows[-1].get("audio_end_seconds") or rows[-1]["audio_seconds"])
        last_start = float(rows[-1]["audio_seconds"])
        self.coverage_start = first
        self.coverage_end = max(last_end, last_start)
        if audio_duration and audio_duration > 0:
            # Spec samples t ∈ [0, duration]; clamp the draw to FINE coverage
            # so uncovered pre-roll does not masquerade as resolver error.
            self.duration = float(audio_duration)
        else:
            self.duration = self.coverage_end
        self._rng = random.Random(seed)

    @classmethod
    def from_fixture(
        cls,
        fixture: BookFixture,
        *,
        sample_count: int = DEFAULT_SAMPLE_COUNT,
        seed: int = DEFAULT_SEED,
        fine_table: Path | None = None,
    ) -> SpotCheckRunner:
        table_path = fine_table or discover_fine_locator_table(fixture)
        if table_path is None:
            raise FileNotFoundError(
                f"no FINE locator table for {fixture.key!r}; run a FINE alignment "
                f"or place a table at {_ARTIFACTS / f'{fixture.key}_fine_table.json'}"
            )
        rows = load_locator_rows(table_path)
        cmap = load_or_build_coarse_map(fixture)
        return cls(
            coarse_map=cmap,
            fine_rows=rows,
            sample_count=sample_count,
            seed=seed,
            audio_duration=fixture.audio_duration_seconds,
            title=fixture.title,
            book_id=int(fixture.logical_book_id or 0),
        )

    def _sample_timestamps(self, n: int) -> list[float]:
        lo = max(0.0, self.coverage_start)
        hi = min(self.duration, self.coverage_end) if self.duration else self.coverage_end
        if hi <= lo:
            hi = lo + 1e-3
        return [self._rng.uniform(lo, hi) for _ in range(n)]

    def _sample_xpointers(self, n: int) -> list[str]:
        xps = [str(r["xpointer"]) for r in self.fine_rows if r.get("xpointer")]
        if not xps:
            raise ValueError("FINE table has no XPointers")
        if n <= len(xps):
            return self._rng.sample(xps, n)
        return [self._rng.choice(xps) for _ in range(n)]

    def _warmup(self) -> None:
        ts = self.fine.starts[len(self.fine.starts) // 2] if self.fine.starts else 0.0
        xp = str(self.fine_rows[len(self.fine_rows) // 2].get("xpointer") or "")
        for bundle in (self.fine_bundle, self.coarse_bundle):
            _timed_resolve(bundle, timestamp=ts, xpointer=None)
            if xp:
                _timed_resolve(bundle, timestamp=None, xpointer=xp)

    def run(self) -> SpotCheckResult:
        self._warmup()
        timestamps = self._sample_timestamps(self.sample_count)
        xpointers = self._sample_xpointers(self.sample_count)

        a2b = self._audio_to_book(timestamps)
        b2a = self._book_to_audio(xpointers)
        rt = self._round_trip(timestamps, xpointers)

        fine_errors = a2b["fine_errors"] + b2a["fine_errors"]
        fine_n = a2b["fine_n"] + b2a["fine_n"]
        fine_hits = a2b["fine_hits"] + b2a["fine_hits"]
        coarse_errors = a2b["coarse_errors"] + b2a["coarse_errors"]
        fine_ms = a2b["fine_ms"] + b2a["fine_ms"]
        coarse_ms = a2b["coarse_ms"] + b2a["coarse_ms"]

        notes = list(a2b["notes"] + b2a["notes"] + rt["notes"])
        if self.coverage_start > 0.01:
            notes.append(
                f"sampled within FINE coverage [{self.coverage_start:.3f}s, {self.coverage_end:.3f}s]"
            )

        return SpotCheckResult(
            title=self.title,
            sample_count=self.sample_count,
            seed=self.seed,
            coverage_start_s=self.coverage_start,
            coverage_end_s=self.coverage_end,
            fine_locator_count=len(self.fine_rows),
            fine_precision=FinePrecision(
                exact_match_rate=(fine_hits / fine_n) if fine_n else 0.0,
                mean_error_s=_mean(fine_errors),
                max_error_s=_max(fine_errors),
                sample_count=fine_n,
            ),
            coarse_precision=CoarsePrecision(
                median_error_s=float(statistics.median(coarse_errors)) if coarse_errors else 0.0,
                mean_error_s=_mean(coarse_errors),
                p90_error_s=_quantile(coarse_errors, 0.90),
                max_error_s=_max(coarse_errors),
                sample_count=len(coarse_errors),
            ),
            latency=LookupLatency(
                fine_mean_ms=_mean(fine_ms),
                fine_p95_ms=_quantile(fine_ms, 0.95),
                coarse_mean_ms=_mean(coarse_ms),
                coarse_p95_ms=_quantile(coarse_ms, 0.95),
                sample_count=len(fine_ms) + len(coarse_ms),
            ),
            round_trip=RoundTripMetrics(
                sample_count=self.sample_count,
                audio_to_ebook_to_audio_same_sentence_rate=rt["audio_rate"],
                audio_to_ebook_to_audio_mean_drift_s=rt["audio_mean_drift"],
                audio_to_ebook_to_audio_max_drift_s=rt["audio_max_drift"],
                ebook_to_audio_to_ebook_exact_rate=rt["ebook_rate"],
            ),
            audio_to_book=_breakdown(
                fine_hits=a2b["fine_hits"],
                fine_n=a2b["fine_n"],
                fine_errors=a2b["fine_errors"],
                coarse_errors=a2b["coarse_errors"],
                fine_ms=a2b["fine_ms"],
                coarse_ms=a2b["coarse_ms"],
            ),
            book_to_audio=_breakdown(
                fine_hits=b2a["fine_hits"],
                fine_n=b2a["fine_n"],
                fine_errors=b2a["fine_errors"],
                coarse_errors=b2a["coarse_errors"],
                fine_ms=b2a["fine_ms"],
                coarse_ms=b2a["coarse_ms"],
            ),
            notes=notes,
        )

    def _audio_to_book(self, timestamps: list[float]) -> dict[str, Any]:
        fine_errors: list[float] = []
        coarse_errors: list[float] = []
        fine_ms: list[float] = []
        coarse_ms: list[float] = []
        fine_hits = 0
        fine_n = 0
        notes: list[str] = []
        for t in timestamps:
            gt = self.fine.by_timestamp(t)
            pos_f, ms_f, err_f = _timed_resolve(
                self.fine_bundle, timestamp=t, xpointer=None
            )
            fine_ms.append(ms_f)
            if gt is None or pos_f is None:
                notes.append(f"audio→book FINE miss t={t:.4f} err={err_f}")
                continue
            fine_n += 1
            xp_f = str(pos_f.get("xpointer") or "")
            xp_gt = str(gt.get("xpointer") or "")
            cfi_f = pos_f.get("epub_cfi")
            cfi_gt = gt.get("epub_cfi")
            locator_match = xp_f == xp_gt and (
                cfi_gt is None or cfi_f is None or cfi_f == cfi_gt
            )
            if locator_match:
                fine_hits += 1
            t_f = float(pos_f.get("audio_seconds") or 0.0)
            t_gt = float(gt["audio_seconds"])
            fine_errors.append(abs(t_f - t_gt))

            pos_c, ms_c, err_c = _timed_resolve(
                self.coarse_bundle, timestamp=t, xpointer=None
            )
            coarse_ms.append(ms_c)
            if pos_c is None:
                notes.append(f"audio→book COARSE miss t={t:.4f} err={err_c}")
                continue
            xp_c = str(pos_c.get("xpointer") or "")
            t_truth = fine_timestamp_for_xpointer(self.fine, xp_c)
            if t_truth is None:
                notes.append(f"audio→book COARSE xp not in FINE: {xp_c}")
                continue
            # Δt_error = |t_queried − t_ground_truth(XPointer_coarse)|
            coarse_errors.append(abs(t - t_truth))
        return {
            "fine_errors": fine_errors,
            "coarse_errors": coarse_errors,
            "fine_ms": fine_ms,
            "coarse_ms": coarse_ms,
            "fine_hits": fine_hits,
            "fine_n": fine_n,
            "notes": notes,
        }

    def _book_to_audio(self, xpointers: list[str]) -> dict[str, Any]:
        fine_errors: list[float] = []
        coarse_errors: list[float] = []
        fine_ms: list[float] = []
        coarse_ms: list[float] = []
        fine_hits = 0
        fine_n = 0
        notes: list[str] = []
        for xp in xpointers:
            gt = self.fine.by_xpointer(xp)
            pos_f, ms_f, err_f = _timed_resolve(
                self.fine_bundle, timestamp=None, xpointer=xp
            )
            fine_ms.append(ms_f)
            if gt is None or pos_f is None:
                notes.append(f"book→audio FINE miss xp={xp} err={err_f}")
                continue
            fine_n += 1
            t_gt = float(gt["audio_seconds"])
            t_f = float(pos_f.get("audio_seconds") or 0.0)
            xp_f = str(pos_f.get("xpointer") or "")
            if xp_f == xp and abs(t_f - t_gt) <= 1e-9:
                fine_hits += 1
            fine_errors.append(abs(t_f - t_gt))

            pos_c, ms_c, err_c = _timed_resolve(
                self.coarse_bundle, timestamp=None, xpointer=xp
            )
            coarse_ms.append(ms_c)
            if pos_c is None:
                notes.append(f"book→audio COARSE miss xp={xp} err={err_c}")
                continue
            t_c = float(pos_c.get("audio_seconds") or 0.0)
            # Δt_error = |t_coarse − t_ground_truth(XPointer)|
            coarse_errors.append(abs(t_c - t_gt))
        return {
            "fine_errors": fine_errors,
            "coarse_errors": coarse_errors,
            "fine_ms": fine_ms,
            "coarse_ms": coarse_ms,
            "fine_hits": fine_hits,
            "fine_n": fine_n,
            "notes": notes,
        }

    def _round_trip(
        self, timestamps: list[float], xpointers: list[str]
    ) -> dict[str, Any]:
        audio_ok = 0
        audio_n = 0
        drifts: list[float] = []
        ebook_ok = 0
        ebook_n = 0
        notes: list[str] = []

        for t in timestamps:
            fwd, _, err = _timed_resolve(self.fine_bundle, timestamp=t, xpointer=None)
            if fwd is None:
                notes.append(f"roundtrip A→E miss t={t:.4f} err={err}")
                continue
            xp = str(fwd.get("xpointer") or "")
            back, _, err_b = _timed_resolve(
                self.fine_bundle, timestamp=None, xpointer=xp
            )
            if back is None:
                notes.append(f"roundtrip A→E→A miss xp={xp} err={err_b}")
                continue
            audio_n += 1
            t_rt = float(back.get("audio_seconds") or 0.0)
            drifts.append(abs(t_rt - t))
            again, _, _ = _timed_resolve(
                self.fine_bundle, timestamp=t_rt, xpointer=None
            )
            if again is not None and str(again.get("xpointer") or "") == xp:
                audio_ok += 1

        for xp in xpointers:
            fwd, _, err = _timed_resolve(
                self.fine_bundle, timestamp=None, xpointer=xp
            )
            if fwd is None:
                notes.append(f"roundtrip E→A miss xp={xp} err={err}")
                continue
            t = float(fwd.get("audio_seconds") or 0.0)
            back, _, err_b = _timed_resolve(
                self.fine_bundle, timestamp=t, xpointer=None
            )
            if back is None:
                notes.append(f"roundtrip E→A→E miss t={t:.4f} err={err_b}")
                continue
            ebook_n += 1
            if str(back.get("xpointer") or "") == xp:
                ebook_ok += 1

        return {
            "audio_rate": (audio_ok / audio_n) if audio_n else 0.0,
            "audio_mean_drift": _mean(drifts),
            "audio_max_drift": _max(drifts),
            "ebook_rate": (ebook_ok / ebook_n) if ebook_n else 0.0,
            "notes": notes,
        }


def run_spot_checks(
    fixtures: list[BookFixture],
    *,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    seed: int = DEFAULT_SEED,
    progress: Callable[[str], None] | None = None,
) -> list[SpotCheckResult]:
    emit = progress or (lambda s: None)
    results: list[SpotCheckResult] = []
    for fx in fixtures:
        emit(f"Spot-check {fx.title} (N={sample_count})…")
        runner = SpotCheckRunner.from_fixture(fx, sample_count=sample_count, seed=seed)
        result = runner.run()
        emit(
            f"  FINE exact {result.fine_precision.as_percent():.1f}%  "
            f"COARSE mean ±{result.coarse_precision.mean_error_s:.1f}s  "
            f"FINE p95 {result.latency.fine_p95_ms:.3f}ms"
        )
        results.append(result)
    return results
