"""
ClutterGenerator — realistic non-hostile contacts for wargame sensor feeds.

Models the civilian and environmental background a real sensor suite sees: birds,
commercial drones, RC hobbyists, weather returns, and ground vehicles. Injecting
this clutter into sensor feeds forces the fusion engine and threat classifier to
distinguish genuine threats from benign contacts, matching the real operational
environment.

Sensor phenomenology per source type:
  Birds:            RCS ~0.01 m², no RF, Levy-flight path, 5–15 m/s
  Commercial drone: RCS ~0.1 m², RF emitting, linear waypoint path, 10–20 m/s
  RC hobbyist:      RCS ~0.05 m², RF emitting, local area, 5–30 m/s
  Weather return:   random position near sensor, no velocity, radar only
  Ground vehicle:   RCS ~10 m², ground level (z=0), no RF

Coordinate frame is ENU meters about the site origin, matching csontology.
"""
from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from csontology import Detection, Vec3


# ---- Domain constants ----

_BIRD_RCS = 0.01       # m²
_DRONE_RCS = 0.1       # m²
_RC_RCS = 0.05         # m²
_VEHICLE_RCS = 10.0    # m²

_BIRD_SPEED_MIN = 5.0
_BIRD_SPEED_MAX = 15.0
_DRONE_SPEED_MIN = 10.0
_DRONE_SPEED_MAX = 20.0
_RC_SPEED_MIN = 5.0
_RC_SPEED_MAX = 30.0
_VEHICLE_SPEED = 10.0  # m/s ground speed

# Levy-flight turn probability per tick (birds mostly fly straight).
_LEVY_TURN_PROB = 0.08

# How close a clutter source must be to a sensor for it to potentially detect.
_MAX_SENSOR_RANGE_M = 5000.0

# Detection confidence assigned to clutter contacts (low, to aid fusion rejection).
_CLUTTER_CONFIDENCE = 0.3


@dataclass
class ClutterSource:
    """One non-hostile contact in the airspace or on the ground."""

    source_type: str          # "bird", "commercial_drone", "rc_hobbyist", "weather", "ground_vehicle"
    position: Vec3
    velocity: Vec3
    rcs_m2: float             # radar cross section in m²
    rf_emitting: bool         # does it emit RF?
    visual_signature: str     # what it looks like to EO/IR

    # Internal state for path following.
    _waypoint: Optional[Vec3] = field(default=None, repr=False)
    _id: str = field(default_factory=lambda: str(uuid.uuid4())[:8], repr=False)


@dataclass
class ClutterConfig:
    """Density and rate parameters for clutter generation."""

    bird_density_per_km2: float = 5.0
    commercial_drone_rate_per_hr: float = 2.0
    rc_hobbyist_rate_per_hr: float = 0.5
    weather_clutter_probability: float = 0.1
    bounds_m: float = 5000.0


def _random_position(rng: random.Random, bounds_m: float, altitude_m: float) -> Vec3:
    """Random ENU position within a square bounds at the given altitude."""
    x = rng.uniform(-bounds_m, bounds_m)
    y = rng.uniform(-bounds_m, bounds_m)
    return (x, y, altitude_m)


def _random_velocity(rng: random.Random, speed_min: float, speed_max: float) -> Vec3:
    """Random horizontal velocity vector at a given speed range."""
    speed = rng.uniform(speed_min, speed_max)
    angle = rng.uniform(0.0, 2.0 * math.pi)
    vx = speed * math.cos(angle)
    vy = speed * math.sin(angle)
    vz = rng.uniform(-0.5, 0.5)
    return (vx, vy, vz)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class ClutterGenerator:
    """Generates realistic non-hostile contacts for wargame sensor feeds."""

    def __init__(self, config: Optional[ClutterConfig] = None, seed: int = 42) -> None:
        self._config = config or ClutterConfig()
        self._rng = random.Random(seed)
        self._sources: List[ClutterSource] = []
        self._tick_count: int = 0

    def initialize(self) -> None:
        """Spawn initial clutter population based on config densities."""
        self._sources.clear()
        self._spawn_birds()
        self._spawn_commercial_drones()
        self._spawn_rc_hobbyists()

    def _spawn_birds(self) -> None:
        """Spawn the initial bird population from areal density."""
        area_km2 = (2.0 * self._config.bounds_m / 1000.0) ** 2
        count = int(self._config.bird_density_per_km2 * area_km2)
        for _ in range(count):
            self._sources.append(self._make_bird())

    def _spawn_commercial_drones(self) -> None:
        """Spawn an initial cohort of commercial drones proportional to the hourly rate."""
        count = int(self._config.commercial_drone_rate_per_hr)
        for _ in range(count):
            self._sources.append(self._make_commercial_drone())

    def _spawn_rc_hobbyists(self) -> None:
        """Spawn RC hobbyists based on the configured rate."""
        count = int(self._config.rc_hobbyist_rate_per_hr)
        for _ in range(count):
            self._sources.append(self._make_rc_hobbyist())

    def _make_bird(self) -> ClutterSource:
        alt = self._rng.uniform(10.0, 300.0)
        pos = _random_position(self._rng, self._config.bounds_m, alt)
        vel = _random_velocity(self._rng, _BIRD_SPEED_MIN, _BIRD_SPEED_MAX)
        return ClutterSource(
            source_type="bird",
            position=pos,
            velocity=vel,
            rcs_m2=_BIRD_RCS,
            rf_emitting=False,
            visual_signature="small_flapping",
        )

    def _make_commercial_drone(self) -> ClutterSource:
        alt = self._rng.uniform(30.0, 120.0)
        pos = _random_position(self._rng, self._config.bounds_m * 0.8, alt)
        vel = _random_velocity(self._rng, _DRONE_SPEED_MIN, _DRONE_SPEED_MAX)
        wp = _random_position(self._rng, self._config.bounds_m * 0.8, alt)
        src = ClutterSource(
            source_type="commercial_drone",
            position=pos,
            velocity=vel,
            rcs_m2=_DRONE_RCS,
            rf_emitting=True,
            visual_signature="quadrotor_steady",
        )
        src._waypoint = wp
        return src

    def _make_rc_hobbyist(self) -> ClutterSource:
        alt = self._rng.uniform(5.0, 80.0)
        # RC hobbyists stay in a local area — within 500 m of a random hub.
        hub_x = self._rng.uniform(-500.0, 500.0)
        hub_y = self._rng.uniform(-500.0, 500.0)
        pos = (hub_x + self._rng.uniform(-50.0, 50.0), hub_y + self._rng.uniform(-50.0, 50.0), alt)
        vel = _random_velocity(self._rng, _RC_SPEED_MIN, _RC_SPEED_MAX)
        return ClutterSource(
            source_type="rc_hobbyist",
            position=pos,
            velocity=vel,
            rcs_m2=_RC_RCS,
            rf_emitting=True,
            visual_signature="small_rc_craft",
        )

    def _make_weather_return(self, sensor_position: Vec3) -> ClutterSource:
        """Create a transient weather return near a given sensor."""
        offset_x = self._rng.uniform(-500.0, 500.0)
        offset_y = self._rng.uniform(-500.0, 500.0)
        pos: Vec3 = (
            sensor_position[0] + offset_x,
            sensor_position[1] + offset_y,
            self._rng.uniform(100.0, 2000.0),
        )
        return ClutterSource(
            source_type="weather",
            position=pos,
            velocity=(0.0, 0.0, 0.0),
            rcs_m2=self._rng.uniform(0.5, 5.0),
            rf_emitting=False,
            visual_signature="none",
        )

    def _make_ground_vehicle(self) -> ClutterSource:
        x = self._rng.uniform(-self._config.bounds_m, self._config.bounds_m)
        y = self._rng.uniform(-self._config.bounds_m, self._config.bounds_m)
        angle = self._rng.uniform(0.0, 2.0 * math.pi)
        speed = _VEHICLE_SPEED
        vel: Vec3 = (speed * math.cos(angle), speed * math.sin(angle), 0.0)
        return ClutterSource(
            source_type="ground_vehicle",
            position=(x, y, 0.0),
            velocity=vel,
            rcs_m2=_VEHICLE_RCS,
            rf_emitting=False,
            visual_signature="wheeled_vehicle",
        )

    def advance(self, dt: float) -> None:
        """Move existing clutter, spawn/despawn based on rates."""
        self._tick_count += 1
        updated: List[ClutterSource] = []
        for src in self._sources:
            if src.source_type == "weather":
                # Weather returns are transient — expire them after one tick.
                continue
            moved = self._move_source(src, dt)
            if self._in_bounds(moved.position):
                updated.append(moved)
        self._sources = updated
        self._maybe_spawn_new(dt)

    def _move_source(self, src: ClutterSource, dt: float) -> ClutterSource:
        """Advance one source by dt seconds, applying type-specific motion."""
        if src.source_type == "bird":
            vel = self._levy_step(src.velocity)
        elif src.source_type == "commercial_drone":
            vel = self._waypoint_step(src, dt)
        else:
            vel = src.velocity

        x = src.position[0] + vel[0] * dt
        y = src.position[1] + vel[1] * dt
        z = _clamp(src.position[2] + vel[2] * dt, 0.0, 3000.0)
        src.position = (x, y, z)
        src.velocity = vel
        return src

    def _levy_step(self, velocity: Vec3) -> Vec3:
        """Apply a Levy-flight perturbation to a bird velocity.

        Most ticks the bird flies straight. Occasionally it makes a sharp turn.
        This mimics the intermittent, erratic flight of real birds and makes them
        harder to track than a steady commercial drone.
        """
        if self._rng.random() < _LEVY_TURN_PROB:
            angle = self._rng.uniform(0.0, 2.0 * math.pi)
            speed = math.hypot(velocity[0], velocity[1])
            speed = _clamp(speed, _BIRD_SPEED_MIN, _BIRD_SPEED_MAX)
            vx = speed * math.cos(angle)
            vy = speed * math.sin(angle)
            vz = self._rng.uniform(-0.5, 0.5)
            return (vx, vy, vz)
        return velocity

    def _waypoint_step(self, src: ClutterSource, dt: float) -> Vec3:
        """Steer a commercial drone toward its current waypoint.

        When it arrives within 20 m, pick a new random waypoint. This creates
        the linear, predictable path that distinguishes commercial drones from
        birds. The behavior is intentionally simple — commercial drones do not
        evade or maneuver.
        """
        wp = src._waypoint
        if wp is None:
            alt = src.position[2]
            src._waypoint = _random_position(self._rng, self._config.bounds_m * 0.8, alt)
            return src.velocity

        dx = wp[0] - src.position[0]
        dy = wp[1] - src.position[1]
        dist = math.hypot(dx, dy)
        if dist < 20.0:
            alt = src.position[2]
            src._waypoint = _random_position(self._rng, self._config.bounds_m * 0.8, alt)
            return src.velocity

        speed = math.hypot(src.velocity[0], src.velocity[1])
        speed = _clamp(speed, _DRONE_SPEED_MIN, _DRONE_SPEED_MAX)
        vx = (dx / dist) * speed
        vy = (dy / dist) * speed
        return (vx, vy, 0.0)

    def _in_bounds(self, pos: Vec3) -> bool:
        b = self._config.bounds_m * 1.5
        return abs(pos[0]) <= b and abs(pos[1]) <= b

    def _maybe_spawn_new(self, dt: float) -> None:
        """Probabilistically spawn new commercial drones and RC hobbyists each tick."""
        drone_prob = (self._config.commercial_drone_rate_per_hr / 3600.0) * dt
        if self._rng.random() < drone_prob:
            self._sources.append(self._make_commercial_drone())

        rc_prob = (self._config.rc_hobbyist_rate_per_hr / 3600.0) * dt
        if self._rng.random() < rc_prob:
            self._sources.append(self._make_rc_hobbyist())

    def get_detections(self, sensor_kind: str, sensor_position: Vec3) -> List[Detection]:
        """Generate sensor detections from clutter sources.

        Each sensor type has different sensitivity:
        - Radar: sees all sources with RCS > 0; weather returns are radar only.
        - EO/IR: sees airborne sources with a visual signature; no weather.
        - RF/passive: sees only RF-emitting sources; no weather, no birds.

        Detection probability is a simple range gate — any clutter within
        _MAX_SENSOR_RANGE_M is potentially detected at _CLUTTER_CONFIDENCE. We
        do not model false negatives per source here, as the fusion engine handles
        clutter rejection from low-confidence tracks.
        """
        import time

        detections: List[Detection] = []
        ts = time.time()

        sources_to_check: List[ClutterSource] = list(self._sources)

        # Weather is radar-only and generated per-call.
        if sensor_kind == "radar" and self._rng.random() < self._config.weather_clutter_probability:
            sources_to_check.append(self._make_weather_return(sensor_position))

        for src in sources_to_check:
            if not self._sensor_can_see(sensor_kind, src):
                continue
            dist = _dist3(src.position, sensor_position)
            if dist > _MAX_SENSOR_RANGE_M:
                continue
            det_id = f"clutter-{src.source_type}-{src._id}-{self._tick_count}"
            detections.append(
                Detection(
                    id=det_id,
                    timestamp=ts,
                    position=src.position,
                    velocity=src.velocity,
                    confidence=_CLUTTER_CONFIDENCE,
                    sensor_id=f"clutter_{sensor_kind}",
                    size_rcs=src.rcs_m2,
                )
            )

        return detections

    def _sensor_can_see(self, sensor_kind: str, src: ClutterSource) -> bool:
        """Return True if this sensor type can detect this clutter source.

        Weather sources are radar-only returns. They are injected into
        sources_to_check only for radar calls, so this gate must pass them
        through for radar rather than block them.
        """
        if sensor_kind == "radar":
            # Radar sees everything with a non-zero RCS, including weather.
            return True
        if sensor_kind in ("eo_ir", "eoir"):
            # EO/IR sees airborne sources with a real visual signature, not weather.
            return src.source_type not in ("weather",) and src.visual_signature != "none"
        if sensor_kind in ("rf", "passive"):
            # RF-passive only hears emitters. Weather does not emit.
            return src.rf_emitting
        # Unknown sensor type: see nothing.
        return False

    @property
    def active_sources(self) -> List[ClutterSource]:
        return list(self._sources)


def _dist3(a: Vec3, b: Vec3) -> float:
    """Euclidean distance between two Vec3 points."""
    return math.sqrt(
        (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
    )
