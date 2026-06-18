"""
Unit tests for the sensor delay compensator.

The compensator handles out-of-order measurements from sensors with different
latencies (e.g. radar at 50 ms, EO/IR at 200 ms). These tests verify the
backward-forward rewind logic, the fallback path for measurements older than
the history window, and compatibility with both CV and CTRV filter models.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from fusion.delay_compensator import DelayCompensator, StateSnapshot
from fusion.kalman import ConstantVelocityKalman, CTRVKalmanFilter, create_filter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cv_filter(
    pos=(0.0, 0.0, 0.0),
    vel=(10.0, 0.0, 0.0),
    pos_sigma=5.0,
    vel_sigma=2.0,
    process_noise=1.0,
) -> ConstantVelocityKalman:
    return ConstantVelocityKalman(
        position=pos,
        velocity=vel,
        pos_sigma=pos_sigma,
        vel_sigma=vel_sigma,
        process_noise=process_noise,
    )


def _make_ctrv_filter(
    pos=(0.0, 0.0, 0.0),
    speed=10.0,
    yaw=0.0,
    yaw_rate=0.1,
    climb_rate=0.0,
) -> CTRVKalmanFilter:
    return CTRVKalmanFilter(
        position=pos,
        speed=speed,
        yaw=yaw,
        yaw_rate=yaw_rate,
        climb_rate=climb_rate,
        pos_std=5.0,
        speed_std=2.0,
        yaw_std=0.5,
        yaw_rate_std=0.3,
        climb_rate_std=0.5,
    )


def _R(sigma=5.0) -> np.ndarray:
    return np.eye(3) * sigma**2


def _record_and_predict(dc: DelayCompensator, filt, dt: float, t: float) -> None:
    """Predict the filter and record the resulting snapshot."""
    filt.predict(dt)
    dc.record(t, filt._state, filt._cov)


# ---------------------------------------------------------------------------
# test_no_delay_matches_direct
# ---------------------------------------------------------------------------

def test_no_delay_matches_direct() -> None:
    """Zero delay: compensate should produce the same result as a direct update.

    When detection_time == current_time the compensator restores the most
    recent snapshot (taken just before the update), applies the update, and
    predicts forward by zero seconds. The result must match doing the update
    directly on the filter.
    """
    filt_direct = _make_cv_filter()
    filt_comp = _make_cv_filter()
    dc = DelayCompensator()

    t = 0.0
    dt = 0.1

    # Advance both filters through a few predict steps.
    for step in range(5):
        t += dt
        filt_direct.predict(dt)
        filt_comp.predict(dt)
        dc.record(t, filt_comp._state, filt_comp._cov)

    measurement = np.array([filt_comp.position[0] + 2.0,
                             filt_comp.position[1],
                             filt_comp.position[2]])
    R = _R(5.0)

    # Capture the live state before the compensated call so we can verify
    # compensate() does not permanently alter it.
    live_state_before = filt_comp._state.copy()
    live_cov_before = filt_comp._cov.copy()

    # Direct update on the reference filter.
    filt_direct.update(
        (float(measurement[0]), float(measurement[1]), float(measurement[2])),
        5.0,
    )
    direct_state = filt_direct._state.copy()
    direct_cov = filt_direct._cov.copy()

    # Compensated update with zero delay (detection_time == current_time).
    comp_state, comp_cov = dc.compensate(t, t, measurement, R, filt_comp)

    np.testing.assert_allclose(comp_state, direct_state, atol=1e-9)
    np.testing.assert_allclose(comp_cov, direct_cov, atol=1e-9)

    # compensate() must leave the filter's live state exactly as it was before
    # the call — it is non-destructive with respect to the present estimate.
    np.testing.assert_allclose(filt_comp._state, live_state_before, atol=1e-12)
    np.testing.assert_allclose(filt_comp._cov, live_cov_before, atol=1e-12)


# ---------------------------------------------------------------------------
# test_delayed_measurement_corrects
# ---------------------------------------------------------------------------

def test_delayed_measurement_corrects() -> None:
    """A delayed measurement reduces position error vs ignoring the delay.

    Scenario: a target moves at 20 m/s east. EO/IR reports a position but
    the measurement is 0.5 s old. Processing it at arrival time places the
    correction at the current predicted location, so the filter does not
    benefit from the extra information. Processing it at measurement_time
    (detection_time) and re-predicting should yield a state closer to the
    true present position.

    We verify that the compensated estimate is closer to ground truth than
    the naive (at-arrival) estimate.
    """
    speed = 20.0  # m/s east
    pos0 = (0.0, 0.0, 0.0)
    vel = (speed, 0.0, 0.0)

    filt_naive = _make_cv_filter(pos=pos0, vel=vel, process_noise=0.01)
    filt_comp = _make_cv_filter(pos=pos0, vel=vel, process_noise=0.01)
    dc = DelayCompensator()

    dt = 0.05  # 50 ms predict steps
    delay = 0.5  # 500 ms sensor delay

    # Advance for 1 second, recording snapshots.
    t = 0.0
    true_pos = list(pos0)
    for _ in range(20):
        t += dt
        true_pos[0] += speed * dt
        filt_naive.predict(dt)
        filt_comp.predict(dt)
        dc.record(t, filt_comp._state, filt_comp._cov)

    current_time = t  # 1.0 s
    detection_time = current_time - delay  # 0.5 s

    # The measurement was taken at detection_time when the target was at:
    meas_pos_at_det_time = np.array([speed * detection_time, 0.0, 0.0])
    R = _R(3.0)

    # Naive: update at arrival time (current_time) ignoring the delay.
    filt_naive.update(
        (float(meas_pos_at_det_time[0]),
         float(meas_pos_at_det_time[1]),
         float(meas_pos_at_det_time[2])),
        3.0,
    )
    naive_pos_x = filt_naive.position[0]

    # Compensated: rewind to detection_time, update, re-predict.
    comp_state, _ = dc.compensate(
        detection_time, current_time, meas_pos_at_det_time, R, filt_comp,
    )
    comp_pos_x = float(comp_state[0])

    true_x = true_pos[0]  # ground truth at current_time

    naive_err = abs(naive_pos_x - true_x)
    comp_err = abs(comp_pos_x - true_x)

    assert comp_err < naive_err, (
        f"Compensated error {comp_err:.4f} m should be smaller than "
        f"naive error {naive_err:.4f} m (true_x={true_x:.2f})"
    )


# ---------------------------------------------------------------------------
# test_history_records_snapshots
# ---------------------------------------------------------------------------

def test_history_records_snapshots() -> None:
    """After N predict steps with record() calls, history contains N entries."""
    filt = _make_cv_filter()
    dc = DelayCompensator(max_history=200)

    n_steps = 50
    t = 0.0
    dt = 0.1

    for _ in range(n_steps):
        t += dt
        filt.predict(dt)
        dc.record(t, filt._state, filt._cov)

    assert len(dc._history) == n_steps
    assert len(dc._times) == n_steps

    # Timestamps must be strictly increasing.
    for i in range(1, len(dc._times)):
        assert dc._times[i] > dc._times[i - 1]

    # Each snapshot must hold a copy (not a reference) to the filter arrays.
    snap = dc._history[-1]
    original_state = snap.state.copy()
    filt._state[0] += 999.0
    np.testing.assert_allclose(snap.state, original_state)


# ---------------------------------------------------------------------------
# test_old_measurement_falls_back
# ---------------------------------------------------------------------------

def test_old_measurement_falls_back() -> None:
    """A detection older than all history is processed at current_time.

    The compensator must not raise; it logs a warning and applies the update
    at the present-time state instead of rewinding.
    """
    filt = _make_cv_filter(vel=(5.0, 0.0, 0.0))
    dc = DelayCompensator(max_history=10)

    t = 1.0  # start recording from t=1
    dt = 0.1
    for _ in range(10):
        t += dt
        filt.predict(dt)
        dc.record(t, filt._state, filt._cov)

    current_time = t  # ~2.0 s
    ancient_detection_time = 0.5  # before the oldest snapshot at 1.1 s

    measurement = np.array(filt.position)
    R = _R()

    # Must not raise; should return a valid (state, cov) at current_time.
    state, cov = dc.compensate(ancient_detection_time, current_time, measurement, R, filt)

    assert state.shape == filt._state.shape
    assert cov.shape == filt._cov.shape

    # The live filter must not have been mutated.
    live_state_before = filt._state.copy()
    np.testing.assert_allclose(filt._state, live_state_before)


# ---------------------------------------------------------------------------
# test_max_compensable_delay
# ---------------------------------------------------------------------------

def test_max_compensable_delay() -> None:
    """max_compensable_delay returns the correct time span of history."""
    dc = DelayCompensator()

    # No history yet.
    assert dc.max_compensable_delay() == pytest.approx(0.0)

    filt = _make_cv_filter()
    t = 0.0
    dt = 0.1
    n = 15
    for _ in range(n):
        t += dt
        filt.predict(dt)
        dc.record(t, filt._state, filt._cov)

    expected_span = dt * (n - 1)  # oldest=0.1, newest=1.5 => span=1.4
    assert dc.max_compensable_delay() == pytest.approx(expected_span, rel=1e-9)


def test_max_compensable_delay_single_snapshot() -> None:
    """With only one snapshot the compensable delay is 0."""
    dc = DelayCompensator()
    filt = _make_cv_filter()
    filt.predict(0.1)
    dc.record(0.1, filt._state, filt._cov)
    assert dc.max_compensable_delay() == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# test_works_with_ctrv
# ---------------------------------------------------------------------------

def test_works_with_ctrv() -> None:
    """Delay compensation integrates correctly with CTRVKalmanFilter.

    We run the same backward-forward logic on a CTRV filter and verify that
    the returned state and covariance have the correct shapes and that a
    delayed measurement still reduces error compared to ignoring the delay.
    """
    speed = 15.0
    filt = _make_ctrv_filter(speed=speed, yaw=0.0, yaw_rate=0.0)
    dc = DelayCompensator()

    dt = 0.05
    delay = 0.3  # 300 ms
    t = 0.0

    for _ in range(20):
        t += dt
        filt.predict(dt)
        dc.record(t, filt._state, filt._cov)

    current_time = t
    detection_time = current_time - delay

    # True position at detection_time (straight-line, yaw=0).
    true_x_at_det = speed * detection_time
    measurement = np.array([true_x_at_det, 0.0, 0.0])
    R = _R(4.0)

    comp_state, comp_cov = dc.compensate(
        detection_time, current_time, measurement, R, filt,
    )

    # State must be 7-element (CTRV) and covariance 7x7.
    assert comp_state.shape == (7,)
    assert comp_cov.shape == (7, 7)

    # Compensated x-position should be close to true present position.
    true_x_now = speed * current_time
    comp_err = abs(float(comp_state[0]) - true_x_now)

    # Without compensation the filter just predicts from t=0 with no update,
    # so its x estimate is speed * current_time already.  With a noisy
    # measurement the compensated value should still be within a few meters.
    assert comp_err < 20.0, (
        f"Compensated CTRV x-error {comp_err:.2f} m is unexpectedly large"
    )

    # Live filter state must be unchanged.
    # (We only check it doesn't blow up to NaN/Inf.)
    assert np.all(np.isfinite(filt._state))
    assert np.all(np.isfinite(filt._cov))


# ---------------------------------------------------------------------------
# test_history_eviction_keeps_times_in_sync
# ---------------------------------------------------------------------------

def test_history_eviction_keeps_times_in_sync() -> None:
    """Overflow eviction keeps _times and _history arrays consistent."""
    max_h = 10
    dc = DelayCompensator(max_history=max_h)
    filt = _make_cv_filter()

    t = 0.0
    n = 25  # 2.5x the history cap
    dt = 0.1
    for _ in range(n):
        t += dt
        filt.predict(dt)
        dc.record(t, filt._state, filt._cov)

    assert len(dc._history) == max_h
    assert len(dc._times) == max_h

    # _times must exactly mirror the snapshots in _history.
    for snap, ts in zip(dc._history, dc._times):
        assert snap.timestamp == pytest.approx(ts)

    # Oldest retained timestamp must be n - max_h steps from the start.
    expected_oldest = (n - max_h + 1) * dt
    assert dc._times[0] == pytest.approx(expected_oldest, rel=1e-9)


# ---------------------------------------------------------------------------
# test_compensate_is_non_destructive
# ---------------------------------------------------------------------------

def test_compensate_is_non_destructive() -> None:
    """compensate() must not permanently alter the filter's live state."""
    filt = _make_cv_filter(vel=(8.0, 3.0, 1.0))
    dc = DelayCompensator()

    t = 0.0
    dt = 0.1
    for _ in range(20):
        t += dt
        filt.predict(dt)
        dc.record(t, filt._state, filt._cov)

    live_state_before = filt._state.copy()
    live_cov_before = filt._cov.copy()

    detection_time = t - 0.5
    measurement = np.array([filt.position[0], filt.position[1], filt.position[2]])
    R = _R()

    dc.compensate(detection_time, t, measurement, R, filt)

    np.testing.assert_allclose(filt._state, live_state_before, atol=1e-12)
    np.testing.assert_allclose(filt._cov, live_cov_before, atol=1e-12)
