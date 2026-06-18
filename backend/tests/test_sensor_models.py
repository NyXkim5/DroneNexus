"""
Tests for backend/fusion/sensor_models.py.

Each test validates one physical property of the noise model. All positions
are in ENU meters with the sensor at the origin unless otherwise stated.
"""
from __future__ import annotations

import numpy as np
import pytest

from fusion.sensor_models import (
    EOIR_MODEL,
    RADAR_MODEL,
    RF_PASSIVE_MODEL,
    SensorNoiseModel,
    compute_R,
    get_model,
)

SENSOR_ORIGIN: tuple[float, float, float] = (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_symmetric(m: np.ndarray, tol: float = 1e-10) -> bool:
    return bool(np.allclose(m, m.T, atol=tol))


def _is_positive_definite(m: np.ndarray) -> bool:
    try:
        np.linalg.cholesky(m)
        return True
    except np.linalg.LinAlgError:
        return False


# ---------------------------------------------------------------------------
# test_radar_R_at_short_range
# ---------------------------------------------------------------------------

def test_radar_R_at_short_range() -> None:
    """At 100 m the diagonal of R should stay close to base noise values."""
    target = (100.0, 0.0, 0.0)
    r = compute_R(RADAR_MODEL, SENSOR_ORIGIN, target)

    assert r.shape == (3, 3)
    # Range sigma at 100 m: 5.0 + 2.0 * 0.1 = 5.2  =>  var = 27.04
    # Cross-range sigma: 0.02 * 100 = 2.0  =>  var = 4.0
    # At short range the diagonals should be small, well under 1000 m^2.
    assert r[0, 0] < 1000.0
    assert r[1, 1] < 1000.0
    assert r[2, 2] < 1000.0
    # All diagonal entries must be positive.
    assert r[0, 0] > 0.0
    assert r[1, 1] > 0.0
    assert r[2, 2] > 0.0


# ---------------------------------------------------------------------------
# test_radar_R_grows_with_range
# ---------------------------------------------------------------------------

def test_radar_R_grows_with_range() -> None:
    """R diagonal at 5000 m must be significantly larger than at 100 m."""
    target_near = (100.0, 0.0, 0.0)
    target_far = (5000.0, 0.0, 0.0)

    r_near = compute_R(RADAR_MODEL, SENSOR_ORIGIN, target_near)
    r_far = compute_R(RADAR_MODEL, SENSOR_ORIGIN, target_far)

    trace_near = np.trace(r_near)
    trace_far = np.trace(r_far)

    assert trace_far > trace_near * 5, (
        f"Expected far-range trace ({trace_far:.1f}) to be >5x near-range "
        f"trace ({trace_near:.1f})"
    )


# ---------------------------------------------------------------------------
# test_eoir_cross_range_small
# ---------------------------------------------------------------------------

def test_eoir_cross_range_small() -> None:
    """EO/IR has small bearing sigma so cross-range variance is small at 500 m."""
    target = (500.0, 0.0, 0.0)
    r_eoir = compute_R(EOIR_MODEL, SENSOR_ORIGIN, target)
    r_radar = compute_R(RADAR_MODEL, SENSOR_ORIGIN, target)

    # EO/IR bearing sigma (0.005) is 4x tighter than radar (0.02).
    # The cross-range variance should be noticeably smaller for EO/IR.
    assert np.trace(r_eoir) < np.trace(r_radar), (
        "EO/IR should have smaller total variance than radar at short range"
    )


# ---------------------------------------------------------------------------
# test_rf_passive_large_uncertainty
# ---------------------------------------------------------------------------

def test_rf_passive_large_uncertainty() -> None:
    """RF passive R diagonal values must be large relative to RADAR."""
    target = (1000.0, 0.0, 0.0)
    r_rf = compute_R(RF_PASSIVE_MODEL, SENSOR_ORIGIN, target)
    r_radar = compute_R(RADAR_MODEL, SENSOR_ORIGIN, target)

    assert np.trace(r_rf) > np.trace(r_radar) * 5, (
        "RF passive total variance should be much larger than radar at 1 km"
    )
    # All diagonals must be large absolute values too.
    for i in range(3):
        assert r_rf[i, i] > 100.0, f"r_rf[{i},{i}] = {r_rf[i,i]:.2f} expected > 100"


# ---------------------------------------------------------------------------
# test_R_is_symmetric_positive_definite
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model", [RADAR_MODEL, EOIR_MODEL, RF_PASSIVE_MODEL])
@pytest.mark.parametrize("target", [
    (100.0, 0.0, 0.0),
    (0.0, 2000.0, 50.0),
    (1500.0, 1500.0, 200.0),
])
def test_R_is_symmetric_positive_definite(
    model: SensorNoiseModel,
    target: tuple[float, float, float],
) -> None:
    """R must be symmetric and positive definite for all models and geometries."""
    r = compute_R(model, SENSOR_ORIGIN, target)
    assert _is_symmetric(r), f"R is not symmetric for {model.sensor_kind} at {target}"
    assert _is_positive_definite(r), (
        f"R is not positive definite for {model.sensor_kind} at {target}"
    )


# ---------------------------------------------------------------------------
# test_R_rotated_to_enu
# ---------------------------------------------------------------------------

def test_R_rotated_to_enu() -> None:
    """R at bearing=0 (north) differs from R at bearing=90 (east).

    When the target is due north the along-range axis aligns with ENU-Y.
    When due east it aligns with ENU-X. The off-diagonal structure must differ,
    proving the rotation is applied and not just a diagonal matrix.
    """
    range_m = 2000.0
    target_north = (0.0, range_m, 0.0)
    target_east = (range_m, 0.0, 0.0)

    r_north = compute_R(RADAR_MODEL, SENSOR_ORIGIN, target_north)
    r_east = compute_R(RADAR_MODEL, SENSOR_ORIGIN, target_east)

    # The (0,0) and (1,1) diagonal entries swap roles between the two bearings.
    assert not np.allclose(r_north, r_east, atol=1.0), (
        "R at bearing=0 and bearing=90 must differ — rotation is not applied"
    )
    # r_north: range axis is Y, so r[1,1] > r[0,0]
    assert r_north[1, 1] > r_north[0, 0], (
        "Due-north target: R[1,1] (along Y) should exceed R[0,0]"
    )
    # r_east: range axis is X, so r[0,0] > r[1,1]
    assert r_east[0, 0] > r_east[1, 1], (
        "Due-east target: R[0,0] (along X) should exceed R[1,1]"
    )


# ---------------------------------------------------------------------------
# test_get_model_returns_correct
# ---------------------------------------------------------------------------

def test_get_model_returns_correct() -> None:
    """get_model returns the right singleton for each valid kind string."""
    assert get_model("RADAR") is RADAR_MODEL
    assert get_model("EOIR") is EOIR_MODEL
    assert get_model("RF_PASSIVE") is RF_PASSIVE_MODEL


def test_get_model_raises_for_unknown() -> None:
    """get_model raises KeyError for an unrecognised sensor kind."""
    with pytest.raises(KeyError, match="LIDAR"):
        get_model("LIDAR")
