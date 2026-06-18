"""
Unit tests for backend/fusion/trajectory.py.

Each test exercises one behavioral contract of TrajectoryPredictor /
PredictedTrajectory. All tests run in isolation with no external I/O.
"""
from __future__ import annotations

import copy
import math
import time

import pytest

from csontology import Track, TrackClass, Vec3
from fusion.kalman import (
    ConstantVelocityKalman,
    CTRVKalmanFilter,
    KalmanFilter,
    create_filter,
)
from fusion.trajectory import PredictedTrajectory, TrajectoryPoint, TrajectoryPredictor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cv_filter(
    position: Vec3 = (0.0, 0.0, 100.0),
    velocity: Vec3 = (10.0, 0.0, 0.0),
    pos_sigma: float = 5.0,
    vel_sigma: float = 2.0,
    process_noise: float = 1.0,
) -> ConstantVelocityKalman:
    return ConstantVelocityKalman(
        position=position,
        velocity=velocity,
        pos_sigma=pos_sigma,
        vel_sigma=vel_sigma,
        process_noise=process_noise,
    )


def _ctrv_filter(
    position: Vec3 = (0.0, 0.0, 100.0),
    speed: float = 20.0,
    yaw: float = 0.0,
    yaw_rate: float = 0.3,
    climb_rate: float = 0.0,
) -> CTRVKalmanFilter:
    return CTRVKalmanFilter(
        position=position,
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


def _track(
    track_id: str = "T001",
    position: Vec3 = (0.0, 0.0, 100.0),
    velocity: Vec3 = (10.0, 0.0, 0.0),
    t: float = 0.0,
) -> Track:
    return Track(
        id=track_id,
        position=position,
        velocity=velocity,
        covariance=(5.0, 5.0, 5.0),
        last_update=t,
        classification=TrackClass.HOSTILE,
        confidence=0.9,
    )


# ---------------------------------------------------------------------------
# test_cv_straight_line
# ---------------------------------------------------------------------------

def test_cv_straight_line() -> None:
    """CV filter moving east should predict positions increasing in x only."""
    kf = _cv_filter(position=(0.0, 0.0, 100.0), velocity=(10.0, 0.0, 0.0))
    track = _track(position=(0.0, 0.0, 100.0), velocity=(10.0, 0.0, 0.0))
    predictor = TrajectoryPredictor(horizon_s=10.0, step_s=1.0)

    traj = predictor.predict(track, kf)

    assert len(traj.points) == 11  # t=0 anchor + 10 steps

    # Each successive point should have greater x, constant y and z.
    for i in range(1, len(traj.points)):
        prev = traj.points[i - 1]
        curr = traj.points[i]
        assert curr.position[0] > prev.position[0], "x must grow eastward"
        assert curr.position[1] == pytest.approx(0.0, abs=1e-6), "y must stay zero"
        assert curr.position[2] == pytest.approx(100.0, abs=1e-6), "z must stay constant"


# ---------------------------------------------------------------------------
# test_ctrv_curves
# ---------------------------------------------------------------------------

def test_ctrv_curves() -> None:
    """CTRV with yaw_rate > 0 predicts a curved arc, not a straight line."""
    kf = _ctrv_filter(position=(0.0, 0.0, 100.0), speed=20.0, yaw=0.0, yaw_rate=0.3)
    track = _track(position=(0.0, 0.0, 100.0), velocity=(20.0, 0.0, 0.0))
    predictor = TrajectoryPredictor(horizon_s=10.0, step_s=1.0)

    traj = predictor.predict(track, kf)

    # Extract y values from all points after the anchor.
    y_values = [pt.position[1] for pt in traj.points[1:]]

    # A circular arc with positive yaw_rate should produce non-zero y displacement.
    max_y_deviation = max(abs(y) for y in y_values)
    assert max_y_deviation > 1.0, (
        "CTRV curved arc must show meaningful lateral displacement"
    )

    # Verify it is NOT a straight line: not all y values are effectively zero.
    assert not all(abs(y) < 0.01 for y in y_values), (
        "CTRV trajectory must curve, not remain straight"
    )


# ---------------------------------------------------------------------------
# test_covariance_grows
# ---------------------------------------------------------------------------

def test_covariance_grows() -> None:
    """Position uncertainty (sigma_x) must increase along the horizon."""
    kf = _cv_filter(pos_sigma=3.0, vel_sigma=1.0, process_noise=2.0)
    track = _track()
    predictor = TrajectoryPredictor(horizon_s=10.0, step_s=1.0)

    traj = predictor.predict(track, kf)

    sigmas = [pt.covariance[0] for pt in traj.points]
    for i in range(1, len(sigmas)):
        assert sigmas[i] >= sigmas[i - 1], (
            f"sigma_x at step {i} ({sigmas[i]:.4f}) must be >= "
            f"step {i-1} ({sigmas[i-1]:.4f})"
        )

    # Ensure it actually grew by the end, not just stayed flat.
    assert sigmas[-1] > sigmas[0], "sigma_x must grow over the full horizon"


# ---------------------------------------------------------------------------
# test_position_at_interpolates
# ---------------------------------------------------------------------------

def test_position_at_interpolates() -> None:
    """position_at() must return an interpolated value between two steps."""
    kf = _cv_filter(position=(0.0, 0.0, 0.0), velocity=(10.0, 0.0, 0.0))
    track = _track(position=(0.0, 0.0, 0.0), velocity=(10.0, 0.0, 0.0), t=100.0)
    predictor = TrajectoryPredictor(horizon_s=10.0, step_s=2.0)

    traj = predictor.predict(track, kf)

    # Anchor at t=100.0, next point at t=102.0.
    # Midpoint interpolation at t=101.0 should give x roughly between the two.
    p_at_100 = traj.position_at(100.0)
    p_at_102 = traj.position_at(102.0)
    p_at_101 = traj.position_at(101.0)

    assert p_at_100 is not None
    assert p_at_102 is not None
    assert p_at_101 is not None

    assert p_at_100[0] < p_at_101[0] < p_at_102[0], (
        "Interpolated x at midpoint must fall strictly between step endpoints"
    )


def test_position_at_out_of_range_returns_none() -> None:
    """position_at() returns None for timestamps outside the trajectory window."""
    kf = _cv_filter()
    track = _track(t=0.0)
    predictor = TrajectoryPredictor(horizon_s=5.0, step_s=1.0)

    traj = predictor.predict(track, kf)

    assert traj.position_at(-1.0) is None, "Before start must return None"
    assert traj.position_at(999.0) is None, "After end must return None"


# ---------------------------------------------------------------------------
# test_time_to_point_finds_intercept
# ---------------------------------------------------------------------------

def test_time_to_point_finds_intercept() -> None:
    """Track moving east toward a target should return a positive intercept time."""
    # Target is 100 m east of starting position.
    target: Vec3 = (100.0, 0.0, 100.0)
    kf = _cv_filter(position=(0.0, 0.0, 100.0), velocity=(10.0, 0.0, 0.0))
    track = _track(position=(0.0, 0.0, 100.0), velocity=(10.0, 0.0, 0.0), t=0.0)
    predictor = TrajectoryPredictor(horizon_s=30.0, step_s=0.5)

    traj = predictor.predict(track, kf)
    t_intercept = traj.time_to_point(target, threshold_m=10.0)

    assert t_intercept is not None, "Moving toward target must produce an intercept"
    # At 10 m/s it takes ~10 s to travel 100 m; allow generous bounds for step size.
    assert 5.0 <= t_intercept <= 20.0, (
        f"Intercept time {t_intercept:.2f} s is outside expected window [5, 20]"
    )


# ---------------------------------------------------------------------------
# test_time_to_point_no_intercept
# ---------------------------------------------------------------------------

def test_time_to_point_no_intercept() -> None:
    """Track moving away from a target must return None."""
    # Target is 500 m west; track moves east. Will never get within 50 m.
    target: Vec3 = (-500.0, 0.0, 100.0)
    kf = _cv_filter(position=(0.0, 0.0, 100.0), velocity=(10.0, 0.0, 0.0))
    track = _track(position=(0.0, 0.0, 100.0), velocity=(10.0, 0.0, 0.0), t=0.0)
    predictor = TrajectoryPredictor(horizon_s=30.0, step_s=0.5)

    traj = predictor.predict(track, kf)
    t_intercept = traj.time_to_point(target, threshold_m=50.0)

    assert t_intercept is None, "Track moving away must return None"


# ---------------------------------------------------------------------------
# test_predict_batch
# ---------------------------------------------------------------------------

def test_predict_batch() -> None:
    """predict_batch produces one trajectory per track with a matching filter."""
    tracks = [
        _track(track_id="T001", t=0.0),
        _track(track_id="T002", t=0.0),
        _track(track_id="T003", t=0.0),
    ]
    filters: dict[str, KalmanFilter] = {
        "T001": _cv_filter(),
        "T002": _cv_filter(velocity=(0.0, 10.0, 0.0)),
        # T003 intentionally omitted to test skip behavior.
    }
    predictor = TrajectoryPredictor(horizon_s=5.0, step_s=1.0)

    results = predictor.predict_batch(tracks, filters)

    assert len(results) == 2, "Only tracks with filters should produce trajectories"
    ids = {r.track_id for r in results}
    assert "T001" in ids
    assert "T002" in ids
    assert "T003" not in ids


# ---------------------------------------------------------------------------
# test_clone_does_not_mutate
# ---------------------------------------------------------------------------

def test_clone_does_not_mutate() -> None:
    """The live filter state must be identical before and after predict()."""
    kf = _cv_filter(position=(10.0, 20.0, 30.0), velocity=(5.0, -3.0, 1.0))
    track = _track(position=(10.0, 20.0, 30.0), velocity=(5.0, -3.0, 1.0), t=0.0)

    pos_before = kf.position
    vel_before = kf.velocity
    sigma_before = kf.position_sigma

    predictor = TrajectoryPredictor(horizon_s=30.0, step_s=0.5)
    predictor.predict(track, kf)

    assert kf.position == pos_before, "position must be unchanged after predict()"
    assert kf.velocity == vel_before, "velocity must be unchanged after predict()"
    assert kf.position_sigma == sigma_before, "sigma must be unchanged after predict()"


def test_clone_does_not_mutate_ctrv() -> None:
    """CTRV filter state must also be unchanged after trajectory prediction."""
    kf = _ctrv_filter(position=(0.0, 0.0, 50.0), speed=15.0, yaw=0.5, yaw_rate=0.2)
    track = _track(position=(0.0, 0.0, 50.0), t=0.0)

    pos_before = kf.position
    vel_before = kf.velocity
    raw_before = kf.raw_state.copy()
    cov_before = kf.covariance.copy()

    predictor = TrajectoryPredictor(horizon_s=20.0, step_s=0.5)
    predictor.predict(track, kf)

    assert kf.position == pos_before
    assert kf.velocity == vel_before
    import numpy as np
    assert np.allclose(kf.raw_state, raw_before)
    assert np.allclose(kf.covariance, cov_before)


# ---------------------------------------------------------------------------
# test_to_dict_serialization
# ---------------------------------------------------------------------------

def test_to_dict_serialization() -> None:
    """to_dict() produces the expected structure for WebSocket transmission."""
    kf = _cv_filter(position=(1.0, 2.0, 3.0), velocity=(4.0, 5.0, 6.0))
    track = _track(track_id="T999", position=(1.0, 2.0, 3.0), t=500.0)
    predictor = TrajectoryPredictor(horizon_s=2.0, step_s=1.0)

    traj = predictor.predict(track, kf)
    d = traj.to_dict()

    assert d["track_id"] == "T999"
    assert isinstance(d["points"], list)
    assert len(d["points"]) == 3  # t=500, t=501, t=502

    first = d["points"][0]
    assert "timestamp" in first
    assert "position" in first
    assert "velocity" in first
    assert "covariance" in first

    pos = first["position"]
    assert set(pos.keys()) == {"x", "y", "z"}
    assert pos["x"] == pytest.approx(1.0, abs=1e-6)
    assert pos["y"] == pytest.approx(2.0, abs=1e-6)
    assert pos["z"] == pytest.approx(3.0, abs=1e-6)

    cov = first["covariance"]
    assert set(cov.keys()) == {"sigma_x", "sigma_y", "sigma_z"}
    for key in ("sigma_x", "sigma_y", "sigma_z"):
        assert cov[key] > 0.0, f"{key} must be positive"

    # Timestamps must be monotonically increasing.
    timestamps = [p["timestamp"] for p in d["points"]]
    for i in range(1, len(timestamps)):
        assert timestamps[i] > timestamps[i - 1], "Timestamps must be strictly increasing"
