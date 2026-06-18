"""
Per-sensor, range-dependent measurement noise models for the fusion pipeline.

Each sensor kind has physically motivated noise characteristics:
  - RADAR: good range accuracy, cross-range error grows with distance
  - EOIR: excellent angular precision at short range, degrades with distance
  - RF_PASSIVE: bearing-only, large position uncertainty

compute_R() builds a 3x3 measurement noise covariance matrix R in ENU frame
for a specific sensor-target geometry. This replaces the flat eye(3)*sigma^2
used in the Kalman filter with a geometry-aware, range-dependent model.

Parameters match the phenomenology in sim_source.py _KIND_PROFILES.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, sin, sqrt

import numpy as np

from csontology import Vec3


@dataclass(frozen=True)
class SensorNoiseModel:
    """Measurement noise model for a specific sensor type."""

    sensor_kind: str
    base_range_sigma_m: float
    range_sigma_growth: float
    base_bearing_sigma_rad: float
    elevation_sigma_rad: float


def compute_R(
    model: SensorNoiseModel,
    sensor_position: Vec3,
    target_position: Vec3,
) -> np.ndarray:
    """Compute a 3x3 measurement noise covariance matrix R in ENU frame.

    Steps:
      1. Compute range from sensor to target.
      2. Compute range sigma = base + growth * range_km.
      3. Compute cross-range sigma = bearing_sigma_rad * range.
      4. Build R_sensor in sensor-aligned frame:
           [along-range, cross-range, elevation].
      5. Rotate R_sensor to ENU using the horizontal bearing angle.

    The returned matrix is symmetric and positive definite.
    """
    dx = target_position[0] - sensor_position[0]
    dy = target_position[1] - sensor_position[1]
    dz = target_position[2] - sensor_position[2]

    range_m = sqrt(dx * dx + dy * dy + dz * dz)
    range_m = max(range_m, 1.0)
    range_km = range_m / 1000.0

    range_sigma = model.base_range_sigma_m + model.range_sigma_growth * range_km
    cross_range_sigma = model.base_bearing_sigma_rad * range_m
    elev_sigma = model.elevation_sigma_rad * range_m

    r_sensor = np.diag(np.array([
        range_sigma ** 2,
        cross_range_sigma ** 2,
        elev_sigma ** 2,
    ], dtype=np.float64))

    bearing = atan2(dx, dy)
    cb = cos(bearing)
    sb = sin(bearing)

    rot = np.array([
        [cb, -sb, 0.0],
        [sb,  cb, 0.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)

    r_enu: np.ndarray = rot @ r_sensor @ rot.T
    return r_enu


# Default models aligned with _KIND_PROFILES in sim_source.py.
# base_range_sigma_m matches pos_noise_m; range_sigma_growth reflects the
# range-noise scaling; bearing sigmas reflect the cross_range_factor ratios.

RADAR_MODEL = SensorNoiseModel(
    sensor_kind="RADAR",
    base_range_sigma_m=5.0,
    range_sigma_growth=2.0,
    base_bearing_sigma_rad=0.02,
    elevation_sigma_rad=0.05,
)

EOIR_MODEL = SensorNoiseModel(
    sensor_kind="EOIR",
    base_range_sigma_m=10.0,
    range_sigma_growth=5.0,
    base_bearing_sigma_rad=0.005,
    elevation_sigma_rad=0.01,
)

RF_PASSIVE_MODEL = SensorNoiseModel(
    sensor_kind="RF_PASSIVE",
    base_range_sigma_m=50.0,
    range_sigma_growth=20.0,
    base_bearing_sigma_rad=0.1,
    elevation_sigma_rad=0.2,
)

_MODEL_MAP: dict[str, SensorNoiseModel] = {
    "RADAR": RADAR_MODEL,
    "EOIR": EOIR_MODEL,
    "RF_PASSIVE": RF_PASSIVE_MODEL,
}


def get_model(sensor_kind: str) -> SensorNoiseModel:
    """Look up a SensorNoiseModel by kind string.

    Raises KeyError for unknown kinds. Callers should catch this and fall back
    to a conservative default rather than crashing the fusion pipeline.
    """
    try:
        return _MODEL_MAP[sensor_kind]
    except KeyError:
        raise KeyError(
            f"Unknown sensor kind {sensor_kind!r}. "
            f"Valid kinds: {list(_MODEL_MAP)}"
        )
