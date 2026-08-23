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
    record_observations: bool = True

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


def abs_progress_signature(current_time_s: float, is_finished: bool) -> str:
    """Stable identity for reader-visible Audiobookshelf progress."""
    payload = json.dumps(
        {
            "currentTime": round(float(current_time_s), 3),
            "isFinished": bool(is_finished),
        },
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
    abs_signature: str = "",
    previous_abs_signature: str = "",
    abs_revision: int = 0,
    previous_abs_revision: int = 0,
    orbit_revision: int = 0,
    previous_orbit_revision: int = 0,
    last_sync_timestamp: float = 0.0,
    last_sync_progress: float = 0.0,
    conflict_deadband_ms: int = 30_000,
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
    tracks_abs = bool(abs_signature)
    has_previous_abs = bool(previous_abs_signature) and previous_abs_revision > 0
    abs_signature_changed = has_previous_abs and abs_signature != previous_abs_signature
    abs_changed = abs_signature_changed and abs_revision > previous_abs_revision
    abs_stale = abs_signature_changed and abs_revision <= previous_abs_revision

    def target_is_bookshift_echo(source: str, revision_ms: int) -> bool:
        """Reject a recent target-side revision that can be our own write echo."""
        if not last_sync_source or not last_sync_timestamp:
            return False
        target_source = "bookorbit" if last_sync_source == "abs" else "abs"
        if source != target_source:
            return False
        source_audio_s = orbit_audio_s if source == "bookorbit" else abs_audio_s
        near_written_position = abs(float(source_audio_s or 0.0) - last_sync_progress) <= 2.0
        near_write_time = abs((revision_ms / 1000.0) - last_sync_timestamp) <= 30.0
        return near_written_position and near_write_time

    def conflict_winner() -> str | None:
        """Return the clearly newer non-echo source, else require a safe rebase."""
        if abs_revision <= 0 or orbit_revision <= 0:
            return None
        if previous_orbit_revision > 0 and orbit_revision <= previous_orbit_revision:
            return "abs" if not target_is_bookshift_echo("abs", abs_revision) else None
        revision_delta = int(abs_revision) - int(orbit_revision)
        if abs(revision_delta) <= max(0, int(conflict_deadband_ms)):
            return None
        source = "abs" if revision_delta > 0 else "bookorbit"
        revision = abs_revision if source == "abs" else orbit_revision
        if target_is_bookshift_echo(source, revision):
            return None
        return source

    if tracks_abs:
        if not has_previous or not has_previous_abs:
            return SyncDecision("skip", "progress observation baseline established")
        if orbit_changed and abs_changed:
            winner = conflict_winner()
            if winner == "abs" and baseline.direction != "skip":
                movement = "backward" if abs_audio_s < float(orbit_audio_s or 0.0) else "forward"
                return SyncDecision(
                    "abs_to_orbit",
                    f"conflict resolved by newer ABS revision; ABS moved {movement} "
                    f"to {float(abs_audio_s):.1f}s",
                )
            if winner == "bookorbit" and orbit_audio_s is not None and baseline.direction != "skip":
                movement = "backward" if orbit_audio_s < abs_audio_s else "forward"
                return SyncDecision(
                    "orbit_to_abs",
                    f"conflict resolved by newer BookOrbit revision; BookOrbit moved "
                    f"{movement} to {float(orbit_audio_s):.1f}s",
                )
            reason = (
                "ambiguous conflicting activity rebased without write"
                if winner is None
                else f"newer {winner} activity has no actionable mapped delta; observations rebased"
            )
            return SyncDecision("skip", reason, record_observations=True)
        if abs_stale and not orbit_changed:
            return SyncDecision(
                "skip", "stale ABS observation ignored", record_observations=False
            )
        if abs_changed and not orbit_changed and baseline.direction != "skip":
            movement = "backward" if abs_audio_s < float(orbit_audio_s or 0.0) else "forward"
            return SyncDecision(
                "abs_to_orbit",
                f"ABS activity moved {movement} to {float(abs_audio_s):.1f}s",
            )
        if orbit_changed and not abs_changed and orbit_audio_s is not None and baseline.direction != "skip":
            movement = "backward" if orbit_audio_s < abs_audio_s else "forward"
            return SyncDecision(
                "orbit_to_abs",
                f"BookOrbit activity moved {movement} to {float(orbit_audio_s):.1f}s",
            )
        if mode_changed and not orbit_changed and not abs_changed:
            return SyncDecision(
                "skip",
                f"COARSE/FINE reinterpretation without user activity "
                f"({previous_sync_mode}→{active_sync_mode})",
            )
        if not orbit_changed and not abs_changed:
            if last_sync_source == "abs":
                return SyncDecision("skip", "unchanged BookShift write echo from BookOrbit")
            if last_sync_source == "bookorbit":
                return SyncDecision("skip", "unchanged BookShift write echo from ABS")
            return SyncDecision("skip", "neither progress observation changed")

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
