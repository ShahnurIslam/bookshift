"""Sync-loop echo suppression tests."""

from __future__ import annotations

import time

from bookshift.domain.sync_loop import (
    SyncLoopGuard,
    evaluate_reconciliation_plan,
    orbit_progress_signature,
)


def test_suppress_alternate_source_within_debounce():
    now = time.time()
    assert SyncLoopGuard.should_suppress_sync(
        1,
        "abs",
        100.0,
        "bookorbit",
        now - 1.0,
        100.5,
    ) is True


def test_accept_valid_progression():
    now = time.time()
    assert SyncLoopGuard.should_suppress_sync(
        1,
        "abs",
        110.0,
        "bookorbit",
        now - 1.0,
        100.0,
        drift_threshold_sec=2.0,
    ) is False


def test_same_source_never_suppressed():
    now = time.time()
    assert SyncLoopGuard.should_suppress_sync(
        1,
        "abs",
        100.0,
        "abs",
        now - 1.0,
        100.0,
    ) is False


def test_outside_debounce_window_not_suppressed():
    now = time.time()
    assert SyncLoopGuard.should_suppress_sync(
        1,
        "abs",
        100.0,
        "bookorbit",
        now - 10.0,
        100.0,
        debounce_window_sec=5.0,
    ) is False


def _reconcile(
    *,
    orbit_audio_s: float,
    signature: str,
    previous_signature: str,
    mode: str = "FINE",
    previous_mode: str = "FINE",
    last_source: str = "",
):
    return evaluate_reconciliation_plan(
        abs_audio_s=100.0,
        orbit_audio_s=orbit_audio_s,
        duration_s=1000.0,
        min_delta_pct=0.5,
        orbit_signature=signature,
        previous_orbit_signature=previous_signature,
        active_sync_mode=mode,
        previous_sync_mode=previous_mode,
        last_sync_source=last_source,
    )


def test_first_observation_establishes_safe_baseline():
    decision = _reconcile(
        orbit_audio_s=200.0, signature="first", previous_signature="", previous_mode=""
    )
    assert decision.direction == "skip"
    assert "baseline" in decision.reason


def test_coarse_to_fine_reinterpretation_is_not_activity():
    decision = _reconcile(
        orbit_audio_s=200.0,
        signature="same",
        previous_signature="same",
        mode="FINE",
        previous_mode="COARSE",
    )
    assert decision.direction == "skip"
    assert "reinterpretation" in decision.reason


def test_genuine_forward_movement_after_promotion_syncs():
    decision = _reconcile(
        orbit_audio_s=200.0,
        signature="new",
        previous_signature="old",
        mode="FINE",
        previous_mode="COARSE",
    )
    assert decision.direction == "orbit_to_abs"


def test_genuine_backward_movement_is_preserved():
    decision = _reconcile(
        orbit_audio_s=50.0, signature="back", previous_signature="old"
    )
    assert decision.direction == "orbit_to_abs"
    assert "backward" in decision.reason


def test_bookshift_authored_write_echo_is_suppressed():
    decision = _reconcile(
        orbit_audio_s=200.0,
        signature="written",
        previous_signature="written",
        last_source="abs",
    )
    assert decision.direction == "skip"
    assert "BookShift write echo" in decision.reason


def test_progress_signature_tracks_locator_and_meaningful_percentage():
    original = orbit_progress_signature(31.93, "/body/DocFragment[8]/body/p[1]/text().0")
    moved = orbit_progress_signature(31.94, "/body/DocFragment[8]/body/p[2]/text().0")
    assert original != moved
