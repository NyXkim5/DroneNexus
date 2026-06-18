"""
Tests for MAVLink telemetry ingestion.

All tests use MockMAVLinkSource so no running drone or SITL is required.
MAVLinkSource._mavlink_to_asset_state is tested directly with stub objects
to verify field mapping without importing the hardware-dependent MAVSDK System.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telemetry.mavlink_mock import MockMAVLinkSource
from telemetry.mavlink_source import MAVLinkSource

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WAYPOINTS = [
    {"lat": 37.7749, "lon": -122.4194, "alt_msl": 50.0},
    {"lat": 37.7769, "lon": -122.4194, "alt_msl": 60.0},
    {"lat": 37.7769, "lon": -122.4174, "alt_msl": 55.0},
]

_REQUIRED_FIELDS = {"type", "drone_id", "timestamp", "seq", "position", "attitude", "velocity", "battery", "gps"}
_POSITION_FIELDS = {"lat", "lon", "alt_msl", "alt_agl"}
_ATTITUDE_FIELDS = {"roll", "pitch", "yaw"}
_VELOCITY_FIELDS = {"ground_speed", "vertical_speed", "heading"}
_BATTERY_FIELDS  = {"voltage", "current", "remaining_pct"}
_GPS_FIELDS      = {"fix_type", "satellites", "hdop"}


async def _collect_n(source: MockMAVLinkSource, n: int) -> list[dict]:
    """Connect, collect n messages, disconnect, return messages."""
    await source.connect()
    messages: list[dict] = []
    async for msg in source.stream_telemetry():
        messages.append(msg)
        if len(messages) >= n:
            break
    await source.disconnect()
    return messages


# ---------------------------------------------------------------------------
# test_mock_source_yields_asset_states
# ---------------------------------------------------------------------------

def test_mock_source_yields_asset_states() -> None:
    """MockMAVLinkSource produces valid ASSET_STATE dicts."""
    source = MockMAVLinkSource(waypoints=_WAYPOINTS, rate_hz=50.0)
    messages = asyncio.run(_collect_n(source, 5))
    assert len(messages) == 5
    for msg in messages:
        assert msg["type"] == "ASSET_STATE"
        assert isinstance(msg["drone_id"], str)
        assert len(msg["drone_id"]) > 0


# ---------------------------------------------------------------------------
# test_asset_state_has_required_fields
# ---------------------------------------------------------------------------

def test_asset_state_has_required_fields() -> None:
    """Output has position, attitude, velocity, battery, gps at top level and correct sub-keys."""
    source = MockMAVLinkSource(waypoints=_WAYPOINTS, rate_hz=50.0)
    messages = asyncio.run(_collect_n(source, 3))
    for msg in messages:
        assert _REQUIRED_FIELDS.issubset(msg.keys()), f"Missing top-level keys: {_REQUIRED_FIELDS - msg.keys()}"
        assert _POSITION_FIELDS.issubset(msg["position"].keys())
        assert _ATTITUDE_FIELDS.issubset(msg["attitude"].keys())
        assert _VELOCITY_FIELDS.issubset(msg["velocity"].keys())
        assert _BATTERY_FIELDS.issubset(msg["battery"].keys())
        assert _GPS_FIELDS.issubset(msg["gps"].keys())


# ---------------------------------------------------------------------------
# test_position_follows_waypoints
# ---------------------------------------------------------------------------

def test_position_follows_waypoints() -> None:
    """Mock source moves between waypoints — lat/lon changes across messages."""
    # Use a slow rate and high speed so position changes are measurable.
    source = MockMAVLinkSource(
        waypoints=_WAYPOINTS,
        rate_hz=10.0,
        speed_m_s=50.0,
    )
    messages = asyncio.run(_collect_n(source, 20))

    lats = [m["position"]["lat"] for m in messages]
    lons = [m["position"]["lon"] for m in messages]

    # Position must not be static across all messages.
    assert max(lats) - min(lats) > 0.0 or max(lons) - min(lons) > 0.0

    # All positions must be in a plausible neighbourhood of the waypoints.
    for msg in messages:
        assert 37.77 <= msg["position"]["lat"] <= 37.78
        assert -122.42 <= msg["position"]["lon"] <= -122.41


# ---------------------------------------------------------------------------
# test_rate_control
# ---------------------------------------------------------------------------

def test_rate_control() -> None:
    """At 10Hz, approximately 10 messages are produced per second."""

    async def run() -> float:
        source = MockMAVLinkSource(waypoints=_WAYPOINTS, rate_hz=10.0)
        await source.connect()
        count = 0
        t_start = time.monotonic()
        async for _ in source.stream_telemetry():
            count += 1
            if time.monotonic() - t_start >= 1.0:
                break
        await source.disconnect()
        return count / (time.monotonic() - t_start)

    actual_hz = asyncio.run(run())
    # Allow 30% tolerance around 10Hz.
    assert 7.0 <= actual_hz <= 13.0, f"Expected ~10Hz, got {actual_hz:.1f}Hz"


# ---------------------------------------------------------------------------
# test_connect_disconnect
# ---------------------------------------------------------------------------

def test_connect_disconnect() -> None:
    """Lifecycle works without error: connect, stream one message, disconnect."""

    async def run() -> dict:
        source = MockMAVLinkSource(waypoints=_WAYPOINTS)
        await source.connect()
        assert source._connected is True
        msg = None
        async for m in source.stream_telemetry():
            msg = m
            break
        await source.disconnect()
        assert source._connected is False
        return msg

    msg = asyncio.run(run())
    assert msg is not None
    assert msg["type"] == "ASSET_STATE"


def test_stream_before_connect_raises() -> None:
    """stream_telemetry raises RuntimeError if connect() was not called first."""

    async def run() -> None:
        source = MockMAVLinkSource(waypoints=_WAYPOINTS)
        async for _ in source.stream_telemetry():
            break

    with pytest.raises(RuntimeError):
        asyncio.run(run())


def test_waypoints_minimum_two_required() -> None:
    """MockMAVLinkSource raises ValueError when fewer than two waypoints are given."""
    with pytest.raises(ValueError):
        MockMAVLinkSource(waypoints=[{"lat": 1.0, "lon": 1.0, "alt_msl": 10.0}])


# ---------------------------------------------------------------------------
# test_mavlink_to_asset_state_mapping
# ---------------------------------------------------------------------------

class _StubPosition:
    latitude_deg = 47.3977
    longitude_deg = 8.5456
    absolute_altitude_m = 488.0
    relative_altitude_m = 20.0


class _StubAttitude:
    roll_rad = 0.05
    pitch_rad = -0.02
    yaw_rad = 1.57


class _StubVelocity:
    ground_speed_m_s = 12.5
    climb_rate_m_s = 0.3
    heading_deg = 90.0


class _StubBattery:
    voltage_v = 11.8
    current_battery_a = 18.2
    remaining_percent = 0.74


class _StubGPSInfo:
    fix_type = 3  # 3D-FIX
    num_satellites = 12
    hdop = 1.1


def test_mavlink_to_asset_state_mapping() -> None:
    """_mavlink_to_asset_state produces correct field values from stub inputs."""
    import math

    source = MAVLinkSource(drone_id="TEST-01")
    result = source._mavlink_to_asset_state(
        _StubPosition(),
        _StubAttitude(),
        _StubVelocity(),
        _StubBattery(),
        _StubGPSInfo(),
    )

    # Top-level structure.
    assert result["type"] == "ASSET_STATE"
    assert result["drone_id"] == "TEST-01"
    assert result["seq"] == 1

    # Position mapping.
    assert result["position"]["lat"] == pytest.approx(47.3977)
    assert result["position"]["lon"] == pytest.approx(8.5456)
    assert result["position"]["alt_msl"] == pytest.approx(488.0)
    assert result["position"]["alt_agl"] == pytest.approx(20.0)

    # Attitude — radians converted to degrees.
    assert result["attitude"]["roll"] == pytest.approx(math.degrees(0.05), rel=1e-4)
    assert result["attitude"]["pitch"] == pytest.approx(math.degrees(-0.02), rel=1e-4)
    assert result["attitude"]["yaw"] == pytest.approx(math.degrees(1.57), rel=1e-4)

    # Velocity.
    assert result["velocity"]["ground_speed"] == pytest.approx(12.5)
    assert result["velocity"]["vertical_speed"] == pytest.approx(0.3)
    assert result["velocity"]["heading"] == pytest.approx(90.0)

    # Battery — remaining_percent 0-1 normalised to 0-100.
    assert result["battery"]["voltage"] == pytest.approx(11.8)
    assert result["battery"]["current"] == pytest.approx(18.2)
    assert result["battery"]["remaining_pct"] == pytest.approx(74.0)

    # GPS.
    assert result["gps"]["fix_type"] == "3D-FIX"
    assert result["gps"]["satellites"] == 12
    assert result["gps"]["hdop"] == pytest.approx(1.1)


def test_mavlink_to_asset_state_none_subsystems() -> None:
    """When attitude/velocity/battery/gps are None, mapping returns safe defaults."""
    source = MAVLinkSource(drone_id="TEST-02")
    result = source._mavlink_to_asset_state(
        _StubPosition(), None, None, None, None
    )
    assert result["attitude"]["roll"] == 0.0
    assert result["attitude"]["pitch"] == 0.0
    assert result["velocity"]["ground_speed"] == 0.0
    assert result["battery"]["remaining_pct"] == 0.0
    assert result["gps"]["fix_type"] == "NO_FIX"


# ---------------------------------------------------------------------------
# test_seq_increments
# ---------------------------------------------------------------------------

def test_seq_increments() -> None:
    """Sequence numbers increment monotonically across successive messages."""
    source = MockMAVLinkSource(waypoints=_WAYPOINTS, rate_hz=50.0)
    messages = asyncio.run(_collect_n(source, 10))
    seqs = [m["seq"] for m in messages]
    for i in range(1, len(seqs)):
        assert seqs[i] == seqs[i - 1] + 1, f"seq gap at index {i}: {seqs}"
