"""
Hostile swarm generator — the red force that drives the whole pipeline.

This module spawns N attacker drones in the shared ENU frame and flies them
toward the defended Site each tick. It mirrors the integration style of
simulation/mock_drone.py but works in ENU meters from csontology rather than
lat/lon, so the sensor layer can sample ground truth directly.

The swarm exposes ground truth through get_truth(). The sensor layer reads that,
adds noise, and emits Detection events. The attacker never touches sensors,
tracks, or threats. It only knows positions, velocities, and cost.

Behaviors
---------
SATURATION  every drone converges on the site from all axes at once.
WAVES       drones split into staggered groups that launch on a delay.
DECOY       a mix of cheap decoys and a smaller set of real, costly threats.
PROBE       a small leading element advances while the rest hold and observe.

All positions and velocities are ENU meters and m/s about the site origin.
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from csontology import SwarmIntent, Vec3, now

logger = logging.getLogger("overwatch.attacker")

# Default per-drone cost in dollars. A real attacker is cheap by design.
DEFAULT_UNIT_COST = 500.0
# A decoy is an order of magnitude cheaper than a real threat.
DECOY_UNIT_COST = 50.0
# Nominal cruise speed of an attacker drone in m/s.
CRUISE_SPEED_MPS = 18.0
# Ring radius the swarm spawns on, in meters from the site.
SPAWN_RADIUS_M = 3000.0
# Distance from the site at which a drone is considered to have arrived.
ARRIVAL_RADIUS_M = 25.0
# Fraction of a DECOY swarm that are real threats. The rest are decoys.
REAL_THREAT_FRACTION = 0.25
# Fraction of a PROBE swarm that forms the leading element.
PROBE_LEAD_FRACTION = 0.15
# Seconds between successive WAVES launches.
WAVE_INTERVAL_S = 8.0
# Number of staggered groups in a WAVES attack.
WAVE_GROUP_COUNT = 4


@dataclass
class AttackerDrone:
    """One hostile drone with ENU kinematics and a dollar cost.

    is_decoy marks cheap throwaway airframes used to draw fire. launch_time is
    the wall-clock second this drone starts moving. Before it launches the drone
    holds its spawn position with zero velocity.
    """
    id: str
    position: Vec3
    velocity: Vec3
    unit_cost: float
    is_decoy: bool = False
    launch_time: float = 0.0
    arrived: bool = False
    speed_mps: float = CRUISE_SPEED_MPS


@dataclass
class TruthDrone:
    """A read-only snapshot of one drone's ground truth for the sensor layer."""
    id: str
    position: Vec3
    velocity: Vec3
    is_decoy: bool
    unit_cost: float


@dataclass
class HostileSwarm:
    """A coordinated red force of attacker drones converging on a Site.

    Construct with a behavior intent, a member count, and the site position in
    ENU meters. Call advance(dt) each tick to fly the swarm toward the site.
    Call get_truth() to read ground-truth kinematics for the sensor layer.
    """

    intent: SwarmIntent
    count: int
    site_position: Vec3 = (0.0, 0.0, 0.0)
    unit_cost: float = DEFAULT_UNIT_COST
    seed: Optional[int] = None
    swarm_id: str = "RED-1"
    spawn_radius_m: float = SPAWN_RADIUS_M
    drones: List[AttackerDrone] = field(default_factory=list)
    first_seen: float = field(default_factory=now)
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not 10 <= self.count <= 1000:
            raise ValueError(f"count must be in 10..1000, got {self.count}")
        self._rng = random.Random(self.seed)
        self._spawn()

    # ---- spawning ----

    def _spawn(self) -> None:
        """Build the member drones for the configured behavior."""
        builders = {
            SwarmIntent.SATURATION: self._spawn_saturation,
            SwarmIntent.WAVES: self._spawn_waves,
            SwarmIntent.DECOY: self._spawn_decoy,
            SwarmIntent.PROBE: self._spawn_probe,
        }
        builder = builders.get(self.intent)
        if builder is None:
            raise ValueError(f"unsupported intent: {self.intent}")
        self.drones = builder()
        logger.info(
            "Spawned %s hostile drones, intent=%s, id=%s",
            len(self.drones), self.intent.value, self.swarm_id,
        )

    def _ring_position(self, index: int) -> Vec3:
        """Place a drone on a spawn ring around the site at a spread bearing."""
        bearing = (index / max(1, self.count)) * 2.0 * math.pi
        bearing += self._rng.uniform(-0.05, 0.05)
        radius = self.spawn_radius_m * self._rng.uniform(0.9, 1.1)
        sx, sy, sz = self.site_position
        x = sx + radius * math.cos(bearing)
        y = sy + radius * math.sin(bearing)
        z = self._rng.uniform(40.0, 160.0)
        return x, y, z

    def _make_drone(
        self, index: int, launch_time: float, is_decoy: bool, cost: float,
    ) -> AttackerDrone:
        """Construct one attacker drone holding at its spawn ring position."""
        return AttackerDrone(
            id=f"{self.swarm_id}-{index:04d}",
            position=self._ring_position(index),
            velocity=(0.0, 0.0, 0.0),
            unit_cost=cost,
            is_decoy=is_decoy,
            launch_time=launch_time,
        )

    def _spawn_saturation(self) -> List[AttackerDrone]:
        """All drones launch at once from every bearing."""
        start = self.first_seen
        return [
            self._make_drone(i, start, is_decoy=False, cost=self.unit_cost)
            for i in range(self.count)
        ]

    def _spawn_waves(self) -> List[AttackerDrone]:
        """Drones split into groups that launch on staggered delays."""
        start = self.first_seen
        drones: List[AttackerDrone] = []
        for i in range(self.count):
            group = i % WAVE_GROUP_COUNT
            launch = start + group * WAVE_INTERVAL_S
            drones.append(
                self._make_drone(i, launch, is_decoy=False, cost=self.unit_cost)
            )
        return drones

    def _spawn_decoy(self) -> List[AttackerDrone]:
        """A small set of real threats hidden among many cheap decoys."""
        start = self.first_seen
        real_count = max(1, round(self.count * REAL_THREAT_FRACTION))
        real_indices = set(self._rng.sample(range(self.count), real_count))
        drones: List[AttackerDrone] = []
        for i in range(self.count):
            is_decoy = i not in real_indices
            cost = DECOY_UNIT_COST if is_decoy else self.unit_cost
            drones.append(self._make_drone(i, start, is_decoy=is_decoy, cost=cost))
        return drones

    def _spawn_probe(self) -> List[AttackerDrone]:
        """A small lead element advances while the rest hold and observe.

        Holding drones get a launch time far in the future so advance() leaves
        them parked on the spawn ring while the lead element closes the site.
        """
        start = self.first_seen
        lead_count = max(1, round(self.count * PROBE_LEAD_FRACTION))
        hold_launch = start + 1e9
        drones: List[AttackerDrone] = []
        for i in range(self.count):
            launch = start if i < lead_count else hold_launch
            drones.append(
                self._make_drone(i, launch, is_decoy=False, cost=self.unit_cost)
            )
        return drones

    # ---- per-tick advance ----

    def advance(self, dt: float) -> None:
        """Fly every launched, unarrived drone toward the site for dt seconds."""
        t = now()
        for drone in self.drones:
            self._advance_one(drone, dt, t)

    def _advance_one(self, drone: AttackerDrone, dt: float, t: float) -> None:
        """Move one drone toward the site, or hold it if not yet launched."""
        if drone.arrived or t < drone.launch_time:
            drone.velocity = (0.0, 0.0, 0.0)
            return
        to_site = self._vector_to_site(drone.position)
        dist = _magnitude(to_site)
        if dist <= ARRIVAL_RADIUS_M:
            drone.arrived = True
            drone.position = self.site_position
            drone.velocity = (0.0, 0.0, 0.0)
            return
        drone.velocity = _scale(to_site, drone.speed_mps / dist)
        step = min(dist, drone.speed_mps * dt)
        drone.position = _add(drone.position, _scale(to_site, step / dist))

    def _vector_to_site(self, position: Vec3) -> Vec3:
        """Return the ENU vector from a position to the site center."""
        return _subtract(self.site_position, position)

    # ---- ground-truth readout ----

    def get_truth(self) -> List[TruthDrone]:
        """Return ground-truth snapshots for the sensor layer to sample."""
        return [
            TruthDrone(
                id=d.id,
                position=d.position,
                velocity=d.velocity,
                is_decoy=d.is_decoy,
                unit_cost=d.unit_cost,
            )
            for d in self.drones
        ]

    def centroid(self) -> Vec3:
        """Return the mean position of all member drones in ENU meters."""
        if not self.drones:
            return self.site_position
        n = float(len(self.drones))
        sx = sum(d.position[0] for d in self.drones) / n
        sy = sum(d.position[1] for d in self.drones) / n
        sz = sum(d.position[2] for d in self.drones) / n
        return sx, sy, sz

    def total_cost(self) -> float:
        """Return the summed dollar cost of the whole red force."""
        return sum(d.unit_cost for d in self.drones)

    def arrived_count(self) -> int:
        """Return how many drones have reached the site."""
        return sum(1 for d in self.drones if d.arrived)


# ---- Vec3 math helpers ----

def _add(a: Vec3, b: Vec3) -> Vec3:
    return a[0] + b[0], a[1] + b[1], a[2] + b[2]


def _subtract(a: Vec3, b: Vec3) -> Vec3:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _scale(a: Vec3, k: float) -> Vec3:
    return a[0] * k, a[1] * k, a[2] * k


def _magnitude(a: Vec3) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
