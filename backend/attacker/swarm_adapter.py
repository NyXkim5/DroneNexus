"""
FlockingSwarmAdapter — bridges HostileSwarm with Reynolds flocking dynamics.

The adapter wraps an existing HostileSwarm and layers Reynolds boid behavior
on top of the swarm's per-tick advance. Each AttackerDrone maps to a BoidState.
After flocking forces are integrated, the adapter writes the new position and
velocity back to the underlying drone so the rest of the pipeline (sensor layer,
fusion, threat) sees the updated kinematics without any other changes.

The adapter does not replace evasive steering. It runs flocking forces in
addition to the swarm's existing advance() call, so separation, cohesion, and
alignment between drones emerge on top of the attacker's goal-seeking behavior.
Drones that are already arrived or killed are skipped every tick.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from attacker.flocking import (
    BoidState,
    FlockingParams,
    advance_boid,
    compute_flocking_forces,
)
from attacker.hostile_swarm import AttackerDrone, HostileSwarm

Vec3 = tuple[float, float, float]


def _magnitude(a: Vec3) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


@dataclass
class FlockingSwarmAdapter:
    """Wraps an existing HostileSwarm with Reynolds flocking dynamics.

    Call initialize_boids() once after construction, then replace calls to
    swarm.advance(dt) with adapter.advance_all(dt, target). The adapter calls
    swarm.advance() internally so all existing attacker behaviors still run,
    then overlays flocking forces on drones that are active.
    """

    _swarm: HostileSwarm
    _params: FlockingParams = field(default_factory=FlockingParams)
    _boids: Dict[str, BoidState] = field(default_factory=dict)

    def __init__(
        self, swarm: HostileSwarm, params: Optional[FlockingParams] = None
    ) -> None:
        self._swarm = swarm
        self._params = params or FlockingParams()
        self._boids = {}

    def initialize_boids(self) -> None:
        """Create a BoidState for each drone from current swarm positions.

        Call once before the first advance_all(). Safe to call again to reset
        boid states to match the current swarm positions, e.g. after a scenario
        restart.
        """
        self._boids = {}
        for drone in self._swarm.drones:
            self._boids[drone.id] = BoidState(
                position=drone.position,
                velocity=drone.velocity,
            )

    def advance_all(self, dt: float, target: Vec3) -> None:
        """Advance the swarm one tick with flocking dynamics.

        Steps per tick:
        1. Run the underlying swarm's normal advance() so evasive steering,
           wave timing, and reaction logic all execute.
        2. For each active drone, sync the BoidState from the updated position
           and velocity produced by the swarm's own advance.
        3. Compute flocking neighbors within perception_radius.
        4. Compute flocking forces (separation, cohesion, alignment, seek).
        5. Set the BoidState acceleration and integrate with advance_boid().
        6. Write the flocking-adjusted position and velocity back to the drone.

        Arrived or killed drones are skipped and their boid states are kept
        stale so they do not influence neighbors.
        """
        self._swarm.advance(dt)

        active = [d for d in self._swarm.drones if not d.arrived]

        for drone in active:
            boid = self._boids.get(drone.id)
            if boid is None:
                boid = BoidState(position=drone.position, velocity=drone.velocity)
                self._boids[drone.id] = boid
            else:
                boid.position = drone.position
                boid.velocity = drone.velocity

        active_ids = {d.id for d in active}
        active_boids = [self._boids[d.id] for d in active if d.id in self._boids]

        for drone in active:
            boid = self._boids.get(drone.id)
            if boid is None:
                continue

            neighbors = self._neighbors(boid, active_boids, drone.id)
            force = compute_flocking_forces(boid, neighbors, target, self._params)

            boid_with_accel = BoidState(
                position=boid.position,
                velocity=boid.velocity,
                acceleration=force,
            )
            updated = advance_boid(boid_with_accel, dt, self._params)
            self._boids[drone.id] = updated

            drone.position = updated.position
            drone.velocity = updated.velocity

    def _neighbors(
        self, boid: BoidState, all_boids: List[BoidState], own_id: str
    ) -> List[BoidState]:
        """Return boids within perception_radius, excluding the boid itself."""
        radius = self._params.perception_radius
        result: List[BoidState] = []
        for other in all_boids:
            if other is boid:
                continue
            diff = (
                other.position[0] - boid.position[0],
                other.position[1] - boid.position[1],
                other.position[2] - boid.position[2],
            )
            if _magnitude(diff) <= radius:
                result.append(other)
        return result

    def get_boid_states(self) -> Dict[str, BoidState]:
        """Return current boid states keyed by drone id, for visualization."""
        return dict(self._boids)
