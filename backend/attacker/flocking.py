"""
Reynolds flocking for BULWARK attacker swarms.

This module is fully independent. It knows nothing about HostileSwarm,
AttackerDrone, sensors, or the wargame runner. It takes BoidStates and
returns steering forces. The caller integrates those forces into velocity
and position each tick.

Three classic Reynolds rules plus a target-seek behavior:
  alignment   — match the average heading of nearby neighbors
  cohesion    — steer toward the center of mass of nearby neighbors
  separation  — push away from neighbors inside the separation radius
  seek        — steer toward a target point

All positions and velocities are in ENU meters and m/s. Acceleration is
in m/s^2. The module uses plain tuples for Vec3 to stay consistent with
the rest of the backend.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Tuple

Vec3 = Tuple[float, float, float]


@dataclass
class FlockingParams:
    """Tunable weights and limits for the Reynolds boid controller."""
    alignment_weight: float = 1.0
    cohesion_weight: float = 1.0
    separation_weight: float = 1.5
    separation_radius: float = 25.0    # meters
    perception_radius: float = 100.0   # meters for alignment and cohesion
    max_speed: float = 40.0            # m/s
    max_force: float = 5.0             # m/s^2 max steering acceleration
    target_weight: float = 2.0         # weight for seek-target behavior


@dataclass
class BoidState:
    """Kinematic state of one boid in ENU meters and m/s."""
    position: Vec3
    velocity: Vec3
    acceleration: Vec3 = field(default_factory=lambda: (0.0, 0.0, 0.0))


# ---- public API ----

def compute_flocking_forces(
    boid: BoidState,
    neighbors: List[BoidState],
    target: Vec3,
    params: FlockingParams,
) -> Vec3:
    """Return the steering acceleration for one boid given its neighbors and target.

    All four rules are computed, weighted, summed, and clamped to max_force.
    If there are no neighbors the result is purely the seek-target force.
    """
    forces: List[Vec3] = []

    if neighbors:
        sep = _separation(boid, neighbors, params)
        aln = _alignment(boid, neighbors, params)
        coh = _cohesion(boid, neighbors, params)
        forces.append(_scale(sep, params.separation_weight))
        forces.append(_scale(aln, params.alignment_weight))
        forces.append(_scale(coh, params.cohesion_weight))

    seek = _seek(boid, target, params)
    forces.append(_scale(seek, params.target_weight))

    total = _sum_forces(forces)
    return _clamp_magnitude(total, params.max_force)


def advance_boid(boid: BoidState, dt: float, params: FlockingParams) -> BoidState:
    """Integrate velocity and position by dt seconds using current acceleration.

    Returns a new BoidState. The original is not mutated.
    """
    new_vel = _add(boid.velocity, _scale(boid.acceleration, dt))
    new_vel = _clamp_magnitude(new_vel, params.max_speed)
    new_pos = _add(boid.position, _scale(new_vel, dt))
    return BoidState(position=new_pos, velocity=new_vel, acceleration=boid.acceleration)


# ---- Reynolds rules ----

def _separation(boid: BoidState, neighbors: List[BoidState], params: FlockingParams) -> Vec3:
    """Steer away from neighbors inside the separation radius.

    Uses a 1/distance weighting so very close neighbors push harder.
    """
    steering = (0.0, 0.0, 0.0)
    count = 0
    for other in neighbors:
        diff = _subtract(boid.position, other.position)
        dist = _magnitude(diff)
        if 0.0 < dist < params.separation_radius:
            weight = 1.0 / dist
            steering = _add(steering, _scale(diff, weight))
            count += 1
    if count == 0:
        return (0.0, 0.0, 0.0)
    steering = _scale(steering, 1.0 / count)
    return _steer_toward(boid, steering, params)


def _alignment(boid: BoidState, neighbors: List[BoidState], params: FlockingParams) -> Vec3:
    """Steer to match the average velocity of neighbors within perception radius."""
    avg_vel = (0.0, 0.0, 0.0)
    count = 0
    for other in neighbors:
        dist = _magnitude(_subtract(boid.position, other.position))
        if dist < params.perception_radius:
            avg_vel = _add(avg_vel, other.velocity)
            count += 1
    if count == 0:
        return (0.0, 0.0, 0.0)
    avg_vel = _scale(avg_vel, 1.0 / count)
    return _steer_toward(boid, avg_vel, params)


def _cohesion(boid: BoidState, neighbors: List[BoidState], params: FlockingParams) -> Vec3:
    """Steer toward the center of mass of neighbors within perception radius."""
    centroid = (0.0, 0.0, 0.0)
    count = 0
    for other in neighbors:
        dist = _magnitude(_subtract(boid.position, other.position))
        if dist < params.perception_radius:
            centroid = _add(centroid, other.position)
            count += 1
    if count == 0:
        return (0.0, 0.0, 0.0)
    centroid = _scale(centroid, 1.0 / count)
    desired = _subtract(centroid, boid.position)
    return _steer_toward(boid, desired, params)


def _seek(boid: BoidState, target: Vec3, params: FlockingParams) -> Vec3:
    """Steer toward the target point at max speed."""
    desired = _subtract(target, boid.position)
    return _steer_toward(boid, desired, params)


def _steer_toward(boid: BoidState, desired: Vec3, params: FlockingParams) -> Vec3:
    """Compute a steering force toward a desired direction.

    Normalizes desired to max_speed, subtracts current velocity, and clamps
    the result to max_force. This is the classic Reynolds steering formula:
    steering = desired_velocity - current_velocity.
    """
    mag = _magnitude(desired)
    if mag <= 0.0:
        return (0.0, 0.0, 0.0)
    desired_vel = _scale(desired, params.max_speed / mag)
    steer = _subtract(desired_vel, boid.velocity)
    return _clamp_magnitude(steer, params.max_force)


# ---- Vec3 math helpers ----

def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _subtract(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a: Vec3, k: float) -> Vec3:
    return (a[0] * k, a[1] * k, a[2] * k)


def _magnitude(a: Vec3) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _normalize(a: Vec3) -> Vec3:
    mag = _magnitude(a)
    if mag <= 0.0:
        return (0.0, 0.0, 0.0)
    return _scale(a, 1.0 / mag)


def _clamp_magnitude(a: Vec3, limit: float) -> Vec3:
    mag = _magnitude(a)
    if mag <= limit or mag <= 0.0:
        return a
    return _scale(a, limit / mag)


def _sum_forces(forces: List[Vec3]) -> Vec3:
    total = (0.0, 0.0, 0.0)
    for f in forces:
        total = _add(total, f)
    return total
