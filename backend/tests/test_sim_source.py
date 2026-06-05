"""
Tests for SimSensorSource.

These assert the source produces detections, that detections are noisy versions
of the truth, and that range and field of view gate which targets appear. They
also cover the phenomenology layer: RCS-dependent detection, range-dependent
noise, clutter false alarms, sensor-kind differentiation, confidence from SNR,
and determinism. Tests use a seeded RNG so noise and probability draws are
deterministic. They run the async stream with asyncio.run to avoid depending on
plugin config.

Real-target tests set false_alarm_rate=0.0 so clutter does not contaminate
assertions about truth detections. Dedicated tests cover clutter on its own.
"""
import asyncio
import math
import os
import random
import sys

# Ensure the backend root is on sys.path so "from sensors..." works.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csontology import Detection
from sensors.sim_source import (
    SensorKind,
    SimSensorSource,
    SimSensorSpec,
    TruthTarget,
)


def _one_target(pos, vel=(0.0, 0.0, 0.0), tid="t1", rcs=0.4) -> TruthTarget:
    return TruthTarget(id=tid, position=pos, velocity=vel, size_rcs=rcs)


def _collect(source: SimSensorSource, ticks: int) -> list[Detection]:
    """Run the source for a fixed number of ticks and return all detections."""

    async def run() -> list[Detection]:
        out: list[Detection] = []
        await source.start()
        agen = source.stream()
        timestamps: set[float] = set()
        async for det in agen:
            out.append(det)
            timestamps.add(det.timestamp)
            if len(timestamps) >= ticks:
                await source.stop()
                break
        await source.stop()
        return out

    return asyncio.run(run())


def test_produces_detections_for_in_range_target() -> None:
    spec = SimSensorSpec(
        sensor_id="radar-1", position=(0.0, 0.0, 0.0), range_m=2000.0,
        fov_deg=360.0, detection_prob=1.0, pos_noise_m=5.0, vel_noise_ms=1.0,
        false_alarm_rate=0.0,
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
        cross_range_factor=1.0, false_alarm_rate=0.0,
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
    assert abs(mean_x - true_pos[0]) < 20.0


def test_out_of_range_target_is_dropped() -> None:
    spec = SimSensorSpec(
        sensor_id="radar-1", position=(0.0, 0.0, 0.0), range_m=500.0,
        fov_deg=360.0, detection_prob=1.0, false_alarm_rate=0.0,
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
        pos_noise_m=1.0, cross_range_factor=1.0, false_alarm_rate=0.0,
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
        fov_deg=360.0, detection_prob=0.0, false_alarm_rate=0.0,
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
        fov_deg=360.0, detection_prob=1.0, false_alarm_rate=0.0,
    )
    s2 = SimSensorSpec(
        sensor_id="b", position=(200.0, 0.0, 0.0), range_m=5000.0,
        fov_deg=360.0, detection_prob=1.0, false_alarm_rate=0.0,
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


# ---- Phenomenology tests ----


def _real_detections(dets: list[Detection]) -> list[Detection]:
    """Keep only true-target detections, dropping clutter by id marker."""
    return [d for d in dets if "-fa-" not in d.id]


def test_low_rcs_detected_less_than_high_rcs_same_range() -> None:
    """A small-RCS target detects less often than a large one at equal range."""
    pos = (1800.0, 0.0, 0.0)  # past reference range so Pd is below peak
    spec = SimSensorSpec(
        sensor_id="r", position=(0.0, 0.0, 0.0), range_m=5000.0,
        detection_prob=1.0, false_alarm_rate=0.0, sensor_kind=SensorKind.RADAR,
    )

    def hits_for(rcs: float) -> int:
        target = [_one_target(pos, tid="t", rcs=rcs)]
        source = SimSensorSource(
            sensors=[spec], truth_fn=lambda: target, rate_hz=500.0,
            rng=random.Random(99),
        )
        dets = _real_detections(_collect_fixed_ticks(source, ticks=200))
        return len(dets)

    low = hits_for(0.01)
    high = hits_for(1.0)
    assert low < high
    assert high > 0


def test_noise_grows_with_range() -> None:
    """Position spread of detections is larger for a far target than a near one."""
    spec = SimSensorSpec(
        sensor_id="r", position=(0.0, 0.0, 0.0), range_m=8000.0,
        detection_prob=1.0, false_alarm_rate=0.0, sensor_kind=SensorKind.RADAR,
    )

    def spread_at(distance: float) -> float:
        pos = (0.0, distance, 0.0)
        target = [_one_target(pos, tid="t", rcs=5.0)]
        source = SimSensorSource(
            sensors=[spec], truth_fn=lambda: target, rate_hz=500.0,
            rng=random.Random(5),
        )
        dets = _real_detections(_collect(source, ticks=80))
        xs = [d.position[0] for d in dets]
        mean = sum(xs) / len(xs)
        return math.sqrt(sum((x - mean) ** 2 for x in xs) / len(xs))

    near = spread_at(400.0)
    far = spread_at(3000.0)
    assert far > near


def test_false_alarms_appear_with_low_confidence() -> None:
    """Clutter appears with no real targets and carries low confidence."""
    spec = SimSensorSpec(
        sensor_id="r", position=(0.0, 0.0, 0.0), range_m=3000.0,
        false_alarm_rate=2.0, sensor_kind=SensorKind.RADAR,
    )
    source = SimSensorSource(
        sensors=[spec], truth_fn=lambda: [], rate_hz=500.0,
        rng=random.Random(3),
    )
    dets = _collect_fixed_ticks(source, ticks=20)
    assert len(dets) > 0
    for d in dets:
        assert "-fa-" in d.id
        assert d.confidence < 0.2
        assert d.size_rcs is None


def test_false_alarm_rate_zero_emits_no_clutter() -> None:
    """With no targets and zero clutter rate the source emits nothing."""
    spec = SimSensorSpec(
        sensor_id="r", position=(0.0, 0.0, 0.0), range_m=3000.0,
        false_alarm_rate=0.0,
    )
    source = SimSensorSource(
        sensors=[spec], truth_fn=lambda: [], rate_hz=500.0,
        rng=random.Random(8),
    )
    dets = _collect_fixed_ticks(source, ticks=15)
    assert dets == []


def test_sensor_kinds_differ_in_characteristics() -> None:
    """Different kinds carry different default range, noise, and clutter."""
    radar = SimSensorSpec(sensor_id="r", sensor_kind=SensorKind.RADAR)
    eoir = SimSensorSpec(sensor_id="e", sensor_kind=SensorKind.EOIR)
    rf = SimSensorSpec(sensor_id="f", sensor_kind=SensorKind.RF_PASSIVE)
    # EO/IR is range limited versus radar and RF.
    assert eoir.range_m < radar.range_m
    assert eoir.range_m < rf.range_m
    # EO/IR has finer cross-range position noise than radar.
    assert eoir.cross_range_factor < radar.cross_range_factor
    # Radar measures velocity better (lower factor) than RF passive.
    assert radar.radial_vel_factor < rf.radial_vel_factor
    # EO/IR degrades in poor conditions, radar does not.
    assert eoir.condition_factor < radar.condition_factor


def test_eoir_shorter_effective_range_than_radar() -> None:
    """An EO/IR target beyond its range is dropped while radar still sees it."""
    far = _one_target((0.0, 3000.0, 0.0), tid="far", rcs=5.0)
    eoir = SimSensorSpec(
        sensor_id="e", sensor_kind=SensorKind.EOIR, detection_prob=1.0,
        false_alarm_rate=0.0,
    )
    radar = SimSensorSpec(
        sensor_id="r", sensor_kind=SensorKind.RADAR, detection_prob=1.0,
        false_alarm_rate=0.0,
    )
    eo_src = SimSensorSource(
        sensors=[eoir], truth_fn=lambda: [far], rate_hz=500.0,
        rng=random.Random(1),
    )
    rad_src = SimSensorSource(
        sensors=[radar], truth_fn=lambda: [far], rate_hz=500.0,
        rng=random.Random(1),
    )
    eo_dets = _real_detections(_collect_fixed_ticks(eo_src, ticks=10))
    rad_dets = _real_detections(_collect(rad_src, ticks=10))
    assert eo_dets == []           # 3000m is past EO/IR 2200m range
    assert len(rad_dets) > 0       # within radar 4000m range


def test_confidence_falls_with_range_and_rcs() -> None:
    """A near large target reads higher confidence than a far small one."""
    spec = SimSensorSpec(
        sensor_id="r", position=(0.0, 0.0, 0.0), range_m=6000.0,
        detection_prob=1.0, false_alarm_rate=0.0, sensor_kind=SensorKind.RADAR,
    )

    def mean_conf(distance: float, rcs: float) -> float:
        pos = (0.0, distance, 0.0)
        target = [_one_target(pos, tid="t", rcs=rcs)]
        source = SimSensorSource(
            sensors=[spec], truth_fn=lambda: target, rate_hz=500.0,
            rng=random.Random(2),
        )
        dets = _real_detections(_collect_fixed_ticks(source, ticks=300))
        assert dets, "expected at least one detection to measure confidence"
        return sum(d.confidence for d in dets) / len(dets)

    strong = mean_conf(300.0, 5.0)
    weak = mean_conf(2200.0, 0.05)
    assert strong > weak


def test_determinism_under_fixed_seed() -> None:
    """Two sources with the same seed produce identical detection streams."""
    truth = [
        _one_target((500.0, 500.0, 40.0), (3.0, -2.0, 0.0), tid="a", rcs=0.3),
        _one_target((1200.0, -300.0, 80.0), (-5.0, 1.0, 0.0), tid="b", rcs=1.5),
    ]
    spec = SimSensorSpec(
        sensor_id="r", position=(0.0, 0.0, 0.0), range_m=4000.0,
        detection_prob=0.9, false_alarm_rate=0.5, sensor_kind=SensorKind.RADAR,
    )

    def stream_once() -> list[tuple]:
        source = SimSensorSource(
            sensors=[spec], truth_fn=lambda: truth, rate_hz=500.0,
            rng=random.Random(42),
        )
        dets = _collect(source, ticks=10)
        return [(d.id, d.position, d.velocity, d.confidence) for d in dets]

    assert stream_once() == stream_once()


def test_undeclared_rcs_still_detects() -> None:
    """A target with no RCS uses the default and is still detectable near range."""
    spec = SimSensorSpec(
        sensor_id="r", position=(0.0, 0.0, 0.0), range_m=4000.0,
        detection_prob=1.0, false_alarm_rate=0.0, sensor_kind=SensorKind.RADAR,
    )
    target = [TruthTarget(id="t", position=(300.0, 0.0, 0.0),
                          velocity=(0.0, 0.0, 0.0), size_rcs=None)]
    source = SimSensorSource(
        sensors=[spec], truth_fn=lambda: target, rate_hz=500.0,
        rng=random.Random(6),
    )
    dets = _real_detections(_collect(source, ticks=20))
    assert len(dets) > 0
    for d in dets:
        assert d.size_rcs is None


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
