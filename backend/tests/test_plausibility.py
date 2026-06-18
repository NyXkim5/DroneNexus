"""
Unit tests for the kinematic plausibility gate.

Each test targets one specific behaviour described in the design spec.
State vectors are constructed directly as numpy arrays — no Kalman filter
instantiation required, keeping the tests fast and isolated.

CV state layout:   [x, y, z, vx, vy, vz]                      (length 6)
CTRV state layout: [x, y, z, speed, yaw, yaw_rate, climb_rate] (length 7)
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from fusion.plausibility import PlausibilityGate, PlausibilityLimits, PlausibilityResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cv(
    x: float = 0.0, y: float = 0.0, z: float = 50.0,
    vx: float = 10.0, vy: float = 0.0, vz: float = 0.0,
) -> np.ndarray:
    """Build a 6D CV state vector."""
    return np.array([x, y, z, vx, vy, vz], dtype=np.float64)


def _ctrv(
    x: float = 0.0, y: float = 0.0, z: float = 50.0,
    speed: float = 10.0, yaw: float = 0.0,
    yaw_rate: float = 0.0, climb_rate: float = 0.0,
) -> np.ndarray:
    """Build a 7D CTRV state vector."""
    return np.array([x, y, z, speed, yaw, yaw_rate, climb_rate], dtype=np.float64)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_normal_update_passes() -> None:
    """A physically reasonable state change must be accepted."""
    gate = PlausibilityGate()
    before = _cv(x=0.0, vx=20.0)
    after = _cv(x=20.0, vx=21.0)  # 1 m/s delta-v over 1 s, 20 m jump
    result = gate.check_update(before, after, dt=1.0)
    assert result.plausible
    assert result.reason == ""
    assert result.violation_magnitude == 0.0


def test_speed_violation() -> None:
    """Speed above 100 m/s must fail."""
    gate = PlausibilityGate()
    before = _cv(vx=0.0)
    after = _cv(vx=150.0)  # 150 m/s > 100 m/s limit
    result = gate.check_update(before, after, dt=10.0)
    assert not result.plausible
    assert "speed" in result.reason
    assert result.violation_magnitude == pytest.approx(50.0, abs=0.1)


def test_acceleration_violation() -> None:
    """Delta-v / dt above 20 m/s² must fail."""
    gate = PlausibilityGate()
    before = _cv(vx=0.0)
    after = _cv(vx=50.0)  # 50 m/s change in 1 s = 50 m/s² >> 20 limit
    result = gate.check_update(before, after, dt=1.0)
    assert not result.plausible
    assert "acceleration" in result.reason
    assert result.violation_magnitude == pytest.approx(30.0, abs=0.1)


def test_position_jump_violation() -> None:
    """A 500 m jump in one tick must fail."""
    gate = PlausibilityGate()
    before = _cv(x=0.0, vx=10.0)
    # 500 m jump from a 10 m/s track in 1 s: dynamic limit = max(200, 10*1*2) = 200 m
    after = _cv(x=500.0, vx=10.0)
    result = gate.check_update(before, after, dt=1.0)
    assert not result.plausible
    assert "position jump" in result.reason
    assert result.violation_magnitude == pytest.approx(300.0, abs=0.1)


def test_climb_rate_violation() -> None:
    """Vertical speed above 30 m/s must fail."""
    gate = PlausibilityGate()
    # Use a long dt so:
    #   accel = 45 / 100 = 0.45 m/s²  < 20 limit (pass)
    #   position jump = 45 * 100 / 2 = 2250 m, but dynamic_limit = max(200, 0*100*2) = 200
    # To keep position jump inside limits, start with vz already close to 45 so
    # delta-v is tiny, and keep the z displacement small.
    # before: vz=44, after: vz=45 — delta_v=1, dt=1 → accel=1 m/s² (pass)
    # position jump in z = ~44.5 m (avg vz * dt), total |pos jump| ≈ 44.5 m < 200 m (pass)
    # speed = 45 m/s < 100 m/s (pass)
    # climb rate = 45 m/s > 30 limit → FAIL
    before = _cv(x=0.0, y=0.0, z=0.0, vx=0.0, vy=0.0, vz=44.0)
    after = _cv(x=0.0, y=0.0, z=44.5, vx=0.0, vy=0.0, vz=45.0)
    result = gate.check_update(before, after, dt=1.0)
    assert not result.plausible
    assert "climb rate" in result.reason
    assert result.violation_magnitude == pytest.approx(15.0, abs=0.1)


def test_stationary_always_passes() -> None:
    """Zero velocity and zero change is trivially plausible."""
    gate = PlausibilityGate()
    state = _cv(vx=0.0, vy=0.0, vz=0.0)
    result = gate.check_update(state, state.copy(), dt=1.0)
    assert result.plausible


def test_custom_limits() -> None:
    """Custom PlausibilityLimits must override the defaults."""
    strict = PlausibilityLimits(max_speed_ms=5.0)
    gate = PlausibilityGate(limits=strict)
    before = _cv(vx=0.0)
    after = _cv(vx=8.0)  # 8 m/s > custom 5 m/s limit
    result = gate.check_update(before, after, dt=1.0)
    assert not result.plausible
    assert "speed" in result.reason
    assert result.violation_magnitude == pytest.approx(3.0, abs=0.1)


def test_works_with_cv_state() -> None:
    """check_track must accept a 6D CV state and pass a plausible one."""
    gate = PlausibilityGate()
    state = _cv(vx=15.0, vy=5.0, vz=2.0)
    result = gate.check_track(state)
    assert result.plausible


def test_works_with_ctrv_state() -> None:
    """check_update must accept 7D CTRV vectors and pass a plausible transition."""
    gate = PlausibilityGate()
    before = _ctrv(x=0.0, speed=10.0, yaw=0.0, climb_rate=0.0)
    after = _ctrv(x=10.0, speed=11.0, yaw=0.05, climb_rate=0.5)
    result = gate.check_update(before, after, dt=1.0)
    assert result.plausible


def test_result_has_violation_magnitude() -> None:
    """A failed check must report a positive violation_magnitude."""
    gate = PlausibilityGate()
    before = _cv(vx=0.0)
    after = _cv(vx=200.0)  # 200 m/s, well over the 100 m/s limit
    result = gate.check_update(before, after, dt=100.0)
    assert not result.plausible
    assert result.violation_magnitude > 0.0
    assert result.violation_magnitude == pytest.approx(100.0, abs=0.1)


def test_ctrv_yaw_rate_violation_in_check_track() -> None:
    """CTRV yaw rate beyond 180 deg/s must fail check_track."""
    gate = PlausibilityGate()
    # 360 deg/s = 2*pi rad/s
    state = _ctrv(yaw_rate=2.0 * math.pi)
    result = gate.check_track(state)
    assert not result.plausible
    assert "yaw rate" in result.reason
    assert result.violation_magnitude > 0.0


def test_check_track_passes_plausible_ctrv() -> None:
    """A well-behaved CTRV track state must pass check_track."""
    gate = PlausibilityGate()
    state = _ctrv(speed=20.0, yaw=math.pi / 4, yaw_rate=0.1, climb_rate=5.0)
    result = gate.check_track(state)
    assert result.plausible


def test_acceleration_check_skipped_when_dt_zero() -> None:
    """With dt=0 the acceleration check must not fire even for large velocity change.

    With dt=1 a 30 m/s delta-v would be 30 m/s² (> 20 limit) — a clear failure.
    With dt=0 the acceleration check is skipped entirely and 30 m/s is within
    the speed limit, so the result should be plausible (no division by zero, no
    false positive).
    """
    gate = PlausibilityGate()
    before = _cv(vx=0.0)
    after = _cv(vx=30.0)  # 30 m/s — under 100 m/s speed limit, but 30 m/s² at dt=1
    result = gate.check_update(before, after, dt=0.0)
    # dt=0 skips acceleration check; speed and other checks pass → plausible
    assert result.plausible
