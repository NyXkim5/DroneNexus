"""
Tests for the Reynolds flocking module.

Run from backend/:
    python3 -m pytest tests/test_flocking.py -v
"""
from __future__ import annotations

import math
import random
from typing import List

import pytest

from attacker.flocking import (
    BoidState,
    FlockingParams,
    _magnitude,
    _subtract,
    advance_boid,
    compute_flocking_forces,
)

# Default params used across most tests.
DEFAULT_PARAMS = FlockingParams()


# ---- helpers ----

def _dist(a: BoidState, b: BoidState) -> float:
    return _magnitude(_subtract(a.position, b.position))


def _dist_to(pos_a, pos_b) -> float:
    return _magnitude(_subtract(pos_a, pos_b))


def _stationary(pos) -> BoidState:
    """A boid sitting still at pos."""
    return BoidState(position=pos, velocity=(0.0, 0.0, 0.0))


# ---- individual rule tests ----

def test_separation_pushes_apart() -> None:
    """Two boids within separation radius receive forces pointing away from each other."""
    params = FlockingParams(
        separation_radius=25.0,
        separation_weight=1.5,
        cohesion_weight=0.0,
        alignment_weight=0.0,
        target_weight=0.0,
    )
    boid_a = _stationary((0.0, 0.0, 0.0))
    boid_b = _stationary((10.0, 0.0, 0.0))   # 10 m apart — inside 25 m radius

    force_a = compute_flocking_forces(boid_a, [boid_b], (0.0, 0.0, 0.0), params)
    force_b = compute_flocking_forces(boid_b, [boid_a], (0.0, 0.0, 0.0), params)

    # force_a should point in the -x direction (away from b)
    assert force_a[0] < 0.0, f"Expected negative x force on a, got {force_a}"
    # force_b should point in the +x direction (away from a)
    assert force_b[0] > 0.0, f"Expected positive x force on b, got {force_b}"


def test_cohesion_pulls_together() -> None:
    """A lone boid far from a group steers toward the group centroid."""
    params = FlockingParams(
        cohesion_weight=1.0,
        alignment_weight=0.0,
        separation_weight=0.0,
        target_weight=0.0,
        perception_radius=500.0,
    )
    group = [
        _stationary((100.0, 0.0, 0.0)),
        _stationary((100.0, 10.0, 0.0)),
        _stationary((100.0, -10.0, 0.0)),
    ]
    outlier = _stationary((0.0, 0.0, 0.0))

    force = compute_flocking_forces(outlier, group, (0.0, 0.0, 0.0), params)

    # Centroid of group is at x=100, so force should point in +x direction.
    assert force[0] > 0.0, f"Expected positive x force toward group, got {force}"


def test_alignment_matches_heading() -> None:
    """A stationary boid next to moving neighbors gets a force in their direction."""
    params = FlockingParams(
        alignment_weight=1.0,
        cohesion_weight=0.0,
        separation_weight=0.0,
        target_weight=0.0,
        perception_radius=200.0,
    )
    # Neighbors all moving in +y direction.
    neighbors = [
        BoidState(position=(5.0, 0.0, 0.0), velocity=(0.0, 20.0, 0.0)),
        BoidState(position=(-5.0, 0.0, 0.0), velocity=(0.0, 20.0, 0.0)),
    ]
    boid = BoidState(position=(0.0, 0.0, 0.0), velocity=(0.0, 0.0, 0.0))

    force = compute_flocking_forces(boid, neighbors, (0.0, 0.0, 0.0), params)

    # Force should have a positive y component to match neighbor velocity.
    assert force[1] > 0.0, f"Expected positive y force to align with neighbors, got {force}"


def test_target_seeking() -> None:
    """A boid with no neighbors steers toward the target."""
    params = FlockingParams(
        alignment_weight=0.0,
        cohesion_weight=0.0,
        separation_weight=0.0,
        target_weight=2.0,
    )
    boid = BoidState(position=(0.0, 0.0, 0.0), velocity=(0.0, 0.0, 0.0))
    target = (200.0, 0.0, 0.0)

    force = compute_flocking_forces(boid, [], target, params)

    # With no neighbors only seek applies. Force points toward target (+x).
    assert force[0] > 0.0, f"Expected positive x force toward target, got {force}"
    assert _magnitude(force) > 0.0


def test_speed_clamped() -> None:
    """advance_boid cannot produce a velocity exceeding max_speed."""
    params = FlockingParams(max_speed=40.0, max_force=5.0)
    # Give the boid a huge acceleration to try to breach the cap.
    boid = BoidState(
        position=(0.0, 0.0, 0.0),
        velocity=(39.0, 0.0, 0.0),
        acceleration=(100.0, 0.0, 0.0),
    )
    for _ in range(50):
        boid = advance_boid(boid, dt=1.0, params=params)

    speed = _magnitude(boid.velocity)
    assert speed <= params.max_speed + 1e-9, f"Speed {speed} exceeds max_speed {params.max_speed}"


def test_force_clamped() -> None:
    """compute_flocking_forces never returns a force magnitude exceeding max_force."""
    params = FlockingParams(
        max_force=5.0,
        separation_weight=100.0,
        cohesion_weight=100.0,
        alignment_weight=100.0,
        target_weight=100.0,
        separation_radius=200.0,
        perception_radius=500.0,
    )
    boid = BoidState(position=(0.0, 0.0, 0.0), velocity=(0.0, 0.0, 0.0))
    # Many neighbors packed close to trigger large raw forces.
    neighbors = [
        BoidState(position=(float(i), float(i), 0.0), velocity=(float(i) * 2, 0.0, 0.0))
        for i in range(1, 20)
    ]
    target = (1000.0, 1000.0, 0.0)

    force = compute_flocking_forces(boid, neighbors, target, params)
    mag = _magnitude(force)

    assert mag <= params.max_force + 1e-9, f"Force {mag} exceeds max_force {params.max_force}"


def test_swarm_converges() -> None:
    """20 boids initialized at random positions converge into a formation within 100 ticks.

    Convergence is measured by the standard deviation of pairwise distances dropping
    from the initial spread. A flock that has formed will have lower variance in
    inter-agent distances than one scattered randomly.
    """
    rng = random.Random(42)
    params = FlockingParams(
        alignment_weight=1.0,
        cohesion_weight=1.0,
        separation_weight=1.5,
        target_weight=0.5,
        max_speed=15.0,
        max_force=3.0,
        perception_radius=200.0,
        separation_radius=30.0,
    )
    target = (0.0, 0.0, 0.0)
    boids = [
        BoidState(
            position=(rng.uniform(-300.0, 300.0), rng.uniform(-300.0, 300.0), rng.uniform(50.0, 150.0)),
            velocity=(rng.uniform(-5.0, 5.0), rng.uniform(-5.0, 5.0), 0.0),
        )
        for _ in range(20)
    ]

    def _spread(bs: List[BoidState]) -> float:
        """Mean absolute deviation of pairwise distances from their mean."""
        dists = [
            _dist(bs[i], bs[j])
            for i in range(len(bs))
            for j in range(i + 1, len(bs))
        ]
        mean = sum(dists) / len(dists)
        return sum(abs(d - mean) for d in dists) / len(dists)

    spread_before = _spread(boids)

    for _ in range(100):
        forces = [compute_flocking_forces(b, boids, target, params) for b in boids]
        new_boids = []
        for b, f in zip(boids, forces):
            updated = BoidState(position=b.position, velocity=b.velocity, acceleration=f)
            new_boids.append(advance_boid(updated, dt=0.5, params=params))
        boids = new_boids

    spread_after = _spread(boids)

    assert spread_after < spread_before, (
        f"Swarm did not converge: spread went from {spread_before:.1f} to {spread_after:.1f}"
    )


def test_swarm_reaches_target() -> None:
    """10 boids with target seeking reach within 50 m of the target within 100 ticks."""
    params = FlockingParams(
        alignment_weight=0.5,
        cohesion_weight=0.5,
        separation_weight=1.0,
        target_weight=3.0,
        max_speed=30.0,
        max_force=5.0,
        perception_radius=150.0,
        separation_radius=20.0,
    )
    target = (0.0, 0.0, 100.0)

    rng = random.Random(7)
    boids = [
        BoidState(
            position=(rng.uniform(800.0, 1200.0), rng.uniform(800.0, 1200.0), rng.uniform(80.0, 120.0)),
            velocity=(rng.uniform(-2.0, 2.0), rng.uniform(-2.0, 2.0), 0.0),
        )
        for _ in range(10)
    ]

    for _ in range(100):
        forces = [compute_flocking_forces(b, boids, target, params) for b in boids]
        new_boids = []
        for b, f in zip(boids, forces):
            updated = BoidState(position=b.position, velocity=b.velocity, acceleration=f)
            new_boids.append(advance_boid(updated, dt=1.0, params=params))
        boids = new_boids

    min_dist = min(_dist_to(b.position, target) for b in boids)
    assert min_dist < 50.0, (
        f"No boid reached within 50 m of target after 100 ticks; closest was {min_dist:.1f} m"
    )
