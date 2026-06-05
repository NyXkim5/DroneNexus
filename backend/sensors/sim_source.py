"""
SimSensorSource — a phenomenologically realistic simulated SensorSource.

This adapter samples ground truth from an attacker simulation and emits noisy
Detection events. It models one or more simulated sensors of different kinds:
radar, electro-optical/infrared (EO/IR), and RF/passive. Each sensor sits at a
fixed position, sees out to a range, covers a horizontal field of view, and
turns ground-truth targets into imperfect contacts. The result is a realistic,
imperfect picture for the fusion engine to clean up.

Phenomenology (deterministic given the injected rng):
  - RCS-dependent detection. A radar-range-equation style signal to noise ratio
    rises with target RCS and falls with range to the fourth power. Detection
    probability is a logistic function of that SNR, so small or low-RCS targets
    are detected less often and at shorter effective range.
  - Range-dependent measurement noise. Position and velocity noise grow with
    range relative to a reference distance, not constant across the field.
  - Sensor-type differentiation. Radar, EO/IR, and RF/passive carry different
    range, noise, detection, and condition characteristics.
  - False alarms. Each sensor emits a small Poisson-like number of spurious
    contacts per tick inside its coverage, carrying low confidence, so fusion
    must reject clutter.
  - Confidence reflects SNR (RCS and range), not just distance.

The source does not import attacker internals. It accepts a truth callable that
returns the current ground-truth targets. This keeps the attacker and the sensor
layer decoupled, matching the design boundary "one engine, two sources".

Usage:
  source = SimSensorSource(sensors=[...], truth_fn=attacker.current_targets)
  await source.start()
  async for detection in source.stream():
      fusion.ingest(detection)
  await source.stop()

Coordinate frame is local ENU meters about the site origin, the shared world
model frame. See csontology for the definition.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Callable, Optional, Sequence

from csontology import Detection, Vec3, now
from sensors.base import SensorSource

logger = logging.getLogger("overwatch.sensors")

# Default RCS in square meters used when a truth target carries none. A small
# commercial quadrotor returns on the order of 0.01..0.1 m^2. This sits in that
# band so undeclared targets behave like typical hostile drones.
_DEFAULT_RCS_M2 = 0.05

# Reference range in meters at which a reference-RCS target detects at the
# sensor's nominal detection_prob. SNR is normalized to this point.
_REF_RANGE_M = 1500.0

# Logistic steepness for the SNR-to-detection mapping. Higher is a sharper
# range cutoff. Tuned so detection stays high well inside reference range and
# falls off past it.
_PD_STEEPNESS = 1.1


@dataclass(frozen=True)
class TruthTarget:
    """One ground-truth object the attacker exposes to the sensor layer.

    Immutable snapshot of a real target at one instant. The sensor reads these
    through the truth callable and turns in-range ones into noisy Detections.
    Position and velocity are ENU meters and m/s about the site origin. size_rcs
    is the radar cross section in square meters and may be None, in which case
    the sensor model uses a sensible default.
    """
    id: str
    position: Vec3
    velocity: Vec3
    size_rcs: Optional[float] = None


# A function the source calls each tick to read current ground truth.
TruthFn = Callable[[], Sequence[TruthTarget]]


class SensorKind(str, Enum):
    """Phenomenology family a simulated sensor belongs to.

    RADAR has long range and good radial velocity but coarser cross-range
    position. EOIR has fine angular precision but limited range and weak
    performance in poor conditions. RF_PASSIVE is bearing-leaning and keys on
    emitters, so it favors non-stealthy targets and carries weak velocity.
    """
    RADAR = "RADAR"
    EOIR = "EOIR"
    RF_PASSIVE = "RF_PASSIVE"


@dataclass(frozen=True)
class _KindProfile:
    """Built-in defaults for one sensor kind, applied when a spec omits them."""
    range_m: float
    pos_noise_m: float
    vel_noise_ms: float
    cross_range_factor: float
    radial_vel_factor: float
    condition_factor: float
    false_alarm_rate: float
    base_confidence: float


# Sane defaults per kind. cross_range_factor scales tangential position noise
# relative to radial. radial_vel_factor scales velocity noise (below 1 means the
# kind measures velocity well). condition_factor in 0..1 degrades detection in
# poor conditions (EO/IR is weather sensitive). false_alarm_rate is the mean
# spurious contacts per tick.
_KIND_PROFILES: dict[SensorKind, _KindProfile] = {
    SensorKind.RADAR: _KindProfile(
        range_m=4000.0, pos_noise_m=8.0, vel_noise_ms=1.0,
        cross_range_factor=2.5, radial_vel_factor=0.6, condition_factor=1.0,
        false_alarm_rate=0.25, base_confidence=0.85,
    ),
    SensorKind.EOIR: _KindProfile(
        range_m=2200.0, pos_noise_m=3.0, vel_noise_ms=2.5,
        cross_range_factor=0.4, radial_vel_factor=1.8, condition_factor=0.6,
        false_alarm_rate=0.1, base_confidence=0.9,
    ),
    SensorKind.RF_PASSIVE: _KindProfile(
        range_m=5000.0, pos_noise_m=20.0, vel_noise_ms=4.0,
        cross_range_factor=3.0, radial_vel_factor=2.5, condition_factor=1.0,
        false_alarm_rate=0.15, base_confidence=0.6,
    ),
}


@dataclass
class SimSensorSpec:
    """Configuration for one simulated sensor.

    position is the sensor location in ENU meters. range_m is the maximum
    detection range. fov_deg is the horizontal field of view in degrees, centered
    on bearing_deg (compass-style, 0 = North, 90 = East). A full 360 fov means
    omnidirectional. detection_prob is the peak per-target chance of a contact
    for a reference-RCS target at close range, scaled down by the RCS and range
    phenomenology. pos_noise_m and vel_noise_ms are the close-range Gaussian
    standard deviations applied per axis, grown with range at detection time.

    sensor_kind selects a phenomenology family and supplies any field left at its
    sentinel default. ref_rcs is the reference radar cross section in square
    meters that anchors the detection model. cross_range_factor scales tangential
    position noise versus radial. radial_vel_factor scales velocity noise.
    condition_factor in 0..1 degrades detection in poor conditions.
    false_alarm_rate is the mean number of spurious contacts per tick.
    """
    sensor_id: str
    position: Vec3 = (0.0, 0.0, 0.0)
    fov_deg: float = 360.0
    bearing_deg: float = 0.0
    detection_prob: float = 0.9
    sensor_kind: SensorKind = SensorKind.RADAR
    ref_rcs: float = 0.1
    # Sentinel -1.0 means "take the kind default". This keeps existing callers
    # that pass explicit values working and lets new callers lean on the kind.
    range_m: float = -1.0
    pos_noise_m: float = -1.0
    vel_noise_ms: float = -1.0
    cross_range_factor: float = -1.0
    radial_vel_factor: float = -1.0
    condition_factor: float = -1.0
    false_alarm_rate: float = -1.0
    base_confidence: float = -1.0

    def __post_init__(self) -> None:
        prof = _KIND_PROFILES[self.sensor_kind]
        if self.range_m < 0.0:
            self.range_m = prof.range_m
        if self.pos_noise_m < 0.0:
            self.pos_noise_m = prof.pos_noise_m
        if self.vel_noise_ms < 0.0:
            self.vel_noise_ms = prof.vel_noise_ms
        if self.cross_range_factor < 0.0:
            self.cross_range_factor = prof.cross_range_factor
        if self.radial_vel_factor < 0.0:
            self.radial_vel_factor = prof.radial_vel_factor
        if self.condition_factor < 0.0:
            self.condition_factor = prof.condition_factor
        if self.false_alarm_rate < 0.0:
            self.false_alarm_rate = prof.false_alarm_rate
        if self.base_confidence < 0.0:
            self.base_confidence = prof.base_confidence


def _bearing_deg(dx_east: float, dy_north: float) -> float:
    """Compass bearing in degrees from a sensor to a target in the EN plane.

    0 is North, 90 is East. Matches the fov_deg convention on SimSensorSpec.
    """
    return math.degrees(math.atan2(dx_east, dy_north)) % 360.0


def _angular_gap_deg(a: float, b: float) -> float:
    """Smallest absolute angle between two compass bearings, 0 to 180 degrees."""
    diff = abs(a - b) % 360.0
    return diff if diff <= 180.0 else 360.0 - diff


def _effective_rcs(target: TruthTarget) -> float:
    """Return the target RCS in square meters, defaulting when absent."""
    if target.size_rcs is None or target.size_rcs <= 0.0:
        return _DEFAULT_RCS_M2
    return target.size_rcs


class SimSensorSource(SensorSource):
    """Phenomenologically realistic multi-sensor source of Detection events.

    Builds Detections by sampling a truth callable, gating each target through
    every sensor's range and field of view, applying an RCS and range dependent
    detection probability, and adding range-grown noise. It also injects a small
    number of clutter detections per sensor per tick. Runs an async generator at
    a fixed rate until stop() is called.
    """

    def __init__(
        self,
        sensors: Sequence[SimSensorSpec],
        truth_fn: TruthFn,
        rate_hz: float = 10.0,
        rng: Optional[random.Random] = None,
        sensor_id: str = "sim",
    ) -> None:
        if not sensors:
            raise ValueError("SimSensorSource needs at least one SimSensorSpec")
        if rate_hz <= 0:
            raise ValueError("rate_hz must be positive")
        super().__init__(sensor_id)
        self._sensors = list(sensors)
        self._truth_fn = truth_fn
        self._interval = 1.0 / rate_hz
        self._rng = rng if rng is not None else random.Random()
        self._stop_event = asyncio.Event()
        self._seq = 0

    async def start(self) -> None:
        """Mark the source running. No external resource to open for a sim."""
        self._stop_event.clear()
        self._running = True
        logger.info(
            "SimSensorSource started with %d sensor(s) at %.1f Hz",
            len(self._sensors), 1.0 / self._interval,
        )

    async def stop(self) -> None:
        """Stop the source and cause stream() to finish cleanly."""
        self._running = False
        self._stop_event.set()
        logger.info("SimSensorSource stopped")

    async def stream(self) -> AsyncIterator[Detection]:
        """Yield Detections at the configured rate until stop() is called.

        Reads ground truth once per tick and emits every passing detection before
        sleeping. Returns cleanly when stop() sets the stop event. Real faults in
        the truth callable propagate as exceptions.
        """
        if not self._running:
            raise RuntimeError("stream() called before start()")
        while not self._stop_event.is_set():
            timestamp = now()
            targets = self._truth_fn()
            for detection in self._detections_for_tick(targets, timestamp):
                yield detection
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue

    def sample_once(self) -> list[Detection]:
        """Build one tick of detections now for a caller that owns the clock.

        The streaming stream() pumps detections on its own timer. A driver that
        runs a fixed tick loop, like the wargame runner, calls this once per tick
        instead so its clock stays authoritative. Reads ground truth once and
        returns every passing detection. Raises if called before start().
        """
        if not self._running:
            raise RuntimeError("sample_once() called before start()")
        timestamp = now()
        return self._detections_for_tick(self._truth_fn(), timestamp)

    def _detections_for_tick(
        self, targets: Sequence[TruthTarget], timestamp: float,
    ) -> list[Detection]:
        """Build every real and spurious detection for one tick."""
        out: list[Detection] = []
        for sensor in self._sensors:
            for target in targets:
                detection = self._observe(sensor, target, timestamp)
                if detection is not None:
                    out.append(detection)
            out.extend(self._false_alarms(sensor, timestamp))
        return out

    def _observe(
        self, sensor: SimSensorSpec, target: TruthTarget, timestamp: float,
    ) -> Optional[Detection]:
        """Return a noisy Detection if the sensor sees the target, else None."""
        if not self._in_range(sensor, target):
            return None
        if not self._in_fov(sensor, target):
            return None
        dist = self._distance(sensor, target)
        if self._rng.random() > self._detection_prob(sensor, target, dist):
            return None
        return self._build_detection(sensor, target, timestamp, dist)

    def _detection_prob(
        self, sensor: SimSensorSpec, target: TruthTarget, dist: float,
    ) -> float:
        """RCS and range dependent detection probability, radar-equation style.

        SNR rises with RCS and falls with range to the fourth power, normalized
        so a reference-RCS target at the reference range scores SNR 1. A logistic
        on log SNR turns that into a probability scaled by the sensor peak.
        """
        snr = self._snr(sensor, target, dist)
        logistic = 1.0 / (1.0 + math.exp(-_PD_STEEPNESS * math.log(max(snr, 1e-9))))
        peak = sensor.detection_prob * sensor.condition_factor
        return max(0.0, min(1.0, peak * logistic))

    def _snr(
        self, sensor: SimSensorSpec, target: TruthTarget, dist: float,
    ) -> float:
        """Normalized signal to noise ratio for one observation.

        Proportional to RCS over range^4, scaled to 1 at the reference point so
        the logistic and confidence read in intuitive units.
        """
        rcs = _effective_rcs(target)
        ref = max(dist, 1.0) ** 4
        norm = (rcs / sensor.ref_rcs) * (_REF_RANGE_M ** 4 / ref)
        return norm

    def _build_detection(
        self, sensor: SimSensorSpec, target: TruthTarget, timestamp: float,
        dist: float,
    ) -> Detection:
        """Apply range-grown anisotropic noise and stamp a Detection."""
        self._seq += 1
        scale = self._range_noise_scale(sensor, dist)
        pos_sd = sensor.pos_noise_m * scale * sensor.cross_range_factor
        vel_sd = sensor.vel_noise_ms * scale * sensor.radial_vel_factor
        noisy_pos = self._add_noise(target.position, pos_sd)
        noisy_vel = self._add_noise(target.velocity, vel_sd)
        confidence = self._confidence(sensor, target, dist)
        return Detection(
            id=f"{sensor.sensor_id}-{self._seq}",
            timestamp=timestamp,
            position=noisy_pos,
            velocity=noisy_vel,
            confidence=confidence,
            sensor_id=sensor.sensor_id,
            size_rcs=target.size_rcs,
        )

    def _range_noise_scale(self, sensor: SimSensorSpec, dist: float) -> float:
        """Noise growth factor, 1 at the reference range and rising with range."""
        return max(0.25, dist / _REF_RANGE_M)

    def _false_alarms(
        self, sensor: SimSensorSpec, timestamp: float,
    ) -> list[Detection]:
        """Generate a Poisson-like batch of low-confidence clutter contacts."""
        count = self._poisson(sensor.false_alarm_rate)
        return [self._false_alarm(sensor, timestamp) for _ in range(count)]

    def _false_alarm(self, sensor: SimSensorSpec, timestamp: float) -> Detection:
        """One spurious contact at a random point inside the sensor coverage."""
        self._seq += 1
        rng = self._rng
        radius = sensor.range_m * math.sqrt(rng.random())
        half = sensor.fov_deg / 2.0 if sensor.fov_deg < 360.0 else 180.0
        bearing = sensor.bearing_deg + rng.uniform(-half, half)
        rad = math.radians(bearing)
        px = sensor.position[0] + radius * math.sin(rad)
        py = sensor.position[1] + radius * math.cos(rad)
        pz = sensor.position[2] + rng.uniform(-50.0, 200.0)
        vel = (rng.gauss(0.0, 5.0), rng.gauss(0.0, 5.0), rng.gauss(0.0, 1.0))
        return Detection(
            id=f"{sensor.sensor_id}-fa-{self._seq}",
            timestamp=timestamp,
            position=(px, py, pz),
            velocity=vel,
            confidence=rng.uniform(0.02, 0.15),
            sensor_id=sensor.sensor_id,
            size_rcs=None,
        )

    def _poisson(self, mean: float) -> int:
        """Draw a Poisson count by the Knuth algorithm from the injected rng."""
        if mean <= 0.0:
            return 0
        limit = math.exp(-mean)
        k = 0
        product = 1.0
        while True:
            product *= self._rng.random()
            if product <= limit:
                return k
            k += 1

    def _distance(self, sensor: SimSensorSpec, target: TruthTarget) -> float:
        """Straight-line distance from sensor to target in meters."""
        dx = target.position[0] - sensor.position[0]
        dy = target.position[1] - sensor.position[1]
        dz = target.position[2] - sensor.position[2]
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def _in_range(self, sensor: SimSensorSpec, target: TruthTarget) -> bool:
        """True when the target lies within the sensor's range sphere."""
        dx = target.position[0] - sensor.position[0]
        dy = target.position[1] - sensor.position[1]
        dz = target.position[2] - sensor.position[2]
        return (dx * dx + dy * dy + dz * dz) <= sensor.range_m * sensor.range_m

    def _in_fov(self, sensor: SimSensorSpec, target: TruthTarget) -> bool:
        """True when the target falls inside the sensor's horizontal field of view."""
        if sensor.fov_deg >= 360.0:
            return True
        dx = target.position[0] - sensor.position[0]
        dy = target.position[1] - sensor.position[1]
        if dx == 0.0 and dy == 0.0:
            return True
        bearing = _bearing_deg(dx, dy)
        return _angular_gap_deg(bearing, sensor.bearing_deg) <= sensor.fov_deg / 2.0

    def _add_noise(self, vec: Vec3, stddev: float) -> Vec3:
        """Add independent zero-mean Gaussian noise to each axis of a vector."""
        return (
            vec[0] + self._rng.gauss(0.0, stddev),
            vec[1] + self._rng.gauss(0.0, stddev),
            vec[2] + self._rng.gauss(0.0, stddev),
        )

    def _confidence(
        self, sensor: SimSensorSpec, target: TruthTarget, dist: float,
    ) -> float:
        """Confidence from SNR (RCS and range), clamped to 0..1.

        A logistic on log SNR maps a strong return near 1 and a marginal return
        near 0, scaled by the sensor base confidence. This replaces the old
        distance-only falloff so a small far target reads as low confidence even
        when detected.
        """
        snr = self._snr(sensor, target, dist)
        logistic = 1.0 / (1.0 + math.exp(-math.log(max(snr, 1e-9))))
        value = sensor.base_confidence * logistic
        return max(0.0, min(1.0, value))
