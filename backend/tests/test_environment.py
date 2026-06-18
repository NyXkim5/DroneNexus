"""
Tests for sensors/environment.py — terrain LOS masking and weather effects.

Tests are deterministic: TerrainProfile.with_hills uses a fixed seed,
WeatherCondition presets use fixed values, and all assertions use tolerances
that account for interpolation and floating-point arithmetic.

Run from the backend directory:
    python3 -m pytest tests/test_environment.py -v
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sensors.environment import (
    EnvironmentModel,
    TerrainProfile,
    WeatherCondition,
    _db_loss_to_linear,
    _distance,
)


# ---------------------------------------------------------------------------
# TerrainProfile tests
# ---------------------------------------------------------------------------

def test_flat_terrain_always_los() -> None:
    """Flat terrain must produce True for every pair of points."""
    terrain = TerrainProfile.flat(size_m=5000.0, resolution_m=100.0)

    pairs = [
        ((0.0, 0.0, 10.0), (1000.0, 1000.0, 10.0)),
        ((-2000.0, 0.0, 5.0), (2000.0, 0.0, 5.0)),
        ((0.0, 0.0, 1.0), (0.0, 2000.0, 1.0)),
        ((500.0, 500.0, 50.0), (-500.0, -500.0, 50.0)),
    ]
    for from_pos, to_pos in pairs:
        assert terrain.has_line_of_sight(from_pos, to_pos), (
            f"Expected LOS between {from_pos} and {to_pos} on flat terrain"
        )


def test_hill_blocks_los() -> None:
    """A tall hill between sensor and target must block LOS.

    Build a single tall, narrow hill exactly between the sensor at (-3000, 0, 5)
    and the target at (3000, 0, 5). The hill centre is at (0, 0) with height
    500 m. The sensor and target are at 5 m altitude so the hill easily blocks.
    """
    # Construct a custom grid with one guaranteed tall hill in the centre.
    size_m = 8000.0
    resolution_m = 50.0
    n_cells = int(math.ceil(size_m / resolution_m))
    grid = np.zeros((n_cells, n_cells), dtype=float)

    # Grid centre cell.
    centre_r = n_cells // 2
    centre_c = n_cells // 2
    sigma_cells = 3.0
    xs = np.arange(n_cells, dtype=float)
    ys = np.arange(n_cells, dtype=float)
    xx, yy = np.meshgrid(xs, ys)
    dist2 = (xx - centre_c) ** 2 + (yy - centre_r) ** 2
    grid += 500.0 * np.exp(-dist2 / (2.0 * sigma_cells ** 2))

    half = size_m / 2.0
    terrain = TerrainProfile(
        elevation_grid=grid,
        resolution_m=resolution_m,
        origin=(-half, -half, 0.0),
    )

    # Sensor and target at 5 m altitude, hill at 500 m between them.
    sensor = (-3000.0, 0.0, 5.0)
    target = (3000.0, 0.0, 5.0)
    assert not terrain.has_line_of_sight(sensor, target), (
        "Expected hill to block LOS between sensor and target"
    )


def test_elevation_interpolation() -> None:
    """elevation_at must interpolate correctly on a known grid."""
    # 2x2 grid: elevation 0 at SW, 100 at SE, 0 at NW, 0 at NE.
    # Bilinear interpolation at the centre of the SE cell should be near 100.
    grid = np.array([[0.0, 100.0], [0.0, 0.0]])
    terrain = TerrainProfile(
        elevation_grid=grid,
        resolution_m=100.0,
        origin=(0.0, 0.0, 0.0),
    )
    # Exactly at column=1, row=0 (SE corner, ENU x=100, y=0).
    assert terrain.elevation_at(100.0, 0.0) == pytest.approx(100.0, abs=1e-6)
    # At the SW corner.
    assert terrain.elevation_at(0.0, 0.0) == pytest.approx(0.0, abs=1e-6)
    # At centre of grid (col=0.5, row=0.5) bilinear average of all four = 25.
    assert terrain.elevation_at(50.0, 50.0) == pytest.approx(25.0, abs=1e-6)


def test_terrain_with_hills_has_variation() -> None:
    """Generated hilly terrain must not be flat."""
    terrain = TerrainProfile.with_hills(
        size_m=5000.0, resolution_m=100.0, n_hills=5, max_height_m=200.0, seed=42
    )
    grid = terrain.elevation_grid
    assert grid.max() > 10.0, "Hill terrain should have positive elevations"
    assert grid.min() >= 0.0, "Elevations should not go negative"
    # Standard deviation must be non-trivial to confirm spatial variation.
    assert grid.std() > 1.0, "Hill terrain should have variation across the grid"


def test_los_high_altitude_clears_hill() -> None:
    """A high-flying target should clear a hill that would block a low target."""
    size_m = 8000.0
    resolution_m = 50.0
    n_cells = int(math.ceil(size_m / resolution_m))
    grid = np.zeros((n_cells, n_cells), dtype=float)
    centre_r = n_cells // 2
    centre_c = n_cells // 2
    sigma_cells = 3.0
    xs = np.arange(n_cells, dtype=float)
    ys = np.arange(n_cells, dtype=float)
    xx, yy = np.meshgrid(xs, ys)
    dist2 = (xx - centre_c) ** 2 + (yy - centre_r) ** 2
    grid += 300.0 * np.exp(-dist2 / (2.0 * sigma_cells ** 2))

    half = size_m / 2.0
    terrain = TerrainProfile(
        elevation_grid=grid, resolution_m=resolution_m, origin=(-half, -half, 0.0)
    )

    sensor = (-3000.0, 0.0, 500.0)
    target = (3000.0, 0.0, 500.0)
    assert terrain.has_line_of_sight(sensor, target), (
        "High-altitude path should clear the hill"
    )


# ---------------------------------------------------------------------------
# WeatherCondition tests
# ---------------------------------------------------------------------------

def test_clear_weather_fields() -> None:
    """Clear preset must have no precipitation and good visibility."""
    w = WeatherCondition.clear()
    assert w.precipitation_mm_hr == pytest.approx(0.0)
    assert w.visibility_m >= 5000.0


def test_rain_weather_has_precipitation() -> None:
    """Rain preset must have positive precipitation rate."""
    w = WeatherCondition.rain(rate_mm_hr=20.0)
    assert w.precipitation_mm_hr == pytest.approx(20.0)
    assert w.visibility_m < 10000.0


def test_fog_weather_low_visibility() -> None:
    """Fog preset must have very low visibility."""
    w = WeatherCondition.fog(visibility_m=200.0)
    assert w.visibility_m == pytest.approx(200.0)
    assert w.precipitation_mm_hr == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# EnvironmentModel — clear weather baseline
# ---------------------------------------------------------------------------

def test_clear_weather_no_modifier() -> None:
    """Clear weather on flat terrain must not significantly degrade any sensor.

    RADAR and RF_PASSIVE are unaffected by visibility so they return exactly 1.0.
    EOIR applies Koschmieder's law even in clear weather, so at 500 m range with
    10 km visibility the factor is exp(-3 * 500 / 10000) ~ 0.86. The test asserts
    it stays above 0.8 — well within useful detection range — and that all sensors
    return positive values.
    """
    env = EnvironmentModel(
        terrain=TerrainProfile.flat(),
        weather=WeatherCondition.clear(),
    )
    sensor_pos: tuple[float, float, float] = (0.0, 0.0, 10.0)
    target_pos: tuple[float, float, float] = (500.0, 0.0, 50.0)

    mod_radar = env.detection_probability_modifier("RADAR", sensor_pos, target_pos)
    mod_rf = env.detection_probability_modifier("RF_PASSIVE", sensor_pos, target_pos)
    mod_eoir = env.detection_probability_modifier("EOIR", sensor_pos, target_pos)

    assert mod_radar == pytest.approx(1.0, abs=1e-6), (
        f"RADAR: expected exactly 1.0 in clear weather with no rain, got {mod_radar}"
    )
    assert mod_rf == pytest.approx(1.0, abs=1e-6), (
        f"RF_PASSIVE: expected exactly 1.0 in clear weather, got {mod_rf}"
    )
    # EOIR visibility factor is always < 1 at non-zero range; in clear weather
    # (10 km visibility) at 500 m it should remain well above 0.8.
    assert mod_eoir > 0.8, (
        f"EOIR: expected > 0.8 in clear weather at 500 m, got {mod_eoir}"
    )
    assert mod_eoir <= 1.0, f"EOIR modifier must not exceed 1.0, got {mod_eoir}"


# ---------------------------------------------------------------------------
# EnvironmentModel — rain effects
# ---------------------------------------------------------------------------

def test_rain_attenuates_radar() -> None:
    """Heavy rain must reduce the radar modifier below 1.0."""
    env = EnvironmentModel(
        terrain=TerrainProfile.flat(),
        weather=WeatherCondition.rain(rate_mm_hr=50.0),
    )
    sensor_pos: tuple[float, float, float] = (0.0, 0.0, 10.0)
    target_pos: tuple[float, float, float] = (3000.0, 0.0, 50.0)

    mod = env.detection_probability_modifier("RADAR", sensor_pos, target_pos)
    assert mod < 1.0, f"Expected rain to attenuate radar, got modifier {mod}"
    assert mod > 0.0, "Rain attenuation should not fully block radar"


def test_rf_unaffected_by_rain() -> None:
    """RF passive must return 1.0 regardless of precipitation on flat terrain."""
    env = EnvironmentModel(
        terrain=TerrainProfile.flat(),
        weather=WeatherCondition.rain(rate_mm_hr=100.0),
    )
    sensor_pos: tuple[float, float, float] = (0.0, 0.0, 10.0)
    target_pos: tuple[float, float, float] = (2000.0, 0.0, 50.0)

    mod = env.detection_probability_modifier("RF_PASSIVE", sensor_pos, target_pos)
    assert mod == pytest.approx(1.0, abs=1e-6), (
        f"RF passive should be unaffected by rain, got {mod}"
    )


def test_rain_attenuation_increases_with_rate() -> None:
    """Higher rain rate must produce higher attenuation (lower modifier)."""
    flat = TerrainProfile.flat()
    sensor_pos: tuple[float, float, float] = (0.0, 0.0, 10.0)
    target_pos: tuple[float, float, float] = (5000.0, 0.0, 50.0)

    env_light = EnvironmentModel(flat, WeatherCondition.rain(rate_mm_hr=5.0))
    env_heavy = EnvironmentModel(flat, WeatherCondition.rain(rate_mm_hr=50.0))

    mod_light = env_light.detection_probability_modifier("RADAR", sensor_pos, target_pos)
    mod_heavy = env_heavy.detection_probability_modifier("RADAR", sensor_pos, target_pos)

    assert mod_heavy < mod_light, (
        "Heavy rain should attenuate more than light rain"
    )


# ---------------------------------------------------------------------------
# EnvironmentModel — EO/IR effects
# ---------------------------------------------------------------------------

def test_fog_reduces_eoir() -> None:
    """Dense fog must significantly reduce EO/IR detection modifier."""
    env_clear = EnvironmentModel(
        terrain=TerrainProfile.flat(),
        weather=WeatherCondition.clear(),
    )
    env_fog = EnvironmentModel(
        terrain=TerrainProfile.flat(),
        weather=WeatherCondition.fog(visibility_m=300.0),
    )
    sensor_pos: tuple[float, float, float] = (0.0, 0.0, 10.0)
    target_pos: tuple[float, float, float] = (1000.0, 0.0, 50.0)

    mod_clear = env_clear.detection_probability_modifier("EOIR", sensor_pos, target_pos)
    mod_fog = env_fog.detection_probability_modifier("EOIR", sensor_pos, target_pos)

    assert mod_fog < mod_clear, (
        f"Fog should reduce EOIR modifier: clear={mod_clear:.4f} fog={mod_fog:.4f}"
    )
    assert mod_fog < 0.1, (
        f"At range 1000 m in 300 m visibility, EOIR modifier should be very low, got {mod_fog:.4f}"
    )


def test_cloud_ceiling_blocks_eoir() -> None:
    """Target above the cloud ceiling must be invisible to ground-level EOIR."""
    env = EnvironmentModel(
        terrain=TerrainProfile.flat(),
        weather=WeatherCondition(
            visibility_m=10000.0,
            precipitation_mm_hr=0.0,
            cloud_base_m=500.0,
        ),
    )
    sensor_pos: tuple[float, float, float] = (0.0, 0.0, 10.0)   # below clouds
    target_pos: tuple[float, float, float] = (200.0, 0.0, 800.0)  # above clouds

    mod = env.detection_probability_modifier("EOIR", sensor_pos, target_pos)
    assert mod == pytest.approx(0.0, abs=1e-6), (
        f"Target above cloud ceiling should give 0 EOIR modifier, got {mod}"
    )


def test_both_above_clouds_not_blocked() -> None:
    """Two airborne platforms both above clouds must retain LOS for EOIR."""
    env = EnvironmentModel(
        terrain=TerrainProfile.flat(),
        weather=WeatherCondition(
            visibility_m=10000.0,
            precipitation_mm_hr=0.0,
            cloud_base_m=500.0,
        ),
    )
    sensor_pos: tuple[float, float, float] = (0.0, 0.0, 600.0)   # above clouds
    target_pos: tuple[float, float, float] = (200.0, 0.0, 700.0)  # above clouds

    mod = env.detection_probability_modifier("EOIR", sensor_pos, target_pos)
    assert mod > 0.0, "Both platforms above cloud ceiling should not be blocked"


# ---------------------------------------------------------------------------
# EnvironmentModel — terrain LOS
# ---------------------------------------------------------------------------

def test_terrain_blocks_all_sensor_kinds() -> None:
    """A blocking hill must return 0.0 for every sensor kind."""
    size_m = 8000.0
    resolution_m = 50.0
    n_cells = int(math.ceil(size_m / resolution_m))
    grid = np.zeros((n_cells, n_cells), dtype=float)
    centre_r = n_cells // 2
    centre_c = n_cells // 2
    sigma_cells = 3.0
    xs = np.arange(n_cells, dtype=float)
    ys = np.arange(n_cells, dtype=float)
    xx, yy = np.meshgrid(xs, ys)
    dist2 = (xx - centre_c) ** 2 + (yy - centre_r) ** 2
    grid += 500.0 * np.exp(-dist2 / (2.0 * sigma_cells ** 2))

    half = size_m / 2.0
    terrain = TerrainProfile(
        elevation_grid=grid, resolution_m=resolution_m, origin=(-half, -half, 0.0)
    )
    env = EnvironmentModel(terrain=terrain, weather=WeatherCondition.clear())

    sensor_pos: tuple[float, float, float] = (-3000.0, 0.0, 5.0)
    target_pos: tuple[float, float, float] = (3000.0, 0.0, 5.0)

    for kind in ("RADAR", "EOIR", "RF_PASSIVE"):
        mod = env.detection_probability_modifier(kind, sensor_pos, target_pos)
        assert mod == pytest.approx(0.0, abs=1e-6), (
            f"{kind}: expected 0.0 when terrain blocks LOS, got {mod}"
        )


def test_combined_terrain_and_weather() -> None:
    """Clear LOS with rain must produce a modifier strictly between 0 and 1 for RADAR."""
    env = EnvironmentModel(
        terrain=TerrainProfile.flat(),
        weather=WeatherCondition.rain(rate_mm_hr=25.0),
    )
    sensor_pos: tuple[float, float, float] = (0.0, 0.0, 10.0)
    target_pos: tuple[float, float, float] = (4000.0, 0.0, 50.0)

    mod = env.detection_probability_modifier("RADAR", sensor_pos, target_pos)
    assert 0.0 < mod < 1.0, (
        f"Combined modifier should be between 0 and 1, got {mod}"
    )


# ---------------------------------------------------------------------------
# rain_attenuation_db_per_km unit tests
# ---------------------------------------------------------------------------

def test_no_rain_zero_attenuation() -> None:
    """No precipitation must give 0 dB/km attenuation."""
    env = EnvironmentModel(weather=WeatherCondition.clear())
    assert env.rain_attenuation_db_per_km() == pytest.approx(0.0)


def test_rain_attenuation_positive() -> None:
    """Non-zero rain must give positive dB/km attenuation."""
    env = EnvironmentModel(weather=WeatherCondition.rain(rate_mm_hr=10.0))
    atten = env.rain_attenuation_db_per_km()
    assert atten > 0.0, f"Expected positive attenuation, got {atten}"


# ---------------------------------------------------------------------------
# visibility_factor unit tests
# ---------------------------------------------------------------------------

def test_visibility_factor_zero_range() -> None:
    """At range 0 the visibility factor must be 1.0."""
    env = EnvironmentModel(weather=WeatherCondition.fog(visibility_m=500.0))
    assert env.visibility_factor(0.0) == pytest.approx(1.0, abs=1e-6)


def test_visibility_factor_at_visibility_range() -> None:
    """At range == visibility_m the factor must be exp(-3) ~0.05."""
    vis = 1000.0
    env = EnvironmentModel(weather=WeatherCondition(visibility_m=vis))
    factor = env.visibility_factor(vis)
    assert factor == pytest.approx(math.exp(-3.0), abs=1e-6)


def test_visibility_factor_decreases_with_range() -> None:
    """Visibility factor must decrease monotonically with range."""
    env = EnvironmentModel(weather=WeatherCondition.fog(visibility_m=800.0))
    ranges = [100.0, 500.0, 1000.0, 2000.0]
    factors = [env.visibility_factor(r) for r in ranges]
    for i in range(len(factors) - 1):
        assert factors[i] > factors[i + 1], (
            f"Factor at range {ranges[i]} should exceed factor at {ranges[i+1]}"
        )


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

def test_db_loss_to_linear_zero_db() -> None:
    """0 dB loss must return 1.0."""
    assert _db_loss_to_linear(0.0) == pytest.approx(1.0)


def test_db_loss_to_linear_ten_db() -> None:
    """10 dB loss must return 0.1."""
    assert _db_loss_to_linear(10.0) == pytest.approx(0.1, abs=1e-6)


def test_db_loss_to_linear_negative() -> None:
    """Negative dB (gain) must be clamped to 1.0."""
    assert _db_loss_to_linear(-5.0) == pytest.approx(1.0)


def test_distance_helper() -> None:
    """_distance must compute correct 3D Euclidean distance."""
    a: tuple[float, float, float] = (0.0, 0.0, 0.0)
    b: tuple[float, float, float] = (3.0, 4.0, 0.0)
    assert _distance(a, b) == pytest.approx(5.0)

    c: tuple[float, float, float] = (0.0, 0.0, 0.0)
    d: tuple[float, float, float] = (1.0, 1.0, 1.0)
    assert _distance(c, d) == pytest.approx(math.sqrt(3.0))
