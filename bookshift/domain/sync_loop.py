"""Sync-loop echo suppression for bidirectional progress bridges."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SyncDecision:
    """Direction to write, or skip."""

    direction: str
    reason: str

    @property
    def should_sync(self) -> bool:
        return self.direction in ("abs_to_orbit", "orbit_to_abs")


def evaluate_sync_plan(
    *,
    abs_audio_s: float,
    orbit_audio_s: float | None,
    duration_s: float,
    min_delta_pct: float,
    abs_mapped_ebook_pct: float | None = None,
    orbit_pct: float | None = None,
) -> SyncDecision:
    """Choose a direction from comparable progress, preferring audio time."""
    if orbit_audio_s is not None:
        if duration_s > 0:
            delta = 100.0 * (float(orbit_audio_s) - float(abs_audio_s)) / duration_s
            threshold = min_delta_pct
            unit = "% audio"
        else:
            delta = float(orbit_audio_s) - float(abs_audio_s)
            threshold = 30.0
            unit = "s audio"
        if delta >= threshold:
            return SyncDecision("orbit_to_abs", f"BookOrbit ahead by {delta:.3f}{unit}")
        if -delta >= threshold:
            return SyncDecision("abs_to_orbit", f"ABS ahead by {-delta:.3f}{unit}")
        return SyncDecision("skip", "In sync (delta below threshold)")

    if abs_mapped_ebook_pct is None or orbit_pct is None:
        return SyncDecision("skip", "no comparable progress")
    delta_pct = float(abs_mapped_ebook_pct) - float(orbit_pct)
    if delta_pct >= min_delta_pct:
        return SyncDecision("abs_to_orbit", f"ABS mapped progress ahead by {delta_pct:.3f}%")
    if delta_pct <= -min_delta_pct:
        return SyncDecision("skip", "reverse audio mapping is unavailable")
    return SyncDecision("skip", "Delta below threshold")


def orbit_progress_signature(percentage: float, xpointer: str | None) -> str:
    """Stable identity for the user-visible BookOrbit progress record."""
    payload = json.dumps(
        {"percentage": round(float(percentage), 3), "koreaderProgress": xpointer or ""},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluate_reconciliation_plan(
    *,
    abs_audio_s: float,
    orbit_audio_s: float | None,
    duration_s: float,
    min_delta_pct: float,
    orbit_signature: str,
    previous_orbit_signature: str,
    active_sync_mode: str,
    previous_sync_mode: str,
    last_sync_source: str,
    abs_mapped_ebook_pct: float | None = None,
    orbit_pct: float | None = None,
) -> SyncDecision:
    """Separate reader activity from locator promotion and write echoes."""
    baseline = evaluate_sync_plan(
        abs_audio_s=abs_audio_s,
        orbit_audio_s=orbit_audio_s,
        duration_s=duration_s,
        min_delta_pct=min_delta_pct,
        abs_mapped_ebook_pct=abs_mapped_ebook_pct,
        orbit_pct=orbit_pct,
    )
    has_previous = bool(previous_orbit_signature)
    orbit_changed = has_previous and orbit_signature != previous_orbit_signature
    mode_changed = bool(previous_sync_mode) and active_sync_mode != previous_sync_mode

    if not has_previous and baseline.direction == "orbit_to_abs":
        return SyncDecision("skip", "BookOrbit observation baseline established")
    if orbit_changed and orbit_audio_s is not None and baseline.direction != "skip":
        movement = "backward" if orbit_audio_s < abs_audio_s else "forward"
        return SyncDecision(
            "orbit_to_abs",
            f"BookOrbit activity moved {movement} to {float(orbit_audio_s):.1f}s",
        )
    if mode_changed and not orbit_changed and baseline.direction == "orbit_to_abs":
        return SyncDecision(
            "skip",
            f"COARSE/FINE reinterpretation without BookOrbit activity "
            f"({previous_sync_mode}→{active_sync_mode})",
        )
    if has_previous and not orbit_changed and baseline.direction == "orbit_to_abs":
        if last_sync_source == "abs":
            return SyncDecision("skip", "unchanged BookShift write echo from BookOrbit")
        return SyncDecision("skip", "unchanged BookOrbit progress is not new activity")
    return baseline


class SyncLoopGuard:
    """Detect and suppress echo updates between ABS and BookOrbit/KOReader."""

    @staticmethod
    def should_suppress_sync(
        book_id: int,
        source: str,
        progress_seconds: float,
        last_source: str,
        last_timestamp: float,
        last_progress: float,
        *,
        debounce_window_sec: float = 5.0,
        drift_threshold_sec: float = 2.0,
    ) -> bool:
        """Return True when an alternate-source update should be suppressed."""
        _ = book_id  # reserved for structured logging at call sites
        if not last_source or not last_timestamp:
            return False
        if source == last_source:
            return False
        now = time.time()
        if (now - float(last_timestamp)) > debounce_window_sec:
            return False
        drift = abs(float(progress_seconds) - float(last_progress))
        return drift <= drift_threshold_sec

    @staticmethod
    def metadata_from_book(book: dict[str, Any]) -> tuple[str, float, float]:
        return (
            str(book.get("last_sync_source") or ""),
            float(book.get("last_sync_timestamp") or 0.0),
            float(book.get("last_sync_progress") or 0.0),
        )
