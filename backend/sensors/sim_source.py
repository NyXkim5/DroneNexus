"""
SimSensorSource — a simulated SensorSource for wargaming and fusion tests.

This adapter samples ground truth from an attacker simulation and emits noisy
Detection events. It models one or more simulated sensors. Each sensor sits at a
fixed position, sees out to a range, covers a horizontal field of view, drops
some contacts by a detection probability, and adds Gaussian noise to position
and velocity. The result is a realistic, imperfect picture for the fusion engine
to clean up.

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
from typing import AsyncIterator, Callable, Optional, Sequence

from csontology import Detection, Vec3, now
from sensors.base import SensorSource

logger = logging.getLogger("overwatch.sensors")


@dataclass(frozen=True)
class TruthTarget:
    """One ground-truth object the attacker exposes to the sensor layer.

    Immutable snapshot of a real target at one instant. The sensor reads these
    through the truth callable and turns in-range ones into noisy Detections.
    Position and velocity are ENU meters and m/s about the site origin.
    """
    id: str
    position: Vec3
    velocity: Vec3
    size_rcs: Optional[float] = None


# A function the source calls each tick to read current ground truth.
TruthFn = Callable[[], Sequence[TruthTarget]]


@dataclass
class SimSensorSpec:
    """Configuration for one simulated sensor.

    position is the sensor location in ENU meters. range_m is the maximum
    detection range. fov_deg is the horizontal field of view in degrees, centered
    on bearing_deg (compass-style, 0 = North, 90 = East). A full 360 fov means
    omnidirectional. detection_prob is the per-target chance of a contact when in
    range and in view. pos_noise_m and vel_noise_ms are Gaussian standard
    deviations applied per axis to position and velocity.
    """
    sensor_id: str
    position: Vec3 = (0.0, 0.0, 0.0)
    range_m: float = 5000.0
    fov_deg: float = 360.0
    bearing_deg: float = 0.0
    detection_prob: float = 0.9
    pos_noise_m: float = 4.0
    vel_noise_ms: float = 1.0
    base_confidence: float = 0.85


def _bearing_deg(dx_east: float, dy_north: float) -> float:
    """Compass bearing in degrees from a sensor to a target in the EN plane.

    0 is North, 90 is East. Matches the fov_deg convention on SimSensorSpec.
    """
    return math.degrees(math.atan2(dx_east, dy_north)) % 360.0


def _angular_gap_deg(a: float, b: float) -> float:
    """Smallest absolute angle between two compass bearings, 0 to 180 degrees."""
    diff = abs(a - b) % 360.0
    return diff if diff <= 180.0 else 360.0 - diff


class SimSensorSource(SensorSource):
    """Simulated multi-sensor source of noisy Detection events.

    Builds Detections by sampling a truth callable, filtering each target through
    every sensor's range and field of view, dropping some by detection
    probability, and adding Gaussian noise. Runs an async generator at a fixed
    rate until stop() is called.
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
        """Yield noisy Detections at the configured rate until stop() is called.

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
        """Build every detection from every sensor for one tick."""
        out: list[Detection] = []
        for sensor in self._sensors:
            for target in targets:
                detection = self._observe(sensor, target, timestamp)
                if detection is not None:
                    out.append(detection)
        return out

    def _observe(
        self, sensor: SimSensorSpec, target: TruthTarget, timestamp: float,
    ) -> Optional[Detection]:
        """Return a noisy Detection if the sensor sees the target, else None."""
        if not self._in_range(sensor, target):
            return None
        if not self._in_fov(sensor, target):
            return None
        if self._rng.random() > sensor.detection_prob:
            return None
        return self._build_detection(sensor, target, timestamp)

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

    def _build_detection(
        self, sensor: SimSensorSpec, target: TruthTarget, timestamp: float,
    ) -> Detection:
        """Apply Gaussian noise and stamp a Detection from one observation."""
        self._seq += 1
        noisy_pos = self._add_noise(target.position, sensor.pos_noise_m)
        noisy_vel = self._add_noise(target.velocity, sensor.vel_noise_ms)
        confidence = self._confidence(sensor, target)
        return Detection(
            id=f"{sensor.sensor_id}-{self._seq}",
            timestamp=timestamp,
            position=noisy_pos,
            velocity=noisy_vel,
            confidence=confidence,
            sensor_id=sensor.sensor_id,
            size_rcs=target.size_rcs,
        )

    def _add_noise(self, vec: Vec3, stddev: float) -> Vec3:
        """Add independent zero-mean Gaussian noise to each axis of a vector."""
        return (
            vec[0] + self._rng.gauss(0.0, stddev),
            vec[1] + self._rng.gauss(0.0, stddev),
            vec[2] + self._rng.gauss(0.0, stddev),
        )

    def _confidence(self, sensor: SimSensorSpec, target: TruthTarget) -> float:
        """Confidence that falls off with range, clamped to 0..1."""
        dx = target.position[0] - sensor.position[0]
        dy = target.position[1] - sensor.position[1]
        dz = target.position[2] - sensor.position[2]
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        falloff = 1.0 - (dist / sensor.range_m) if sensor.range_m > 0 else 0.0
        value = sensor.base_confidence * max(0.0, falloff)
        return max(0.0, min(1.0, value))
