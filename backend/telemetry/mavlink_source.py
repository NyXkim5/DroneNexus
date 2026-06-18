"""
MAVLink telemetry ingestion — converts MAVLink streams to OVERWATCH ASSET_STATE format.

Requires a running drone or SITL. For testing without hardware, use MockMAVLinkSource
from telemetry.mavlink_mock instead.

Connection string examples:
  UDP (SITL default): "udp://:14540"
  Serial:             "serial:///dev/ttyUSB0:57600"
  TCP:                "tcp://localhost:5760"
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

logger = logging.getLogger("overwatch.mavlink_source")

# MAVLink GPS fix type codes -> human-readable strings
_GPS_FIX_MAP: dict[int, str] = {
    0: "NO_FIX",
    1: "NO_FIX",
    2: "2D-FIX",
    3: "3D-FIX",
    4: "3D-DGPS",
    5: "3D-RTK",
    6: "3D-RTK",
}


def _iso_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class MAVLinkSource:
    """Ingests MAVLink telemetry and converts to OVERWATCH ASSET_STATE format."""

    def __init__(
        self,
        connection_string: str = "udp://:14540",
        drone_id: str = "MAVLINK-01",
        rate_hz: float = 10.0,
    ) -> None:
        """
        connection_string: MAVLink connection (UDP, serial, TCP).
        drone_id: identifier placed in every yielded ASSET_STATE dict.
        rate_hz: maximum output rate; upstream MAVSDK streams run asynchronously.
        """
        self._connection_string = connection_string
        self._drone_id = drone_id
        self._rate_hz = rate_hz
        self._system: Optional[object] = None
        self._connected: bool = False
        self._seq: int = 0

        # Latest telemetry snapshots — updated by concurrent stream tasks.
        self._position: Optional[object] = None
        self._attitude: Optional[object] = None
        self._velocity: Optional[object] = None
        self._battery: Optional[object] = None
        self._gps_info: Optional[object] = None

        self._stream_tasks: list[asyncio.Task] = []

    async def connect(self) -> None:
        """Establish MAVLink connection via MAVSDK."""
        try:
            from mavsdk import System  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "mavsdk is not installed. Run: pip install mavsdk"
            ) from exc

        self._system = System()
        await self._system.connect(system_address=self._connection_string)
        logger.info("[%s] Connecting to %s", self._drone_id, self._connection_string)

        async for state in self._system.core.connection_state():
            if state.is_connected:
                self._connected = True
                logger.info("[%s] Connected", self._drone_id)
                break

        self._stream_tasks = [
            asyncio.create_task(self._collect_position()),
            asyncio.create_task(self._collect_attitude()),
            asyncio.create_task(self._collect_velocity()),
            asyncio.create_task(self._collect_battery()),
            asyncio.create_task(self._collect_gps()),
        ]

    async def disconnect(self) -> None:
        """Clean shutdown — cancels background stream tasks."""
        for task in self._stream_tasks:
            task.cancel()
        await asyncio.gather(*self._stream_tasks, return_exceptions=True)
        self._stream_tasks.clear()
        self._connected = False
        logger.info("[%s] Disconnected", self._drone_id)

    async def stream_telemetry(self) -> AsyncIterator[dict]:
        """
        Yield ASSET_STATE-compatible dicts from MAVLink telemetry.

        Maps MAVLink fields to OVERWATCH format:
          GLOBAL_POSITION_INT -> position (lat, lon, alt)
          ATTITUDE            -> attitude (roll, pitch, yaw)
          VFR_HUD             -> velocity (ground_speed, heading, climb)
          SYS_STATUS          -> battery (voltage, current, remaining)
          GPS_RAW_INT         -> gps (fix_type, satellites, hdop)
        """
        if not self._connected:
            raise RuntimeError("Call connect() before stream_telemetry().")

        interval = 1.0 / self._rate_hz
        while self._connected:
            t0 = time.monotonic()
            if self._position is not None:
                yield self._mavlink_to_asset_state(
                    self._position,
                    self._attitude,
                    self._velocity,
                    self._battery,
                    self._gps_info,
                )
            elapsed = time.monotonic() - t0
            await asyncio.sleep(max(0.0, interval - elapsed))

    def _mavlink_to_asset_state(
        self,
        position: object,
        attitude: Optional[object],
        velocity: Optional[object],
        battery: Optional[object],
        gps: Optional[object],
    ) -> dict:
        """Convert individual MAVLink message fields to ASSET_STATE dict."""
        self._seq += 1

        # Position — MAVSDK returns lat/lon in degrees, alt in metres MSL.
        lat = getattr(position, "latitude_deg", 0.0)
        lon = getattr(position, "longitude_deg", 0.0)
        alt_msl = getattr(position, "absolute_altitude_m", 0.0)
        alt_agl = getattr(position, "relative_altitude_m", 0.0)

        # Attitude — radians from MAVSDK, convert to degrees for the HUD.
        roll_deg = math.degrees(getattr(attitude, "roll_rad", 0.0)) if attitude else 0.0
        pitch_deg = math.degrees(getattr(attitude, "pitch_rad", 0.0)) if attitude else 0.0
        yaw_deg = math.degrees(getattr(attitude, "yaw_rad", 0.0)) if attitude else 0.0

        # Velocity — VFR_HUD fields.
        ground_speed = getattr(velocity, "ground_speed_m_s", 0.0) if velocity else 0.0
        vertical_speed = getattr(velocity, "climb_rate_m_s", 0.0) if velocity else 0.0
        heading = getattr(velocity, "heading_deg", yaw_deg) if velocity else yaw_deg

        # Battery — MAVSDK reports voltage in V, current in A, remaining 0-1.
        voltage = 0.0
        current = 0.0
        remaining_pct = 0.0
        if battery is not None:
            voltage = getattr(battery, "voltage_v", 0.0)
            current = getattr(battery, "current_battery_a", 0.0)
            remaining_raw = getattr(battery, "remaining_percent", 0.0)
            # MAVSDK reports 0.0-1.0; normalise to 0-100.
            remaining_pct = remaining_raw * 100.0 if remaining_raw <= 1.0 else remaining_raw

        # GPS — MAVSDK GpsInfo.
        fix_type_str = "NO_FIX"
        satellites = 0
        hdop = 99.9
        if gps is not None:
            fix_type_int = getattr(gps, "fix_type", None)
            if fix_type_int is not None:
                # MAVSDK FixType enum — extract numeric value if needed.
                raw = getattr(fix_type_int, "value", fix_type_int)
                fix_type_str = _GPS_FIX_MAP.get(int(raw), "NO_FIX")
            satellites = getattr(gps, "num_satellites", 0)
            hdop = getattr(gps, "hdop", 99.9)

        return {
            "type": "ASSET_STATE",
            "drone_id": self._drone_id,
            "timestamp": _iso_now(),
            "seq": self._seq,
            "position": {
                "lat": lat,
                "lon": lon,
                "alt_msl": alt_msl,
                "alt_agl": alt_agl,
            },
            "attitude": {
                "roll": roll_deg,
                "pitch": pitch_deg,
                "yaw": yaw_deg,
            },
            "velocity": {
                "ground_speed": ground_speed,
                "vertical_speed": vertical_speed,
                "heading": heading,
            },
            "battery": {
                "voltage": voltage,
                "current": current,
                "remaining_pct": remaining_pct,
            },
            "gps": {
                "fix_type": fix_type_str,
                "satellites": satellites,
                "hdop": hdop,
            },
        }

    # ------------------------------------------------------------------
    # Background coroutines — each subscribes to one MAVSDK telemetry stream
    # and updates the corresponding snapshot field.
    # ------------------------------------------------------------------

    async def _collect_position(self) -> None:
        try:
            async for pos in self._system.telemetry.position():
                self._position = pos
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[%s] Position stream error: %s", self._drone_id, exc)

    async def _collect_attitude(self) -> None:
        try:
            async for att in self._system.telemetry.attitude_euler():
                self._attitude = att
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[%s] Attitude stream error: %s", self._drone_id, exc)

    async def _collect_velocity(self) -> None:
        try:
            async for vel in self._system.telemetry.velocity_ned():
                self._velocity = vel
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[%s] Velocity stream error: %s", self._drone_id, exc)

    async def _collect_battery(self) -> None:
        try:
            async for bat in self._system.telemetry.battery():
                self._battery = bat
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[%s] Battery stream error: %s", self._drone_id, exc)

    async def _collect_gps(self) -> None:
        try:
            async for gps in self._system.telemetry.gps_info():
                self._gps_info = gps
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[%s] GPS stream error: %s", self._drone_id, exc)
