"""
Tests for RealSensorSource — the deployable real-data SensorSource.

These prove the real source emits Detections through the same interface the sim
source uses, converts coordinates correctly, skips malformed log lines, maps live
DroneState telemetry, and actually drives the real fusion engine.
"""
from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from pathlib import Path

import pytest

from csontology import Detection, latlon_to_enu, ORIGIN_LAT, ORIGIN_LON
from fusion.track_manager import FusionConfig, TrackManager
from sensors.base import SensorSource
from sensors.real_source import RealSensorSource


def _write_jsonl(tmp_path: Path, lines: list[str]) -> Path:
    """Write raw JSONL lines to a temp file and return its path."""
    path = tmp_path / "capture.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _latlon_offset(east_m: float, north_m: float) -> tuple[float, float]:
    """Return a lat/lon offset from the site origin by the given ENU meters."""
    meters_per_deg = 111320.0
    lat = ORIGIN_LAT + north_m / meters_per_deg
    lon = ORIGIN_LON + east_m / (meters_per_deg * math.cos(math.radians(ORIGIN_LAT)))
    return lat, lon


def _start(source: RealSensorSource) -> None:
    """Run the async start() to completion in a test."""
    asyncio.run(source.start())


def test_replay_emits_detections_in_timestamp_order(tmp_path: Path) -> None:
    """Recorded contacts come out as Detections ordered by timestamp."""
    lat_a, lon_a = _latlon_offset(east_m=500.0, north_m=0.0)
    lat_b, lon_b = _latlon_offset(east_m=0.0, north_m=800.0)
    lines = [
        json.dumps({
            "sensor_id": "radar-1", "timestamp": 20.0,
            "lat": lat_b, "lon": lon_b, "alt": 120.0, "confidence": 0.7, "rcs": 0.04,
        }),
        json.dumps({
            "sensor_id": "radar-1", "timestamp": 10.0,
            "lat": lat_a, "lon": lon_a, "alt": 50.0, "confidence": 0.9, "rcs": 0.09,
        }),
    ]
    path = _write_jsonl(tmp_path, lines)
    source = RealSensorSource.from_jsonl(path, sensor_id="radar-1")
    _start(source)

    first = source.sample_once()
    second = source.sample_once()
    third = source.sample_once()

    assert [d.timestamp for d in first + second] == [10.0, 20.0]
    assert third == []
    assert all(isinstance(d, Detection) for d in first + second)
    assert first[0].sensor_id == "radar-1"


def test_replay_converts_latlon_to_enu(tmp_path: Path) -> None:
    """A geodetic contact lands at the expected ENU position."""
    lat, lon = _latlon_offset(east_m=500.0, north_m=-300.0)
    line = json.dumps({
        "sensor_id": "rf-2", "timestamp": 1.0,
        "lat": lat, "lon": lon, "alt": 75.0,
    })
    path = _write_jsonl(tmp_path, [line])
    source = RealSensorSource.from_jsonl(path)
    _start(source)

    det = source.sample_once()[0]
    exp_x, exp_y, exp_z = latlon_to_enu(lat, lon, 75.0)
    assert det.position[0] == pytest.approx(exp_x, abs=1e-6)
    assert det.position[1] == pytest.approx(exp_y, abs=1e-6)
    assert det.position[2] == pytest.approx(75.0, abs=1e-6)


def test_replay_carries_confidence_and_rcs(tmp_path: Path) -> None:
    """Confidence and rcs pass through to the Detection unchanged."""
    line = json.dumps({
        "sensor_id": "radar-1", "timestamp": 5.0,
        "x": 100.0, "y": 200.0, "z": 60.0,
        "vx": 3.0, "vy": -4.0, "vz": 0.5,
        "confidence": 0.42, "rcs": 0.07,
    })
    path = _write_jsonl(tmp_path, [line])
    source = RealSensorSource.from_jsonl(path)
    _start(source)

    det = source.sample_once()[0]
    assert det.confidence == pytest.approx(0.42)
    assert det.size_rcs == pytest.approx(0.07)
    assert det.position == (100.0, 200.0, 60.0)
    assert det.velocity == (3.0, -4.0, 0.5)


def test_malformed_lines_skipped(tmp_path: Path) -> None:
    """Bad JSON, non-objects, and records missing position are skipped."""
    good = json.dumps({"sensor_id": "s", "timestamp": 1.0, "x": 1.0, "y": 2.0})
    lines = [
        "{not valid json",
        json.dumps([1, 2, 3]),
        json.dumps({"sensor_id": "s", "timestamp": 2.0}),
        good,
    ]
    path = _write_jsonl(tmp_path, lines)
    source = RealSensorSource.from_jsonl(path)
    _start(source)

    dets = source.sample_once()
    assert len(dets) == 1
    assert dets[0].position == (1.0, 2.0, 0.0)
    assert source.sample_once() == []


@dataclass
class _FakeDroneState:
    """Minimal stand-in for telemetry.collector.DroneState."""

    drone_id: str
    lat: float
    lon: float
    alt_agl: float = 0.0
    alt_msl: float = 0.0
    ground_speed: float = 0.0
    vertical_speed: float = 0.0
    heading: float = 0.0


def test_from_telemetry_maps_dronestate(tmp_path: Path) -> None:
    """A live DroneState feed becomes Detections in the ENU frame."""
    lat, lon = _latlon_offset(east_m=400.0, north_m=600.0)
    states = [_FakeDroneState(
        drone_id="hostile-1", lat=lat, lon=lon, alt_agl=90.0,
        ground_speed=10.0, heading=90.0, vertical_speed=1.5,
    )]
    source = RealSensorSource.from_telemetry(lambda: states, sensor_id="tele")
    _start(source)

    dets = source.sample_once()
    assert len(dets) == 1
    det = dets[0]
    exp_x, exp_y, _exp_z = latlon_to_enu(lat, lon, 90.0)
    assert det.position[0] == pytest.approx(exp_x, abs=1e-6)
    assert det.position[1] == pytest.approx(exp_y, abs=1e-6)
    assert det.position[2] == pytest.approx(90.0, abs=1e-6)
    # heading 90 (East) at 10 m/s maps to +x, ~0 y, vertical to +z.
    assert det.velocity[0] == pytest.approx(10.0, abs=1e-6)
    assert det.velocity[1] == pytest.approx(0.0, abs=1e-6)
    assert det.velocity[2] == pytest.approx(1.5, abs=1e-6)
    assert det.sensor_id == "tele"


def test_is_a_sensor_source() -> None:
    """RealSensorSource satisfies the SensorSource interface."""
    source = RealSensorSource.from_telemetry(lambda: [])
    assert isinstance(source, SensorSource)
    for name in ("start", "stop", "stream", "sample_once"):
        assert hasattr(source, name)


def test_sample_once_before_start_raises(tmp_path: Path) -> None:
    """Sampling before start() is a hard error, not a silent empty list."""
    line = json.dumps({"timestamp": 1.0, "x": 0.0, "y": 0.0})
    source = RealSensorSource.from_jsonl(_write_jsonl(tmp_path, [line]))
    with pytest.raises(RuntimeError):
        source.sample_once()


def test_real_source_drives_fusion(tmp_path: Path) -> None:
    """Feeding real detections into TrackManager.update produces a track.

    This is the core thesis check: the same fusion engine that consumes the sim
    source confirms a track from the real source with no engine change.
    """
    config = FusionConfig(confirm_hits=2, confirm_window=3)
    manager = TrackManager(config)
    # A target sitting still at one ENU point, reported every tick.
    lat, lon = _latlon_offset(east_m=300.0, north_m=400.0)
    lines = [
        json.dumps({
            "sensor_id": "radar-1", "timestamp": float(t),
            "lat": lat, "lon": lon, "alt": 80.0, "confidence": 0.9,
        })
        for t in range(5)
    ]
    source = RealSensorSource.from_jsonl(_write_jsonl(tmp_path, lines))
    _start(source)

    for t in range(5):
        dets = source.sample_once()
        manager.update(dets, float(t))

    confirmed = manager.confirmed_tracks()
    assert len(confirmed) >= 1
    exp_x, exp_y, _z = latlon_to_enu(lat, lon, 80.0)
    track = confirmed[0]
    assert track.position[0] == pytest.approx(exp_x, abs=60.0)
    assert track.position[1] == pytest.approx(exp_y, abs=60.0)


def test_stream_replays_then_returns(tmp_path: Path) -> None:
    """stream() yields each recorded contact then returns cleanly at the end."""
    lines = [
        json.dumps({"sensor_id": "s", "timestamp": float(t), "x": float(t), "y": 0.0})
        for t in range(3)
    ]
    source = RealSensorSource.from_jsonl(_write_jsonl(tmp_path, lines), rate_hz=1000.0)

    async def drain() -> list[Detection]:
        await source.start()
        out: list[Detection] = []
        async for det in source.stream():
            out.append(det)
        await source.stop()
        return out

    collected = asyncio.run(drain())
    assert [d.timestamp for d in collected] == [0.0, 1.0, 2.0]


def test_runner_drives_engine_from_real_source(tmp_path) -> None:
    """The WargameRunner runs on an injected RealSensorSource with no other change.

    The same object seen across ticks confirms a track, proving the deployable
    real-data path drives the identical fusion and decision engine the simulator
    uses. This is the one-engine-two-sources thesis, demonstrated end to end.
    """
    from wargame import load_scenario
    from wargame.runner import WargameRunner

    lines = [
        json.dumps({
            "timestamp": float(t), "x": 400.0 - 25.0 * t, "y": 0.0, "z": 80.0,
            "sensor_id": "radar-real", "id": "obj-1", "confidence": 0.9,
        })
        for t in range(12)
    ]
    path = _write_jsonl(tmp_path, lines)
    scenario = load_scenario("probe_120")
    scenario.max_ticks = 12
    source = RealSensorSource.from_jsonl(str(path), rate_hz=scenario.tick_hz)

    async def go():
        runner = WargameRunner(scenario, source=source)
        last = None
        async for frame in runner.run(pace=False):
            last = frame
        return last

    last = asyncio.run(go())
    assert last is not None
    assert last.metrics.tracks_held >= 1
