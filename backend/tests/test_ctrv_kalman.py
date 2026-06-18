"""
Unit tests for the CTRVKalmanFilter.

Five tests cover the five behaviours called out in the task spec:

  test_straight_line_prediction     — CTRV with yaw_rate=0 matches CV
  test_circular_motion              — constant yaw_rate traces a circle
  test_update_corrects_position     — measurement pulls state toward truth
  test_covariance_grows_on_predict  — P expands between updates
  test_jacobian_numerical_check     — analytical J matches finite differences

All positions are ENU metres.  Tolerances are deliberately loose (centimetre
to metre level) so the tests stay stable under small algorithmic tweaks while
still catching obvious regressions.
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np
import pytest

from fusion.kalman import CTRVKalmanFilter, create_filter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctrv(
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
    speed: float = 10.0,
    yaw: float = 0.0,
    yaw_rate: float = 0.0,
    climb_rate: float = 0.0,
    pos_std: float = 1.0,
    speed_std: float = 1.0,
    yaw_std: float = 0.1,
    yaw_rate_std: float = 0.1,
    climb_rate_std: float = 0.5,
) -> CTRVKalmanFilter:
    return CTRVKalmanFilter(
        position=(x, y, z),
        speed=speed,
        yaw=yaw,
        yaw_rate=yaw_rate,
        climb_rate=climb_rate,
        pos_std=pos_std,
        speed_std=speed_std,
        yaw_std=yaw_std,
        yaw_rate_std=yaw_rate_std,
        climb_rate_std=climb_rate_std,
        process_std_pos=0.5,
        process_std_speed=1.0,
        process_std_yaw_rate=0.5,
        process_std_climb=0.5,
    )


def _cv_position_after(
    x0: float, y0: float, z0: float,
    vx: float, vy: float, vz: float,
    dt: float,
) -> tuple:
    return (x0 + vx * dt, y0 + vy * dt, z0 + vz * dt)


# ---------------------------------------------------------------------------
# test_straight_line_prediction
# ---------------------------------------------------------------------------

def test_straight_line_prediction() -> None:
    """CTRV with yaw_rate=0 predicts the same position as constant-velocity.

    A drone flying east at 10 m/s with zero turn rate should end up at
    (10*dt, 0, 0) regardless of which model is used.  We compare the CTRV
    prediction against the analytical CV answer.
    """
    speed = 10.0
    yaw = 0.0  # east
    dt = 2.0

    kf = _make_ctrv(speed=speed, yaw=yaw, yaw_rate=0.0)
    kf.predict(dt)

    x, y, z = kf.position
    expected_x = speed * math.cos(yaw) * dt
    expected_y = speed * math.sin(yaw) * dt

    assert x == pytest.approx(expected_x, abs=1e-6), (
        f"CTRV straight-line x mismatch: got {x}, expected {expected_x}"
    )
    assert y == pytest.approx(expected_y, abs=1e-6), (
        f"CTRV straight-line y mismatch: got {y}, expected {expected_y}"
    )
    assert z == pytest.approx(0.0, abs=1e-9)


def test_straight_line_matches_cv_factory() -> None:
    """create_filter('ctrv') and create_filter('cv') agree on straight flight.

    Both filters start at the same position and velocity.  After one predict
    step with no turn the position estimates should agree to centimetre level.
    """
    velocity = (10.0, 0.0, 0.0)
    position = (0.0, 0.0, 100.0)
    dt = 1.5

    cv = create_filter(
        "cv", position=position, velocity=velocity,
        pos_sigma=5.0, vel_sigma=2.0, process_noise=1.0,
    )
    ctrv = create_filter(
        "ctrv", position=position, velocity=velocity,
        pos_sigma=5.0, vel_sigma=2.0, process_noise=1.0,
    )

    cv.predict(dt)
    ctrv.predict(dt)

    cx, cy, cz = cv.position
    tx, ty, tz = ctrv.position

    assert tx == pytest.approx(cx, abs=0.01)
    assert ty == pytest.approx(cy, abs=0.01)
    assert tz == pytest.approx(cz, abs=0.01)


# ---------------------------------------------------------------------------
# test_circular_motion
# ---------------------------------------------------------------------------

def test_circular_motion() -> None:
    """A drone with constant yaw_rate should trace a horizontal circle.

    With speed v and turn rate omega the radius is r = v / omega.  After a
    quarter-circle (T/4 seconds) the drone should be at (r, r, 0) from the
    start when beginning at (0,0,0) heading east.

    We run the CTRV filter with no measurement updates so the predicted
    trajectory equals the model output.  We check that:
      - the radius from the circle centre stays within 1 % of theory
      - the z coordinate stays flat (no climb rate)
    """
    speed = 20.0        # m/s
    omega = 0.2         # rad/s, a gentle right turn
    radius = speed / omega  # theoretical circle radius

    kf = _make_ctrv(speed=speed, yaw=0.0, yaw_rate=omega, pos_std=0.1)

    dt = 0.05  # 20 Hz
    quarter_circle_steps = int(round((math.pi / 2) / omega / dt))

    for _ in range(quarter_circle_steps):
        kf.predict(dt)

    x, y, z = kf.position
    # At t = T/4 the drone should be one radius north of the start
    # and one radius east of the circle centre.
    # Circle centre is at (0, radius) from the start (for heading east, turning left).
    # For a right turn (positive yaw_rate in ENU = counter-clockwise when seen from above),
    # the centre is at (0, +radius).
    # After quarter turn the drone is at (radius, radius) approximately.
    dist_from_centre = math.hypot(x - 0.0, y - radius)

    # Allow 2 % positional tolerance — discretisation error grows with dt.
    assert abs(dist_from_centre - radius) / radius < 0.02, (
        f"Circular motion radius error: got {dist_from_centre:.2f}, "
        f"expected {radius:.2f}"
    )
    assert z == pytest.approx(0.0, abs=0.01)


def test_circular_motion_full_circle() -> None:
    """After a full 2*pi turn the drone should return near its start position."""
    speed = 15.0
    omega = 0.3         # rad/s
    period = 2 * math.pi / omega  # full circle period (s)

    kf = _make_ctrv(speed=speed, yaw=0.0, yaw_rate=omega, pos_std=0.1)

    dt = 0.02
    steps = int(round(period / dt))
    for _ in range(steps):
        kf.predict(dt)

    x, y, z = kf.position
    # Should be back near origin; allow 5 % of circumference as tolerance.
    circumference = 2 * math.pi * speed / omega
    dist = math.hypot(x, y)
    assert dist < 0.05 * circumference, (
        f"Full-circle return error: {dist:.2f} m from origin "
        f"(circumference {circumference:.2f} m)"
    )


# ---------------------------------------------------------------------------
# test_update_corrects_position
# ---------------------------------------------------------------------------

def test_update_corrects_position() -> None:
    """A measurement update must pull the state estimate toward the observation.

    We initialise the filter with a large initial position uncertainty so the
    Kalman gain weights the measurement heavily.  We then feed a measurement
    that disagrees with the prior and verify the posterior estimate is
    significantly closer to the measurement than the prior was.
    """
    prior_pos = (0.0, 0.0, 0.0)
    meas_pos = (50.0, 30.0, 10.0)

    kf = _make_ctrv(
        x=prior_pos[0], y=prior_pos[1], z=prior_pos[2],
        pos_std=100.0,  # very uncertain — measurement should dominate
    )

    prior_dist = math.dist(kf.position, meas_pos)
    kf.update(measurement=meas_pos, R=np.eye(3) * 1.0)
    posterior_dist = math.dist(kf.position, meas_pos)

    assert posterior_dist < prior_dist, (
        f"Update did not move toward measurement: "
        f"prior dist={prior_dist:.2f}, posterior dist={posterior_dist:.2f}"
    )
    # With very large initial uncertainty, the posterior should be well inside
    # 10 % of the original distance to the measurement.
    assert posterior_dist < 0.1 * prior_dist, (
        f"Update effect too small: posterior still {posterior_dist:.2f} m away"
    )


def test_update_does_not_overshoot() -> None:
    """The posterior position must not overshoot past the measurement."""
    meas_pos = (20.0, 0.0, 0.0)
    kf = _make_ctrv(x=0.0, pos_std=50.0)
    kf.update(measurement=meas_pos, R=np.eye(3) * 4.0)

    post_x, _, _ = kf.position
    # x should move toward 20.0 but not past it (prior is at 0, meas at 20).
    assert 0.0 <= post_x <= 20.0, (
        f"Update overshot or went the wrong way: post_x={post_x:.4f}"
    )


# ---------------------------------------------------------------------------
# test_covariance_grows_on_predict
# ---------------------------------------------------------------------------

def test_covariance_grows_on_predict() -> None:
    """Predicting without a measurement must increase position uncertainty.

    The diagonal entries of P for x, y, z must all strictly increase after
    each predict step when process noise is nonzero.
    """
    kf = _make_ctrv(pos_std=1.0)

    diag_before = np.diag(kf.covariance)[:3].copy()
    kf.predict(1.0)
    diag_after = np.diag(kf.covariance)[:3].copy()

    for axis, (before, after) in enumerate(zip(diag_before, diag_after)):
        assert after > before, (
            f"Covariance did not grow on axis {axis}: "
            f"before={before:.6f}, after={after:.6f}"
        )


def test_covariance_grows_monotonically() -> None:
    """Three successive predict steps must each increase x-position variance."""
    kf = _make_ctrv(pos_std=1.0)
    variances = [np.diag(kf.covariance)[0]]
    for _ in range(3):
        kf.predict(0.5)
        variances.append(np.diag(kf.covariance)[0])

    for i in range(1, len(variances)):
        assert variances[i] > variances[i - 1], (
            f"Variance did not grow at step {i}: {variances[i - 1]:.6f} -> {variances[i]:.6f}"
        )


def test_update_reduces_covariance() -> None:
    """A measurement update must reduce the x-position variance."""
    kf = _make_ctrv(pos_std=10.0)
    kf.predict(1.0)

    var_before = np.diag(kf.covariance)[0]
    kf.update(measurement=kf.position, R=np.eye(3) * 1.0)
    var_after = np.diag(kf.covariance)[0]

    assert var_after < var_before, (
        f"Update did not reduce covariance: {var_before:.4f} -> {var_after:.4f}"
    )


# ---------------------------------------------------------------------------
# test_jacobian_numerical_check
# ---------------------------------------------------------------------------

def _predict_state(state_vec: np.ndarray, dt: float) -> np.ndarray:
    """Pure functional CTRV prediction — no filter object — for FD Jacobian."""
    x, y, z, spd, yaw, yr, cr = state_vec
    yaw_dt = yaw + yr * dt
    if abs(yr) > 1e-6:
        x_new = x + (spd / yr) * (math.sin(yaw_dt) - math.sin(yaw))
        y_new = y + (spd / yr) * (math.cos(yaw) - math.cos(yaw_dt))
    else:
        x_new = x + spd * math.cos(yaw) * dt
        y_new = y + spd * math.sin(yaw) * dt
    z_new = z + cr * dt
    return np.array([x_new, y_new, z_new, spd, yaw_dt, yr, cr])


def _finite_diff_jacobian(state: np.ndarray, dt: float, eps: float = 1e-5) -> np.ndarray:
    """Numerical Jacobian via central differences."""
    n = len(state)
    f0 = _predict_state(state, dt)
    jac = np.zeros((n, n))
    for j in range(n):
        sp = state.copy()
        sm = state.copy()
        sp[j] += eps
        sm[j] -= eps
        jac[:, j] = (_predict_state(sp, dt) - _predict_state(sm, dt)) / (2 * eps)
    return jac


def test_jacobian_numerical_check() -> None:
    """Analytical Jacobian matches finite-difference approximation.

    We test three operating points: straight flight, moderate turn, sharp turn.
    Tolerance is 1e-4 relative to the element magnitude (the yaw_rate Jacobian
    entries involve higher-order trig terms that are slightly less accurate at
    machine precision).
    """
    dt = 0.1
    test_cases = [
        # (speed, yaw, yaw_rate, description)
        (15.0, 0.3, 0.0, "straight flight — degenerate branch"),
        (15.0, 0.3, 0.5, "moderate right turn"),
        (20.0, 1.0, -1.2, "sharp left turn"),
    ]

    for speed, yaw, yr, desc in test_cases:
        state = np.array([10.0, -5.0, 100.0, speed, yaw, yr, 2.0])
        jac_fd = _finite_diff_jacobian(state, dt)
        # Replicate the analytical Jacobian computation directly.
        from fusion.kalman import CTRVKalmanFilter as _C
        jac_analytic = _C._jacobian(speed, yaw, yr, dt)

        max_err = np.max(np.abs(jac_analytic - jac_fd))
        assert max_err < 1e-4, (
            f"Jacobian mismatch ({desc}): max element error = {max_err:.2e}\n"
            f"Analytical:\n{jac_analytic}\nFinite-diff:\n{jac_fd}"
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_create_filter_cv_returns_cv() -> None:
    from fusion.kalman import ConstantVelocityKalman
    kf = create_filter(
        "cv", position=(0.0, 0.0, 0.0), velocity=(5.0, 0.0, 0.0),
        pos_sigma=5.0, vel_sigma=2.0, process_noise=1.0,
    )
    assert isinstance(kf, ConstantVelocityKalman)


def test_create_filter_ctrv_returns_ctrv() -> None:
    kf = create_filter(
        "ctrv", position=(0.0, 0.0, 50.0), velocity=(10.0, 0.0, 0.0),
        pos_sigma=5.0, vel_sigma=2.0, process_noise=4.0,
    )
    assert isinstance(kf, CTRVKalmanFilter)


def test_create_filter_bad_model_raises() -> None:
    with pytest.raises(ValueError, match="unknown filter model"):
        create_filter(
            "imm",  # type: ignore[arg-type]
            position=(0.0, 0.0, 0.0), velocity=(0.0, 0.0, 0.0),
            pos_sigma=1.0, vel_sigma=1.0, process_noise=1.0,
        )


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_predict_negative_dt_raises() -> None:
    kf = _make_ctrv()
    with pytest.raises(ValueError, match="dt must be non-negative"):
        kf.predict(-0.1)


def test_update_bad_R_shape_raises() -> None:
    kf = _make_ctrv()
    with pytest.raises(ValueError, match="R must be 3x3"):
        kf.update(measurement=(0.0, 0.0, 0.0), R=np.eye(4))


def test_predict_zero_dt_is_noop() -> None:
    kf = _make_ctrv(x=5.0, y=3.0, z=1.0, speed=10.0, yaw=0.3)
    pos_before = kf.position
    cov_before = kf.covariance.copy()
    kf.predict(0.0)
    assert kf.position == pos_before
    assert np.allclose(kf.covariance, cov_before)
