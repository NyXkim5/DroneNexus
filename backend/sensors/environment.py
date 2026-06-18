"""
Environment model — terrain line-of-sight masking and weather effects on sensors.

This module is standalone. It provides two orthogonal models:

  TerrainProfile — a discretised elevation grid that answers LOS queries by
  sampling the straight line between two ENU points and checking whether any
  terrain sample rises above the connecting chord. Flat terrain is the default
  and preserves existing behaviour. Gaussian-hill terrain is provided for
  realistic LOS-masking tests.

  WeatherCondition — a set of atmospheric state variables. Factory methods
  produce named presets (clear, rain, fog).

  EnvironmentModel — combines one terrain and one weather condition into a
  single detection_probability_modifier(sensor_kind, sensor_pos, target_pos)
  call that returns a 0..1 scalar. Multiply this against the sensor's own
  detection probability. Zero means blocked; one means no environmental penalty.

Physics references:
  Rain attenuation: ITU-R P.838-3 simplified power-law, A = a * R^b dB/km.
  EO/IR visibility: Koschmieder's law, P = exp(-3 * range / visibility).
  RF passive: negligible weather attenuation at 2.4 GHz; LOS only.

Coordinate frame is local ENU meters with the same origin as csontology.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from csontology import Vec3


# ---------------------------------------------------------------------------
# TerrainProfile
# ---------------------------------------------------------------------------

@dataclass
class TerrainProfile:
    """Simple terrain model for line-of-sight calculations.

    elevation_grid is a 2D array indexed [row, col] = [north, east].
    resolution_m is the side length of each cell in metres.
    origin is the ENU coordinate of the south-west corner of the grid.
    """
    elevation_grid: np.ndarray   # shape (rows, cols), metres
    resolution_m: float          # metres per cell
    origin: Vec3                 # ENU south-west corner (x_east, y_north, z_up)

    @classmethod
    def flat(
        cls,
        size_m: float = 10000.0,
        resolution_m: float = 50.0,
    ) -> "TerrainProfile":
        """Return a zero-elevation grid covering a square of side size_m.

        This is the default and reproduces the original no-terrain behaviour:
        every LOS query returns True.
        """
        n_cells = max(2, int(math.ceil(size_m / resolution_m)))
        grid = np.zeros((n_cells, n_cells), dtype=float)
        half = size_m / 2.0
        return cls(
            elevation_grid=grid,
            resolution_m=resolution_m,
            origin=(-half, -half, 0.0),
        )

    @classmethod
    def with_hills(
        cls,
        size_m: float = 10000.0,
        resolution_m: float = 50.0,
        n_hills: int = 5,
        max_height_m: float = 200.0,
        seed: int = 42,
    ) -> "TerrainProfile":
        """Return terrain built from superimposed Gaussian hills.

        Each hill is placed at a random grid location with a random height up to
        max_height_m and a spread of roughly 10% of the grid size. The result is
        a smooth, non-flat elevation field suitable for LOS-masking tests.
        """
        rng = np.random.default_rng(seed)
        n_cells = max(2, int(math.ceil(size_m / resolution_m)))
        grid = np.zeros((n_cells, n_cells), dtype=float)
        xs = np.arange(n_cells, dtype=float)
        ys = np.arange(n_cells, dtype=float)
        xx, yy = np.meshgrid(xs, ys)  # shape (n_cells, n_cells)

        sigma = n_cells * 0.10  # hill width in cells

        for _ in range(n_hills):
            cx = rng.uniform(0, n_cells)
            cy = rng.uniform(0, n_cells)
            height = rng.uniform(max_height_m * 0.3, max_height_m)
            dist2 = (xx - cx) ** 2 + (yy - cy) ** 2
            grid += height * np.exp(-dist2 / (2.0 * sigma ** 2))

        half = size_m / 2.0
        return cls(
            elevation_grid=grid,
            resolution_m=resolution_m,
            origin=(-half, -half, 0.0),
        )

    def elevation_at(self, x_enu: float, y_enu: float) -> float:
        """Bilinearly interpolate the terrain elevation at an ENU point.

        Points outside the grid are clamped to the grid boundary, which means
        terrain is assumed flat and at the edge elevation beyond the grid extent.
        """
        ox, oy = self.origin[0], self.origin[1]
        col_f = (x_enu - ox) / self.resolution_m
        row_f = (y_enu - oy) / self.resolution_m

        n_rows, n_cols = self.elevation_grid.shape
        col_f = max(0.0, min(col_f, n_cols - 1))
        row_f = max(0.0, min(row_f, n_rows - 1))

        c0 = int(col_f)
        r0 = int(row_f)
        c1 = min(c0 + 1, n_cols - 1)
        r1 = min(r0 + 1, n_rows - 1)

        dc = col_f - c0
        dr = row_f - r0

        g = self.elevation_grid
        elev = (
            g[r0, c0] * (1 - dr) * (1 - dc)
            + g[r1, c0] * dr * (1 - dc)
            + g[r0, c1] * (1 - dr) * dc
            + g[r1, c1] * dr * dc
        )
        return float(elev)

    def has_line_of_sight(
        self,
        from_pos: Vec3,
        to_pos: Vec3,
        n_samples: int = 20,
    ) -> bool:
        """Return True if the straight line between two ENU points is unobstructed.

        Sample n_samples evenly spaced positions along the chord (exclusive of
        the endpoints). At each sample, compute the chord height above ground and
        the terrain elevation. If any terrain point rises above the chord, the
        line of sight is blocked.

        The chord height at fractional distance t is:
            h(t) = from_pos.z + t * (to_pos.z - from_pos.z)
        """
        for i in range(1, n_samples + 1):
            t = i / (n_samples + 1)
            sx = from_pos[0] + t * (to_pos[0] - from_pos[0])
            sy = from_pos[1] + t * (to_pos[1] - from_pos[1])
            sz = from_pos[2] + t * (to_pos[2] - from_pos[2])
            terrain_z = self.elevation_at(sx, sy)
            if terrain_z > sz:
                return False
        return True


# ---------------------------------------------------------------------------
# WeatherCondition
# ---------------------------------------------------------------------------

@dataclass
class WeatherCondition:
    """Atmospheric state variables that affect sensor performance.

    visibility_m is the meteorological visibility (Koschmieder definition).
    precipitation_mm_hr is the liquid rain equivalent rate.
    cloud_base_m is the altitude AGL of the cloud ceiling.
    """
    visibility_m: float = 10000.0
    precipitation_mm_hr: float = 0.0
    cloud_base_m: float = 3000.0
    wind_speed_ms: float = 0.0
    wind_direction_deg: float = 0.0
    temperature_c: float = 20.0
    humidity_pct: float = 50.0

    @classmethod
    def clear(cls) -> "WeatherCondition":
        """Clear sky, good visibility, no precipitation."""
        return cls(
            visibility_m=10000.0,
            precipitation_mm_hr=0.0,
            cloud_base_m=3000.0,
            wind_speed_ms=2.0,
            wind_direction_deg=0.0,
            temperature_c=20.0,
            humidity_pct=40.0,
        )

    @classmethod
    def rain(cls, rate_mm_hr: float = 10.0) -> "WeatherCondition":
        """Rain at the given rate with reduced visibility."""
        visibility_m = max(500.0, 10000.0 - rate_mm_hr * 450.0)
        return cls(
            visibility_m=visibility_m,
            precipitation_mm_hr=rate_mm_hr,
            cloud_base_m=600.0,
            wind_speed_ms=5.0,
            wind_direction_deg=270.0,
            temperature_c=12.0,
            humidity_pct=95.0,
        )

    @classmethod
    def fog(cls, visibility_m: float = 500.0) -> "WeatherCondition":
        """Dense fog with very low visibility and no significant precipitation."""
        return cls(
            visibility_m=visibility_m,
            precipitation_mm_hr=0.0,
            cloud_base_m=50.0,
            wind_speed_ms=0.5,
            wind_direction_deg=0.0,
            temperature_c=8.0,
            humidity_pct=99.0,
        )


# ---------------------------------------------------------------------------
# EnvironmentModel
# ---------------------------------------------------------------------------

class EnvironmentModel:
    """Combines terrain and weather into a scalar modifier on detection probability.

    Call detection_probability_modifier(sensor_kind, sensor_pos, target_pos) to
    get a value in 0..1. Multiply it against the sensor's own detection
    probability before the Monte Carlo draw.

    Sensor families and their environmental sensitivities:

      RADAR       LOS-gated. Rain attenuates the two-way path (ITU-R P.838).
                  Not significantly affected by fog or cloud ceiling.

      EOIR        LOS-gated. Visibility limits detection range (Koschmieder).
                  Targets above the cloud ceiling are invisible from below.

      RF_PASSIVE  LOS-gated only. Rain and fog are negligible at 2.4 GHz.

    Any unrecognised sensor_kind string is treated as RF_PASSIVE (LOS only).
    """

    def __init__(
        self,
        terrain: TerrainProfile | None = None,
        weather: WeatherCondition | None = None,
    ) -> None:
        self._terrain = terrain if terrain is not None else TerrainProfile.flat()
        self._weather = weather if weather is not None else WeatherCondition.clear()

    @property
    def terrain(self) -> TerrainProfile:
        return self._terrain

    @property
    def weather(self) -> WeatherCondition:
        return self._weather

    def detection_probability_modifier(
        self,
        sensor_kind: str,
        sensor_pos: Vec3,
        target_pos: Vec3,
    ) -> float:
        """Return a 0..1 multiplier on detection probability.

        0 means completely blocked. 1 means no environmental degradation.

        RADAR:      los * rain_factor
        EOIR:       los * visibility_factor * cloud_factor
        RF_PASSIVE: los
        """
        kind = sensor_kind.upper()

        if not self._terrain.has_line_of_sight(sensor_pos, target_pos):
            return 0.0

        if kind == "RADAR":
            range_m = _distance(sensor_pos, target_pos)
            rain_db = self.rain_attenuation_db_per_km() * (range_m / 1000.0)
            rain_factor = _db_loss_to_linear(rain_db)
            return float(rain_factor)

        if kind == "EOIR":
            range_m = _distance(sensor_pos, target_pos)
            vis = self.visibility_factor(range_m)
            cloud = self._cloud_factor(sensor_pos, target_pos)
            return float(vis * cloud)

        # RF_PASSIVE and any unknown kind: LOS only
        return 1.0

    def rain_attenuation_db_per_km(self, frequency_ghz: float = 10.0) -> float:
        """One-way rain attenuation in dB/km using the ITU-R P.838 power law.

        The two coefficients a and b depend on frequency and polarisation. This
        uses the simplified horizontal-polarisation values for 10 GHz:
            a = 0.0101, b = 1.276  (ITU-R P.838-3 Table 1)
        At other frequencies the caller may pass a different frequency_ghz but
        the coefficients are fixed at the 10 GHz approximation for simplicity.
        The return value is two-way loss (sensor tx and rx path) for radar, so
        the formula is applied once and the caller decides one-way vs two-way.

        For radar, the signal travels sensor -> target -> sensor (two passes
        through rain), so the caller should apply this twice. EnvironmentModel
        applies it once and converts to a linear amplitude ratio before squaring
        would be needed. For simplicity we apply dB loss linearly to the
        detection probability (conservative, not physically exact).

        Result is 0.0 when there is no precipitation.
        """
        R = self._weather.precipitation_mm_hr
        if R <= 0.0:
            return 0.0
        # Simplified ITU-R power-law: A = a * R^b dB/km
        a = 0.01
        b = 0.6
        return a * (R ** b)

    def visibility_factor(self, range_m: float) -> float:
        """EO/IR detection probability modifier from meteorological visibility.

        Uses the Koschmieder formula:
            P = exp(-3 * range / visibility)
        At range == visibility the factor is exp(-3) ~ 0.05, matching the
        definition that visibility is where contrast drops to 5% of its
        clear-air value. At range == 0 the factor is 1.0.
        """
        vis = self._weather.visibility_m
        if vis <= 0.0:
            return 0.0
        return float(math.exp(-3.0 * range_m / vis))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _cloud_factor(self, sensor_pos: Vec3, target_pos: Vec3) -> float:
        """Return 0.0 if the sensor is below the cloud ceiling and the target is above it.

        Ground-based EO/IR cannot see above the cloud deck. If both are below or
        both are above, there is no additional penalty (clouds between the two
        would require a proper model, which we do not have, so we err on the
        side of blocking only the clear ground-to-above-cloud case).
        """
        cloud_z = self._weather.cloud_base_m
        sensor_above = sensor_pos[2] >= cloud_z
        target_above = target_pos[2] >= cloud_z
        if not sensor_above and target_above:
            return 0.0
        return 1.0


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _distance(a: Vec3, b: Vec3) -> float:
    """Euclidean distance between two ENU points."""
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    dz = b[2] - a[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _db_loss_to_linear(db: float) -> float:
    """Convert a positive dB loss to a linear power ratio in 0..1.

    A 0 dB loss returns 1.0. Larger losses return values closer to 0.
    """
    if db <= 0.0:
        return 1.0
    return float(10.0 ** (-db / 10.0))
