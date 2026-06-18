"""
preflight.py

Preflight check system inspired by Skybrush Server's PreflightCheckResult pattern.
Pure Python module with zero ROS2 dependencies for full unit testability.

Provides a safety gate before missions by validating battery, GPS, connection,
geofence, IMU, and mode state against configurable thresholds.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

__all__ = (
    "PreflightCheckResult",
    "PreflightCheck",
    "PreflightChecker",
)


class PreflightCheckResult(Enum):
    """Possible outcomes for a single preflight check item."""

    PASS = "pass"
    WARNING = "warning"
    RUNNING = "running"
    SOFT_FAILURE = "softFailure"
    FAILURE = "failure"
    SKIP = "skip"

    @property
    def severity(self) -> int:
        """Numeric severity. Higher means worse."""
        return _SEVERITY_MAP[self]

    @property
    def passed(self) -> bool:
        """True only for PASS and WARNING results."""
        return self in (PreflightCheckResult.PASS, PreflightCheckResult.WARNING)


_SEVERITY_MAP = {
    PreflightCheckResult.PASS: 0,
    PreflightCheckResult.WARNING: 10,
    PreflightCheckResult.RUNNING: 20,
    PreflightCheckResult.SOFT_FAILURE: 30,
    PreflightCheckResult.FAILURE: 40,
    PreflightCheckResult.SKIP: 0,
}


@dataclass
class PreflightCheck:
    """Result of a single preflight check."""

    name: str
    result: PreflightCheckResult
    message: str
    timestamp: float = field(default_factory=time.time)


class PreflightChecker:
    """Runs all preflight checks against current telemetry state.

    All thresholds are configurable via constructor kwargs.
    """

    def __init__(
        self,
        *,
        min_battery_pct: float = 20.0,
        warn_battery_pct: float = 30.0,
        min_gps_satellites: int = 4,
        warn_gps_satellites: int = 6,
        max_gps_hdop: float = 2.0,
        required_mode: str | None = None,
        max_altitude_m: float = 120.0,
        max_accel_bias: float = 0.5,
        max_gyro_bias: float = 0.05,
    ) -> None:
        self.min_battery_pct = min_battery_pct
        self.warn_battery_pct = warn_battery_pct
        self.min_gps_satellites = min_gps_satellites
        self.warn_gps_satellites = warn_gps_satellites
        self.max_gps_hdop = max_gps_hdop
        self.required_mode = required_mode
        self.max_altitude_m = max_altitude_m
        self.max_accel_bias = max_accel_bias
        self.max_gyro_bias = max_gyro_bias

        self._results: list[PreflightCheck] = []

    @property
    def passed(self) -> bool:
        """True if no check resulted in FAILURE or SOFT_FAILURE."""
        return all(
            r.result not in (
                PreflightCheckResult.FAILURE,
                PreflightCheckResult.SOFT_FAILURE,
            )
            for r in self._results
        )

    @property
    def summary(self) -> PreflightCheckResult:
        """Return the worst (highest severity) result across all checks."""
        if not self._results:
            return PreflightCheckResult.PASS
        return max(self._results, key=lambda r: r.result.severity).result

    def run_all(self, state: dict) -> list[PreflightCheck]:
        """Run every check against the provided state dict.

        Expected keys: battery_pct, battery_voltage, gps_satellites, gps_hdop,
        gps_fix_type, connected, armed, lat, lon, alt, flight_mode,
        accel_bias, gyro_bias.
        """
        self._results = [
            self.check_battery(
                state.get("battery_voltage"),
                state.get("battery_pct"),
            ),
            self.check_gps(
                state.get("gps_satellites"),
                state.get("gps_hdop"),
                state.get("gps_fix_type"),
            ),
            self.check_connection(state.get("connected", False)),
            self.check_armed_state(
                state.get("armed", False),
                state.get("expected_armed", False),
            ),
            self.check_mode(
                state.get("flight_mode"),
                self.required_mode,
            ),
            self.check_imu(
                state.get("accel_bias"),
                state.get("gyro_bias"),
            ),
        ]

        geofence = state.get("geofence_vertices")
        if geofence is not None:
            self._results.append(
                self.check_geofence(
                    state.get("lat"),
                    state.get("lon"),
                    state.get("alt"),
                    geofence,
                    self.max_altitude_m,
                )
            )

        for check in self._results:
            _log_check(check)

        return list(self._results)

    def check_battery(
        self,
        voltage: float | None,
        percentage: float | None,
    ) -> PreflightCheck:
        """Validate battery level against thresholds."""
        if percentage is None:
            return PreflightCheck(
                name="battery",
                result=PreflightCheckResult.FAILURE,
                message="Battery percentage unavailable",
            )
        if percentage < self.min_battery_pct:
            return PreflightCheck(
                name="battery",
                result=PreflightCheckResult.FAILURE,
                message=f"Battery critically low: {percentage:.1f}%",
            )
        if percentage < self.warn_battery_pct:
            return PreflightCheck(
                name="battery",
                result=PreflightCheckResult.WARNING,
                message=f"Battery marginal: {percentage:.1f}%",
            )
        return PreflightCheck(
            name="battery",
            result=PreflightCheckResult.PASS,
            message=f"Battery OK: {percentage:.1f}% at {voltage}V",
        )

    def check_gps(
        self,
        satellites: int | None,
        hdop: float | None,
        fix_type: str | None,
    ) -> PreflightCheck:
        """Validate GPS lock quality."""
        if fix_type is None or fix_type == "NO_FIX":
            return PreflightCheck(
                name="gps",
                result=PreflightCheckResult.FAILURE,
                message="No GPS fix",
            )
        if satellites is None or satellites < self.min_gps_satellites:
            sats = satellites if satellites is not None else 0
            return PreflightCheck(
                name="gps",
                result=PreflightCheckResult.FAILURE,
                message=f"Insufficient satellites: {sats}",
            )
        warnings: list[str] = []
        if satellites < self.warn_gps_satellites:
            warnings.append(f"low satellite count ({satellites})")
        if hdop is not None and hdop > self.max_gps_hdop:
            warnings.append(f"high HDOP ({hdop:.1f})")
        if warnings:
            return PreflightCheck(
                name="gps",
                result=PreflightCheckResult.WARNING,
                message=f"GPS marginal: {', '.join(warnings)}",
            )
        return PreflightCheck(
            name="gps",
            result=PreflightCheckResult.PASS,
            message=f"GPS OK: {satellites} sats, HDOP {hdop}",
        )

    def check_connection(self, connected: bool) -> PreflightCheck:
        """Validate autopilot connection."""
        if not connected:
            return PreflightCheck(
                name="connection",
                result=PreflightCheckResult.FAILURE,
                message="Autopilot not connected",
            )
        return PreflightCheck(
            name="connection",
            result=PreflightCheckResult.PASS,
            message="Autopilot connected",
        )

    def check_armed_state(
        self,
        armed: bool,
        expected: bool,
    ) -> PreflightCheck:
        """Validate arm state matches expectation."""
        if armed != expected:
            actual = "armed" if armed else "disarmed"
            want = "armed" if expected else "disarmed"
            return PreflightCheck(
                name="armed_state",
                result=PreflightCheckResult.FAILURE,
                message=f"Vehicle is {actual}, expected {want}",
            )
        return PreflightCheck(
            name="armed_state",
            result=PreflightCheckResult.PASS,
            message="Arm state correct",
        )

    def check_geofence(
        self,
        lat: float | None,
        lon: float | None,
        alt: float | None,
        geofence_vertices: list[tuple[float, float]],
        max_alt: float,
    ) -> PreflightCheck:
        """Validate position is within geofence polygon and altitude limit."""
        if lat is None or lon is None:
            return PreflightCheck(
                name="geofence",
                result=PreflightCheckResult.FAILURE,
                message="Position unavailable for geofence check",
            )
        if alt is not None and alt > max_alt:
            return PreflightCheck(
                name="geofence",
                result=PreflightCheckResult.FAILURE,
                message=f"Altitude {alt:.1f}m exceeds limit {max_alt:.1f}m",
            )
        if not _point_in_polygon(lat, lon, geofence_vertices):
            return PreflightCheck(
                name="geofence",
                result=PreflightCheckResult.FAILURE,
                message=f"Position ({lat:.6f}, {lon:.6f}) outside geofence",
            )
        return PreflightCheck(
            name="geofence",
            result=PreflightCheckResult.PASS,
            message="Position within geofence",
        )

    def check_mode(
        self,
        current_mode: str | None,
        required_mode: str | None,
    ) -> PreflightCheck:
        """Validate flight mode matches requirement."""
        if required_mode is None:
            return PreflightCheck(
                name="mode",
                result=PreflightCheckResult.SKIP,
                message="No required mode configured",
            )
        if current_mode is None or current_mode != required_mode:
            return PreflightCheck(
                name="mode",
                result=PreflightCheckResult.WARNING,
                message=(
                    f"Mode is '{current_mode}', "
                    f"expected '{required_mode}'"
                ),
            )
        return PreflightCheck(
            name="mode",
            result=PreflightCheckResult.PASS,
            message=f"Mode correct: {current_mode}",
        )

    def check_imu(
        self,
        accel_bias: float | None,
        gyro_bias: float | None,
    ) -> PreflightCheck:
        """Validate IMU calibration bias levels."""
        if accel_bias is None and gyro_bias is None:
            return PreflightCheck(
                name="imu",
                result=PreflightCheckResult.SKIP,
                message="IMU bias data unavailable",
            )
        warnings: list[str] = []
        if accel_bias is not None and accel_bias > self.max_accel_bias:
            warnings.append(
                f"accel bias {accel_bias:.3f} > {self.max_accel_bias}"
            )
        if gyro_bias is not None and gyro_bias > self.max_gyro_bias:
            warnings.append(
                f"gyro bias {gyro_bias:.4f} > {self.max_gyro_bias}"
            )
        if warnings:
            return PreflightCheck(
                name="imu",
                result=PreflightCheckResult.WARNING,
                message=f"IMU marginal: {', '.join(warnings)}",
            )
        return PreflightCheck(
            name="imu",
            result=PreflightCheckResult.PASS,
            message="IMU calibration OK",
        )


def _point_in_polygon(
    lat: float,
    lon: float,
    vertices: list[tuple[float, float]],
) -> bool:
    """Ray-casting algorithm for point-in-polygon test."""
    n = len(vertices)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        yi, xi = vertices[i]
        yj, xj = vertices[j]
        if ((yi > lon) != (yj > lon)) and (
            lat < (xj - xi) * (lon - yi) / (yj - yi) + xi
        ):
            inside = not inside
        j = i
    return inside


def _log_check(check: PreflightCheck) -> None:
    """Log a single check result at the appropriate level."""
    if check.result is PreflightCheckResult.FAILURE:
        logger.error("PREFLIGHT FAIL [%s]: %s", check.name, check.message)
    elif check.result is PreflightCheckResult.SOFT_FAILURE:
        logger.warning(
            "PREFLIGHT SOFT_FAIL [%s]: %s", check.name, check.message
        )
    elif check.result is PreflightCheckResult.WARNING:
        logger.warning("PREFLIGHT WARN [%s]: %s", check.name, check.message)
    elif check.result is PreflightCheckResult.PASS:
        logger.debug("PREFLIGHT PASS [%s]: %s", check.name, check.message)
