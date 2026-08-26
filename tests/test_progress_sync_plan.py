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
    orbit_revision: int = 200_000,
    previous_orbit_revision: int = 200_000,
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
        orbit_revision=orbit_revision,
        previous_orbit_revision=previous_orbit_revision,
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


def test_stale_bookorbit_observation_is_not_activity():
    decision = _plan(
        abs_audio_s=200.0,
        orbit_audio_s=100.0,
        orbit_signature="different-but-stale",
        orbit_revision=199_000,
    )
    assert decision.direction == "skip"
    assert "stale BookOrbit" in decision.reason
    assert decision.record_observations is False


def test_both_changed_abs_clearly_newer_wins():
    decision = _plan(
        abs_audio_s=200.0,
        orbit_audio_s=100.0,
        abs_signature="abs-new",
        previous_abs_signature="abs-old",
        abs_revision=300_000,
        orbit_signature="orbit-new",
        previous_orbit_signature="orbit-old",
        orbit_revision=200_000,
        previous_orbit_revision=190_000,
    )
    assert decision.direction == "abs_to_orbit"
    assert "newer ABS revision" in decision.reason


def test_both_changed_bookorbit_clearly_newer_wins():
    decision = _plan(
        abs_audio_s=200.0,
        orbit_audio_s=100.0,
        abs_signature="abs-new",
        previous_abs_signature="abs-old",
        abs_revision=200_000,
        orbit_signature="orbit-new",
        previous_orbit_signature="orbit-old",
        orbit_revision=300_000,
    )
    assert decision.direction == "orbit_to_abs"
    assert "newer BookOrbit revision" in decision.reason


def test_near_simultaneous_conflict_skips_and_rebases():
    decision = _plan(
        abs_audio_s=200.0,
        orbit_audio_s=100.0,
        abs_signature="abs-new",
        previous_abs_signature="abs-old",
        abs_revision=210_000,
        orbit_signature="orbit-new",
        previous_orbit_signature="orbit-old",
        orbit_revision=200_000,
        previous_orbit_revision=190_000,
    )
    assert decision.direction == "skip"
    assert "ambiguous" in decision.reason
    assert decision.record_observations is True


def test_abs_only_movement_after_ambiguous_rebase_wins():
    decision = _plan(
        abs_audio_s=200.0,
        orbit_audio_s=100.0,
        abs_signature="abs-after-rebase",
        previous_abs_signature="abs-rebased",
        abs_revision=400_000,
        previous_abs_revision=300_000,
        orbit_signature="orbit-rebased",
        previous_orbit_signature="orbit-rebased",
        orbit_revision=300_000,
        previous_orbit_revision=300_000,
    )
    assert decision.direction == "abs_to_orbit"


def test_bookorbit_only_movement_after_ambiguous_rebase_wins():
    decision = _plan(
        abs_audio_s=200.0,
        orbit_audio_s=100.0,
        abs_signature="abs-rebased",
        previous_abs_signature="abs-rebased",
        abs_revision=300_000,
        previous_abs_revision=300_000,
        orbit_signature="orbit-after-rebase",
        previous_orbit_signature="orbit-rebased",
        orbit_revision=400_000,
        previous_orbit_revision=300_000,
    )
    assert decision.direction == "orbit_to_abs"


def test_bookshift_target_echo_cannot_win_a_conflict():
    decision = evaluate_reconciliation_plan(
        abs_audio_s=100.0,
        orbit_audio_s=200.0,
        duration_s=DURATION,
        min_delta_pct=MIN_DELTA,
        orbit_signature="orbit-echo",
        previous_orbit_signature="orbit-old",
        active_sync_mode="FINE",
        previous_sync_mode="FINE",
        last_sync_source="abs",
        last_sync_timestamp=200.0,
        last_sync_progress=200.0,
        abs_signature="abs-new",
        previous_abs_signature="abs-old",
        abs_revision=100_000,
        previous_abs_revision=90_000,
        orbit_revision=200_000,
        previous_orbit_revision=90_000,
    )
    assert decision.direction == "skip"
    assert decision.record_observations is True
    assert "ambiguous" in decision.reason


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
        orbit_revision=201_000,
    )
    assert decision.direction == "orbit_to_abs"
    assert "backward" in decision.reason


def test_orbit_forward_activity_remains_symmetric():
    decision = _plan(
        abs_audio_s=100.0,
        orbit_audio_s=200.0,
        orbit_signature="orbit-forward",
        previous_orbit_signature="orbit-before",
        orbit_revision=201_000,
    )
    assert decision.direction == "orbit_to_abs"
    assert "forward" in decision.reason
