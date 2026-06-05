"""
Tests for SimSensorSource.

These assert the source produces detections, that detections are noisy versions
of the truth, and that range and field of view gate which targets appear. Tests
use a seeded RNG so noise and probability draws are deterministic. They run the
async stream with asyncio.run to avoid depending on plugin config.
"""
import asyncio
import math
import os
import random
import sys

# Ensure the backend root is on sys.path so "from sensors..." works.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csontology import Detection
from sensors.sim_source import SimSensorSource, SimSensorSpec, TruthTarget


def _one_target(pos, vel=(0.0, 0.0, 0.0), tid="t1", rcs=0.4) -> TruthTarget:
    return TruthTarget(id=tid, position=pos, velocity=vel, size_rcs=rcs)


def _collect(source: SimSensorSource, ticks: int) -> list[Detection]:
    """Run the source for a fixed number of ticks and return all detections."""

    async def run() -> list[Detection]:
        out: list[Detection] = []
        await source.start()
        agen = source.stream()
        seen_ticks = 0
        # Drain detections, counting sleeps by tracking unique timestamps.
        timestamps: set[float] = set()
        async for det in agen:
            out.append(det)
            timestamps.add(det.timestamp)
            if len(timestamps) >= ticks:
                await source.stop()
                break
            seen_ticks = len(timestamps)
        await source.stop()
        return out

    return asyncio.run(run())


def test_produces_detections_for_in_range_target() -> None:
    spec = SimSensorSpec(
        sensor_id="radar-1", position=(0.0, 0.0, 0.0), range_m=2000.0,
        fov_deg=360.0, detection_prob=1.0, pos_noise_m=5.0, vel_noise_ms=1.0,
    )
    truth = [_one_target((100.0, 50.0, 30.0), (5.0, -3.0, 0.0))]
    source = SimSensorSource(
        sensors=[spec], truth_fn=lambda: truth, rate_hz=50.0,
        rng=random.Random(7),
    )
    dets = _collect(source, ticks=3)
    assert len(dets) >= 3
    for d in dets:
        assert d.sensor_id == "radar-1"
        assert 0.0 <= d.confidence <= 1.0
        assert d.size_rcs == 0.4


def test_detections_are_noisy_versus_truth() -> None:
    true_pos = (300.0, 400.0, 60.0)
    spec = SimSensorSpec(
        sensor_id="radar-1", position=(0.0, 0.0, 0.0), range_m=5000.0,
        fov_deg=360.0, detection_prob=1.0, pos_noise_m=10.0, vel_noise_ms=2.0,
    )
    truth = [_one_target(true_pos, (8.0, 0.0, 0.0))]
    source = SimSensorSource(
        sensors=[spec], truth_fn=lambda: truth, rate_hz=100.0,
        rng=random.Random(123),
    )
    dets = _collect(source, ticks=20)
    assert len(dets) >= 20
    # At least one detection must differ from the exact truth (noise applied).
    exact = [d for d in dets if d.position == true_pos]
    assert len(exact) < len(dets)
    # Mean reported position should sit near the truth, within a few sigma.
    mean_x = sum(d.position[0] for d in dets) / len(dets)
    assert abs(mean_x - true_pos[0]) < 10.0


def test_out_of_range_target_is_dropped() -> None:
    spec = SimSensorSpec(
        sensor_id="radar-1", position=(0.0, 0.0, 0.0), range_m=500.0,
        fov_deg=360.0, detection_prob=1.0,
    )
    far = _one_target((5000.0, 0.0, 0.0))
    source = SimSensorSource(
        sensors=[spec], truth_fn=lambda: [far], rate_hz=200.0,
        rng=random.Random(1),
    )
    dets = _collect_fixed_ticks(source, ticks=5)
    assert dets == []


def test_field_of_view_gates_by_bearing() -> None:
    # Sensor looks North (bearing 0) with a narrow 60 degree fov.
    spec = SimSensorSpec(
        sensor_id="eo-1", position=(0.0, 0.0, 0.0), range_m=5000.0,
        fov_deg=60.0, bearing_deg=0.0, detection_prob=1.0,
    )
    north_target = _one_target((0.0, 1000.0, 0.0), tid="north")  # in view
    east_target = _one_target((1000.0, 0.0, 0.0), tid="east")    # out of view
    truth = [north_target, east_target]
    source = SimSensorSource(
        sensors=[spec], truth_fn=lambda: truth, rate_hz=200.0,
        rng=random.Random(2),
    )
    dets = _collect_fixed_ticks(source, ticks=4)
    assert len(dets) > 0
    # No detection should originate from the east target. Since noise can move a
    # reported position, assert by checking all reported bearings cluster North.
    for d in dets:
        bearing = math.degrees(math.atan2(d.position[0], d.position[1])) % 360.0
        gap = min(bearing, 360.0 - bearing)
        assert gap < 90.0


def test_detection_prob_zero_emits_nothing() -> None:
    spec = SimSensorSpec(
        sensor_id="radar-1", position=(0.0, 0.0, 0.0), range_m=5000.0,
        fov_deg=360.0, detection_prob=0.0,
    )
    truth = [_one_target((100.0, 100.0, 10.0))]
    source = SimSensorSource(
        sensors=[spec], truth_fn=lambda: truth, rate_hz=200.0,
        rng=random.Random(9),
    )
    dets = _collect_fixed_ticks(source, ticks=6)
    assert dets == []


def test_multiple_sensors_each_observe() -> None:
    s1 = SimSensorSpec(
        sensor_id="a", position=(0.0, 0.0, 0.0), range_m=5000.0,
        fov_deg=360.0, detection_prob=1.0,
    )
    s2 = SimSensorSpec(
        sensor_id="b", position=(200.0, 0.0, 0.0), range_m=5000.0,
        fov_deg=360.0, detection_prob=1.0,
    )
    truth = [_one_target((100.0, 100.0, 20.0))]
    source = SimSensorSource(
        sensors=[s1, s2], truth_fn=lambda: truth, rate_hz=200.0,
        rng=random.Random(4),
    )
    dets = _collect_fixed_ticks(source, ticks=3)
    sensor_ids = {d.sensor_id for d in dets}
    assert sensor_ids == {"a", "b"}


def test_stream_before_start_raises() -> None:
    spec = SimSensorSpec(sensor_id="x")
    source = SimSensorSource(sensors=[spec], truth_fn=lambda: [])

    async def run() -> None:
        agen = source.stream()
        await agen.__anext__()

    try:
        asyncio.run(run())
        raised = False
    except RuntimeError:
        raised = True
    assert raised


def test_requires_at_least_one_sensor() -> None:
    try:
        SimSensorSource(sensors=[], truth_fn=lambda: [])
        raised = False
    except ValueError:
        raised = True
    assert raised


def _collect_fixed_ticks(source: SimSensorSource, ticks: int) -> list[Detection]:
    """Run for a fixed number of ticks even when zero detections are produced.

    The detection-counting drain stalls when nothing is emitted, so this driver
    stops the source after a short wall-clock window covering the ticks.
    """

    async def run() -> list[Detection]:
        out: list[Detection] = []
        await source.start()

        async def stopper() -> None:
            await asyncio.sleep(ticks * source._interval + source._interval / 2)
            await source.stop()

        task = asyncio.create_task(stopper())
        async for det in source.stream():
            out.append(det)
        await task
        return out

    return asyncio.run(run())
