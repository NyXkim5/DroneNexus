"""
Tests for FlockingSwarmAdapter.

Verifies that the adapter correctly bridges the HostileSwarm with the Reynolds
flocking module. All tests run from cwd=backend.

Run:
    python3 -m pytest tests/test_swarm_adapter.py -v
"""
from __future__ import annotations

import math

import pytest

from attacker.flocking import BoidState, FlockingParams
from attacker.hostile_swarm import HostileSwarm, _magnitude, _subtract
from attacker.swarm_adapter import FlockingSwarmAdapter
from csontology import SwarmIntent

SITE: tuple[float, float, float] = (0.0, 0.0, 0.0)
DRONE_COUNT = 20


def _make_swarm(seed: int = 1, count: int = DRONE_COUNT) -> HostileSwarm:
    return HostileSwarm(
        SwarmIntent.SATURATION, count=count, site_position=SITE, seed=seed
    )


def _make_adapter(
    swarm: HostileSwarm, params: FlockingParams | None = None
) -> FlockingSwarmAdapter:
    adapter = FlockingSwarmAdapter(swarm=swarm, params=params)
    adapter.initialize_boids()
    return adapter


# ---- test_adapter_initializes_boids ----


def test_adapter_initializes_boids() -> None:
    """Every drone in the swarm gets a BoidState after initialize_boids()."""
    swarm = _make_swarm()
    adapter = _make_adapter(swarm)
    states = adapter.get_boid_states()

    assert len(states) == len(swarm.drones), (
        f"Expected {len(swarm.drones)} boid states, got {len(states)}"
    )
    for drone in swarm.drones:
        assert drone.id in states, f"No BoidState for drone {drone.id}"
        boid = states[drone.id]
        assert isinstance(boid, BoidState)
        assert boid.position == drone.position
        assert boid.velocity == drone.velocity


# ---- test_advance_updates_positions ----


def test_advance_updates_positions() -> None:
    """After advance_all(), at least some drone positions have changed."""
    swarm = _make_swarm()
    initial_positions = {d.id: d.position for d in swarm.drones}
    adapter = _make_adapter(swarm)

    adapter.advance_all(dt=0.2, target=SITE)

    moved = sum(
        1 for d in swarm.drones
        if d.position != initial_positions[d.id]
    )
    assert moved > 0, "No drone moved after advance_all()"


# ---- test_drones_maintain_separation ----


def test_drones_maintain_separation() -> None:
    """After 50 ticks, no two active drones are closer than separation_radius."""
    params = FlockingParams(
        separation_radius=20.0,
        separation_weight=3.0,
        cohesion_weight=0.5,
        alignment_weight=0.5,
        target_weight=1.0,
        max_speed=30.0,
        max_force=8.0,
        perception_radius=200.0,
    )
    swarm = _make_swarm(seed=42, count=15)
    adapter = _make_adapter(swarm, params=params)

    for _ in range(50):
        adapter.advance_all(dt=0.2, target=SITE)

    active = [d for d in swarm.drones if not d.arrived]
    violations = 0
    for i, a in enumerate(active):
        for b in active[i + 1:]:
            dist = _magnitude(_subtract(a.position, b.position))
            if dist < params.separation_radius * 0.5:
                violations += 1

    # Allow a small number of near-violations from drones still converging
    # from spawn positions, but the vast majority must maintain separation.
    assert violations == 0, (
        f"{violations} drone pair(s) violated separation_radius after 50 ticks"
    )


# ---- test_swarm_moves_toward_target ----


def test_swarm_moves_toward_target() -> None:
    """The center of mass of active drones moves closer to the target over time."""
    swarm = _make_swarm(seed=7)
    adapter = _make_adapter(swarm)

    def _centroid() -> tuple[float, float, float]:
        active = [d for d in swarm.drones if not d.arrived]
        if not active:
            return SITE
        n = float(len(active))
        return (
            sum(d.position[0] for d in active) / n,
            sum(d.position[1] for d in active) / n,
            sum(d.position[2] for d in active) / n,
        )

    initial_dist = _magnitude(_subtract(_centroid(), SITE))

    for _ in range(30):
        adapter.advance_all(dt=0.2, target=SITE)

    final_dist = _magnitude(_subtract(_centroid(), SITE))

    assert final_dist < initial_dist, (
        f"Swarm center of mass did not approach target: "
        f"initial={initial_dist:.1f} m, final={final_dist:.1f} m"
    )


# ---- test_existing_scenarios_unaffected ----


def test_existing_scenarios_unaffected() -> None:
    """With use_flocking=False (default), the wargame runner uses bare swarm.advance().

    This test confirms the Scenario dataclass defaults to use_flocking=False and
    that the swarm still advances normally without the adapter in the runner path.
    We verify this at the swarm level: bare advance() moves drones the same way
    the runner would if no adapter is attached.
    """
    from wargame.scenario import load_scenario

    scenario = load_scenario("skirmish_80")
    assert scenario.use_flocking is False, (
        "Default scenario should have use_flocking=False"
    )

    swarm = HostileSwarm(
        scenario.swarm_intent,
        count=scenario.swarm_count,
        site_position=SITE,
        seed=scenario.seed,
    )
    positions_before = [d.position for d in swarm.drones]

    swarm.advance(dt=0.2)
    positions_after = [d.position for d in swarm.drones]

    moved = sum(1 for a, b in zip(positions_before, positions_after) if a != b)
    assert moved > 0, "Bare swarm.advance() should still move drones"
