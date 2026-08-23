"""COARSE / FINE conversion accuracy metrics."""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass
from typing import Any

from bookshift.domain.locator_index import CoarseChapterIndex, FineLocatorIndex
from bookshift.domain.sync_cache import BookBundle, resolve_position


@dataclass
class AccuracyMetrics:
    sample_count: int
    coarse_mean_error_seconds: float
    coarse_max_error_seconds: float
    fine_exact_match_rate: float
    fine_samples: int = 0


def _sample_timestamps(duration: float, n: int, seed: int = 42) -> list[float]:
    if duration <= 0:
        return []
    rng = random.Random(seed)
    # Avoid edges where bisect returns boundary artifacts.
    lo, hi = max(1.0, duration * 0.01), max(2.0, duration * 0.99)
    return [rng.uniform(lo, hi) for _ in range(n)]


def evaluate_coarse_accuracy(
    coarse_map: dict[str, Any],
    *,
    fine_table: list[dict[str, Any]] | None = None,
    audio_duration: float,
    sample_count: int = 50,
    seed: int = 42,
) -> AccuracyMetrics:
    """Compare COARSE timestamp lookups against FINE ground truth when available."""
    coarse_index = CoarseChapterIndex(coarse_map)
    coarse_bundle = BookBundle(
        book_id=0,
        title="accuracy-coarse",
        active_sync_mode="COARSE",
        coarse=coarse_index,
    )
    fine_bundle = None
    if fine_table:
        fine_index = FineLocatorIndex(fine_table)
        fine_bundle = BookBundle(
            book_id=0,
            title="accuracy-fine",
            active_sync_mode="FINE",
            fine=fine_index,
            coarse=coarse_index,
        )

    timestamps = _sample_timestamps(audio_duration, sample_count, seed=seed)
    errors: list[float] = []
    fine_hits = 0
    fine_total = 0

    for t in timestamps:
        coarse_pos = resolve_position(coarse_bundle, timestamp=t, xpointer=None)
        t_coarse = float(coarse_pos.get("audio_seconds") or t)

        if fine_bundle is not None:
            try:
                fine_pos = resolve_position(fine_bundle, timestamp=t, xpointer=None)
                t_truth = float(fine_pos.get("audio_seconds") or t)
                errors.append(abs(t_coarse - t_truth))
            except KeyError:
                continue
            fine_total += 1
            xp_c = str(coarse_pos.get("xpointer") or "")
            xp_f = str(fine_pos.get("xpointer") or "")
            if xp_c and xp_f and xp_c == xp_f:
                fine_hits += 1
        else:
            # Forward timestamp query: ground truth is the requested playback time.
            errors.append(abs(t_coarse - t))

    mean_err = statistics.mean(errors) if errors else 0.0
    max_err = max(errors) if errors else 0.0
    fine_rate = (fine_hits / fine_total) if fine_total else 0.0
    return AccuracyMetrics(
        sample_count=len(errors),
        coarse_mean_error_seconds=mean_err,
        coarse_max_error_seconds=max_err,
        fine_exact_match_rate=fine_rate,
        fine_samples=fine_total,
    )
