"""Tests for the hostile swarm generator.

Run with cwd=backend so imports resolve against the backend root.
"""
from __future__ import annotations

import math

import pytest

from attacker.hostile_swarm import (
    DECOY_UNIT_COST,
    DEFAULT_UNIT_COST,
    HostileSwarm,
    _magnitude,
    _subtract,
)
from csontology import SwarmIntent

SITE = (0.0, 0.0, 0.0)


def _dist_to_site(position) -> float:
    return _magnitude(_subtract(SITE, position))


def _mean_dist_to_site(swarm: HostileSwarm) -> float:
    drones = swarm.get_truth()
    return sum(_dist_to_site(d.position) for d in drones) / len(drones)


def test_size_matches_count() -> None:
    swarm = HostileSwarm(SwarmIntent.SATURATION, count=120, site_position=SITE, seed=1)
    assert len(swarm.get_truth()) == 120
    assert len(swarm.drones) == 120


@pytest.mark.parametrize("count", [10, 1000])
def test_size_bounds_allowed(count: int) -> None:
    swarm = HostileSwarm(SwarmIntent.SATURATION, count=count, site_position=SITE, seed=2)
    assert len(swarm.get_truth()) == count


@pytest.mark.parametrize("count", [9, 1001, 0, -5])
def test_size_out_of_bounds_rejected(count: int) -> None:
    with pytest.raises(ValueError):
        HostileSwarm(SwarmIntent.SATURATION, count=count, site_position=SITE)


def test_drones_spawn_on_ring_away_from_site() -> None:
    swarm = HostileSwarm(SwarmIntent.SATURATION, count=50, site_position=SITE, seed=3)
    for d in swarm.get_truth():
        assert _dist_to_site(d.position) > 1000.0


def test_saturation_converges_over_time() -> None:
    swarm = HostileSwarm(SwarmIntent.SATURATION, count=80, site_position=SITE, seed=4)
    before = _mean_dist_to_site(swarm)
    for _ in range(40):
        swarm.advance(1.0)
    after = _mean_dist_to_site(swarm)
    assert after < before


def test_drones_have_velocity_toward_site_after_launch() -> None:
    swarm = HostileSwarm(SwarmIntent.SATURATION, count=30, site_position=SITE, seed=5)
    swarm.advance(1.0)
    for d in swarm.get_truth():
        if d.position == SITE:
            continue
        to_site = _subtract(SITE, d.position)
        # Velocity should point toward the site: positive dot product.
        dot = sum(v * w for v, w in zip(d.velocity, to_site))
        assert dot > 0.0


def test_velocity_magnitude_matches_cruise_speed() -> None:
    swarm = HostileSwarm(SwarmIntent.SATURATION, count=20, site_position=SITE, seed=6)
    swarm.advance(0.1)
    d = swarm.get_truth()[0]
    assert _magnitude(d.velocity) == pytest.approx(18.0, rel=0.05)


def test_eventual_arrival_at_site() -> None:
    swarm = HostileSwarm(SwarmIntent.SATURATION, count=15, site_position=SITE, seed=7)
    for _ in range(400):
        swarm.advance(1.0)
    assert swarm.arrived_count() == 15


def test_decoy_has_cheap_and_real_mix() -> None:
    swarm = HostileSwarm(SwarmIntent.DECOY, count=100, site_position=SITE, seed=8)
    truth = swarm.get_truth()
    decoys = [d for d in truth if d.is_decoy]
    reals = [d for d in truth if not d.is_decoy]
    assert len(decoys) > 0
    assert len(reals) > 0
    assert len(decoys) > len(reals)
    assert all(d.unit_cost == DECOY_UNIT_COST for d in decoys)
    assert all(d.unit_cost == DEFAULT_UNIT_COST for d in reals)


def test_saturation_has_no_decoys() -> None:
    swarm = HostileSwarm(SwarmIntent.SATURATION, count=40, site_position=SITE, seed=9)
    assert all(not d.is_decoy for d in swarm.get_truth())


def test_waves_launch_staggered() -> None:
    swarm = HostileSwarm(SwarmIntent.WAVES, count=80, site_position=SITE, seed=10)
    launch_times = {d.launch_time for d in swarm.drones}
    # Staggered groups give more than one distinct launch time.
    assert len(launch_times) > 1


def test_waves_converge_slower_than_saturation() -> None:
    sat = HostileSwarm(SwarmIntent.SATURATION, count=80, site_position=SITE, seed=11)
    wav = HostileSwarm(SwarmIntent.WAVES, count=80, site_position=SITE, seed=11)
    for _ in range(3):
        sat.advance(1.0)
        wav.advance(1.0)
    # Later waves have not launched, so the wave swarm stays farther out.
    assert _mean_dist_to_site(wav) > _mean_dist_to_site(sat)


def test_probe_advances_only_lead_element() -> None:
    swarm = HostileSwarm(SwarmIntent.PROBE, count=100, site_position=SITE, seed=12)
    before = [_dist_to_site(d.position) for d in swarm.get_truth()]
    for _ in range(10):
        swarm.advance(1.0)
    after = [_dist_to_site(d.position) for d in swarm.get_truth()]
    moved = sum(1 for b, a in zip(before, after) if a < b - 1.0)
    held = sum(1 for b, a in zip(before, after) if abs(a - b) < 0.01)
    assert moved > 0
    assert held > 0
    assert held > moved  # most of the swarm holds during a probe


def test_behaviors_differ_in_convergence() -> None:
    sat = HostileSwarm(SwarmIntent.SATURATION, count=100, site_position=SITE, seed=13)
    prb = HostileSwarm(SwarmIntent.PROBE, count=100, site_position=SITE, seed=13)
    for _ in range(15):
        sat.advance(1.0)
        prb.advance(1.0)
    # Saturation closes the whole force, probe only nudges the lead element.
    assert _mean_dist_to_site(sat) < _mean_dist_to_site(prb)


def test_total_cost_reflects_decoy_savings() -> None:
    sat = HostileSwarm(SwarmIntent.SATURATION, count=100, site_position=SITE, seed=14)
    dec = HostileSwarm(SwarmIntent.DECOY, count=100, site_position=SITE, seed=14)
    assert dec.total_cost() < sat.total_cost()


def test_centroid_moves_toward_offset_site() -> None:
    site = (500.0, 500.0, 0.0)
    swarm = HostileSwarm(SwarmIntent.SATURATION, count=60, site_position=site, seed=15)
    start = _magnitude(_subtract(site, swarm.centroid()))
    for _ in range(20):
        swarm.advance(1.0)
    end = _magnitude(_subtract(site, swarm.centroid()))
    assert end < start


def test_get_truth_is_decoupled_snapshot() -> None:
    swarm = HostileSwarm(SwarmIntent.SATURATION, count=20, site_position=SITE, seed=16)
    truth = swarm.get_truth()
    first_id = truth[0].id
    # Mutating the snapshot list does not change the swarm.
    truth.clear()
    assert len(swarm.get_truth()) == 20
    assert swarm.get_truth()[0].id == first_id


def test_deterministic_with_seed() -> None:
    a = HostileSwarm(SwarmIntent.SATURATION, count=50, site_position=SITE, seed=99)
    b = HostileSwarm(SwarmIntent.SATURATION, count=50, site_position=SITE, seed=99)
    pa = [d.position for d in a.get_truth()]
    pb = [d.position for d in b.get_truth()]
    assert pa == pb


def test_unknown_intent_rejected() -> None:
    with pytest.raises(ValueError):
        HostileSwarm(SwarmIntent.UNKNOWN, count=20, site_position=SITE)
