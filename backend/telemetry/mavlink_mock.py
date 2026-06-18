"""
MockMAVLinkSource — synthetic MAVLink-like telemetry for testing without hardware.

Generates ASSET_STATE-compatible dicts by interpolating between waypoints.
Matches the public interface of MAVLinkSource (connect / disconnect / stream_telemetry).
"""
from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone
from typing import AsyncIterator, List, Optional


def _iso_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


# Default waypoints: a small square at a generic location.
_DEFAULT_WAYPOINTS: list[dict] = [
    {"lat": 37.7749, "lon": -122.4194, "alt_msl": 50.0},
    {"lat": 37.7759, "lon": -122.4194, "alt_msl": 60.0},
    {"lat": 37.7759, "lon": -122.4184, "alt_msl": 60.0},
    {"lat": 37.7749, "lon": -122.4184, "alt_msl": 50.0},
]

# Metres per degree of latitude (approximate).
_M_PER_DEG_LAT = 111_320.0


def _haversine_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return bearing in degrees (0-360) from point 1 to point 2."""
    d_lon = math.radians(lon2 - lon1)
    r_lat1 = math.radians(lat1)
    r_lat2 = math.radians(lat2)
    x = math.sin(d_lon) * math.cos(r_lat2)
    y = math.cos(r_lat1) * math.sin(r_lat2) - math.sin(r_lat1) * math.cos(r_lat2) * math.cos(d_lon)
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360.0) % 360.0


def _haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in metres between two lat/lon points."""
    r = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class MockMAVLinkSource:
    """
    Mock MAVLink source for testing without hardware.

    Generates synthetic ASSET_STATE dicts by moving along a waypoint path
    at a configurable speed and output rate.
    """

    def __init__(
        self,
        waypoints: Optional[List[dict]] = None,
        rate_hz: float = 10.0,
        drone_id: str = "MOCK-01",
        speed_m_s: float = 10.0,
        battery_voltage: float = 12.6,
        battery_drain_per_s: float = 0.01,
    ) -> None:
        """
        waypoints: list of dicts with keys lat, lon, alt_msl.
                   Defaults to a small square near San Francisco.
        rate_hz:   output rate in Hz.
        drone_id:  identifier placed in every yielded dict.
        speed_m_s: simulated ground speed in m/s.
        battery_voltage: starting battery voltage.
        battery_drain_per_s: remaining_pct drained per second.
        """
        self._waypoints: list[dict] = waypoints if waypoints else list(_DEFAULT_WAYPOINTS)
        if len(self._waypoints) < 2:
            raise ValueError("waypoints must contain at least 2 entries.")
        self._rate_hz = rate_hz
        self._drone_id = drone_id
        self._speed_m_s = speed_m_s
        self._battery_voltage = battery_voltage
        self._battery_drain_per_s = battery_drain_per_s

        self._connected: bool = False
        self._seq: int = 0

        # Current interpolated position state.
        self._lat: float = self._waypoints[0]["lat"]
        self._lon: float = self._waypoints[0]["lon"]
        self._alt_msl: float = self._waypoints[0].get("alt_msl", 50.0)
        self._heading: float = 0.0
        self._wp_index: int = 1
        self._remaining_pct: float = 100.0
        self._t_last: float = 0.0

    async def connect(self) -> None:
        """Mark source as connected and reset simulation state."""
        self._connected = True
        self._lat = self._waypoints[0]["lat"]
        self._lon = self._waypoints[0]["lon"]
        self._alt_msl = self._waypoints[0].get("alt_msl", 50.0)
        self._wp_index = 1
        self._remaining_pct = 100.0
        self._seq = 0
        self._t_last = time.monotonic()

    async def disconnect(self) -> None:
        """Mark source as disconnected."""
        self._connected = False

    async def stream_telemetry(self) -> AsyncIterator[dict]:
        """Yield ASSET_STATE dicts following the waypoint path."""
        if not self._connected:
            raise RuntimeError("Call connect() before stream_telemetry().")

        interval = 1.0 / self._rate_hz
        while self._connected:
            t0 = time.monotonic()
            dt = t0 - self._t_last
            self._t_last = t0

            self._advance(dt)
            yield self._build_state()

            elapsed = time.monotonic() - t0
            await asyncio.sleep(max(0.0, interval - elapsed))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _advance(self, dt: float) -> None:
        """Move along the waypoint path by dt seconds at self._speed_m_s."""
        if not self._waypoints or self._wp_index >= len(self._waypoints):
            self._wp_index = 1  # loop back to start

        target = self._waypoints[self._wp_index]
        t_lat = target["lat"]
        t_lon = target["lon"]
        t_alt = target.get("alt_msl", self._alt_msl)

        dist_to_target = _haversine_distance_m(self._lat, self._lon, t_lat, t_lon)
        step_m = self._speed_m_s * dt

        if dist_to_target <= step_m or dist_to_target < 1e-6:
            # Arrived at waypoint — snap and advance to next.
            self._lat = t_lat
            self._lon = t_lon
            self._alt_msl = t_alt
            self._wp_index = (self._wp_index + 1) % len(self._waypoints)
        else:
            frac = step_m / dist_to_target
            self._lat += (t_lat - self._lat) * frac
            self._lon += (t_lon - self._lon) * frac
            self._alt_msl += (t_alt - self._alt_msl) * frac

        self._heading = _haversine_bearing(self._lat, self._lon, t_lat, t_lon)
        self._remaining_pct = max(
            0.0, self._remaining_pct - self._battery_drain_per_s * dt
        )

    def _build_state(self) -> dict:
        self._seq += 1
        wp_next = self._waypoints[self._wp_index % len(self._waypoints)]
        bearing_rad = math.radians(self._heading)

        return {
            "type": "ASSET_STATE",
            "drone_id": self._drone_id,
            "timestamp": _iso_now(),
            "seq": self._seq,
            "position": {
                "lat": self._lat,
                "lon": self._lon,
                "alt_msl": self._alt_msl,
                "alt_agl": max(0.0, self._alt_msl - 10.0),
            },
            "attitude": {
                "roll": math.degrees(math.sin(bearing_rad) * 0.1),
                "pitch": 0.0,
                "yaw": self._heading,
            },
            "velocity": {
                "ground_speed": self._speed_m_s,
                "vertical_speed": 0.0,
                "heading": self._heading,
            },
            "battery": {
                "voltage": self._battery_voltage * (self._remaining_pct / 100.0),
                "current": 12.0,
                "remaining_pct": self._remaining_pct,
            },
            "gps": {
                "fix_type": "3D-RTK",
                "satellites": 14,
                "hdop": 0.8,
            },
        }
