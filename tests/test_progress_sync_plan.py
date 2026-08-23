"""Audiobookshelf activity-provenance reconciliation tests."""

from bookshift.domain.sync_loop import (
    abs_progress_signature,
    evaluate_reconciliation_plan,
)


DURATION = 1000.0
MIN_DELTA = 0.5


def _plan(
    *,
    abs_audio_s: float,
    orbit_audio_s: float,
    abs_signature: str = "abs-same",
    previous_abs_signature: str = "abs-same",
    abs_revision: int = 200,
    previous_abs_revision: int = 200,
    orbit_signature: str = "orbit-same",
    previous_orbit_signature: str = "orbit-same",
    mode: str = "FINE",
    previous_mode: str = "FINE",
    last_source: str = "",
):
    return evaluate_reconciliation_plan(
        abs_audio_s=abs_audio_s,
        orbit_audio_s=orbit_audio_s,
        duration_s=DURATION,
        min_delta_pct=MIN_DELTA,
        orbit_signature=orbit_signature,
        previous_orbit_signature=previous_orbit_signature,
        active_sync_mode=mode,
        previous_sync_mode=previous_mode,
        last_sync_source=last_source,
        abs_signature=abs_signature,
        previous_abs_signature=previous_abs_signature,
        abs_revision=abs_revision,
        previous_abs_revision=previous_abs_revision,
    )


def test_abs_signature_tracks_position_and_finished_state():
    original = abs_progress_signature(100.0, False)
    assert original != abs_progress_signature(99.0, False)
    assert original != abs_progress_signature(100.0, True)


def test_first_observation_establishes_baseline():
    decision = _plan(
        abs_audio_s=100.0,
        orbit_audio_s=200.0,
        previous_abs_signature="",
        previous_abs_revision=0,
    )
    assert decision.direction == "skip"
    assert "baseline" in decision.reason


def test_abs_backward_activity_overrides_unchanged_further_orbit_position():
    decision = _plan(
        abs_audio_s=100.0,
        orbit_audio_s=200.0,
        abs_signature="abs-moved-back",
        previous_abs_signature="abs-was-ahead",
        abs_revision=201,
    )
    assert decision.direction == "abs_to_orbit"
    assert "backward" in decision.reason


def test_abs_forward_activity_overrides_unchanged_orbit_position():
    decision = _plan(
        abs_audio_s=200.0,
        orbit_audio_s=100.0,
        abs_signature="abs-moved-forward",
        previous_abs_signature="abs-was-behind",
        abs_revision=201,
    )
    assert decision.direction == "abs_to_orbit"
    assert "forward" in decision.reason


def test_stale_abs_observation_is_not_activity():
    decision = _plan(
        abs_audio_s=200.0,
        orbit_audio_s=100.0,
        abs_signature="different-but-stale",
        abs_revision=199,
    )
    assert decision.direction == "skip"
    assert "stale ABS" in decision.reason
    assert decision.record_observations is False


def test_both_sides_changed_is_explicit_conflict():
    decision = _plan(
        abs_audio_s=200.0,
        orbit_audio_s=100.0,
        abs_signature="abs-new",
        previous_abs_signature="abs-old",
        abs_revision=201,
        orbit_signature="orbit-new",
        previous_orbit_signature="orbit-old",
    )
    assert decision.direction == "skip"
    assert "conflicting activity" in decision.reason
    assert decision.record_observations is False


def test_stable_state_does_not_repeat_writes():
    decision = _plan(abs_audio_s=100.0, orbit_audio_s=200.0)
    assert decision.direction == "skip"
    assert "neither progress observation changed" in decision.reason


def test_bookshift_orbit_write_echo_is_suppressed():
    decision = _plan(
        abs_audio_s=100.0,
        orbit_audio_s=200.0,
        last_source="abs",
    )
    assert decision.direction == "skip"
    assert "BookShift write echo from BookOrbit" in decision.reason


def test_bookshift_abs_write_echo_is_suppressed():
    decision = _plan(
        abs_audio_s=200.0,
        orbit_audio_s=100.0,
        last_source="bookorbit",
    )
    assert decision.direction == "skip"
    assert "BookShift write echo from ABS" in decision.reason


def test_coarse_to_fine_reinterpretation_is_not_activity():
    decision = _plan(
        abs_audio_s=100.0,
        orbit_audio_s=200.0,
        mode="FINE",
        previous_mode="COARSE",
    )
    assert decision.direction == "skip"
    assert "reinterpretation" in decision.reason


def test_orbit_backward_activity_remains_symmetric():
    decision = _plan(
        abs_audio_s=200.0,
        orbit_audio_s=100.0,
        orbit_signature="orbit-backward",
        previous_orbit_signature="orbit-before",
    )
    assert decision.direction == "orbit_to_abs"
    assert "backward" in decision.reason
