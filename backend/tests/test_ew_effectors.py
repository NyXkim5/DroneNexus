"""
Tests for EW effectors (backend/defense/ew_effectors.py).

Covers effectiveness physics, range gating, allocation logic, energy tracking,
adjacency matrix degradation, frequency matching, GPS spoofing, and edge cases.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csontology import SwarmIntent, Threat
from defense.ew_effectors import (
    EWAllocator,
    EWEffect,
    EWEffectType,
    EWEffector,
)


# ---- Helpers ----

def _effector(
    eid: str = "ew-1",
    effect_type: EWEffectType = EWEffectType.BARRAGE_JAM,
    power_dbm: float = 40.0,
    bandwidth_mhz: float = 40.0,
    range_m: float = 2000.0,
    active: bool = True,
    energy_budget_j: float = float("inf"),
    power_consumption_w: float = 100.0,
    position: tuple = (0.0, 0.0, 0.0),
) -> EWEffector:
    return EWEffector(
        id=eid,
        position=position,
        effect_type=effect_type,
        power_dbm=power_dbm,
        bandwidth_mhz=bandwidth_mhz,
        range_m=range_m,
        active=active,
        energy_budget_j=energy_budget_j,
        power_consumption_w=power_consumption_w,
    )


def _threat(
    tid: str = "t-1",
    score: float = 0.8,
    swarm_id: str = None,
    intent: SwarmIntent = SwarmIntent.UNKNOWN,
) -> Threat:
    return Threat(
        id=tid,
        score=score,
        time_to_impact_s=15.0,
        value_at_risk=1000.0,
        priority_rank=1,
        track_id=f"trk-{tid}",
        swarm_id=swarm_id,
        intent=intent,
    )


# ---- Effectiveness tests ----

def test_barrage_jam_effectiveness_close_target():
    """Close target should yield high effectiveness (near 1.0)."""
    allocator = EWAllocator()
    effector = _effector(effect_type=EWEffectType.BARRAGE_JAM, power_dbm=60.0)
    # 50 m is well within range; jammer power at target will dominate receiver sensitivity.
    effectiveness = allocator.compute_effectiveness(
        effector, target_position=(50.0, 0.0, 0.0)
    )
    assert effectiveness > 0.8, f"Expected > 0.8, got {effectiveness}"


def test_effectiveness_decreases_with_range():
    """Effectiveness must be strictly lower at 1500 m than at 100 m."""
    allocator = EWAllocator()
    effector = _effector(effect_type=EWEffectType.BARRAGE_JAM, power_dbm=40.0, range_m=2000.0)
    eff_close = allocator.compute_effectiveness(effector, target_position=(100.0, 0.0, 0.0))
    eff_far = allocator.compute_effectiveness(effector, target_position=(1500.0, 0.0, 0.0))
    assert eff_close > eff_far, f"Close={eff_close} should exceed far={eff_far}"


def test_out_of_range_no_effect():
    """Target beyond range_m must return exactly 0.0."""
    allocator = EWAllocator()
    effector = _effector(range_m=500.0)
    effectiveness = allocator.compute_effectiveness(
        effector, target_position=(1000.0, 0.0, 0.0)
    )
    assert effectiveness == 0.0


def test_spot_jam_frequency_match_higher_than_mismatch():
    """Matching frequency should give higher effectiveness than a mismatched one."""
    allocator = EWAllocator()
    effector = _effector(effect_type=EWEffectType.SPOT_JAM, power_dbm=40.0)
    pos = (200.0, 0.0, 0.0)
    # DJI protocol center is 2.4 GHz; test at 2.4 vs 5.8 GHz.
    eff_match = allocator.compute_effectiveness(
        effector, pos, target_frequency_ghz=2.4, target_protocol="dji"
    )
    eff_mismatch = allocator.compute_effectiveness(
        effector, pos, target_frequency_ghz=5.8, target_protocol="dji"
    )
    assert eff_match > eff_mismatch, (
        f"Frequency match={eff_match} should exceed mismatch={eff_mismatch}"
    )


def test_gps_spoof_nav_degradation():
    """GPS spoof must produce nav_degradation > 0 and comm_degradation == 0."""
    allocator = EWAllocator()
    effector = _effector(
        effect_type=EWEffectType.GPS_SPOOF, power_dbm=50.0, active=True
    )
    threat = _threat(swarm_id="sw-1")
    positions = {threat.id: (100.0, 0.0, 10.0)}
    effects = allocator.allocate([effector], [threat], positions)
    assert len(effects) >= 1
    gps_effect = effects[0]
    assert gps_effect.nav_degradation > 0.0, "GPS spoof must degrade navigation"
    assert gps_effect.comm_degradation == 0.0, "GPS spoof must not degrade comms"


# ---- Allocation tests ----

def test_allocate_returns_effects_for_in_range_threats():
    """Allocation must produce at least one EWEffect when a threat is in range."""
    allocator = EWAllocator()
    effector = _effector(active=True, range_m=2000.0)
    threat = _threat()
    positions = {threat.id: (500.0, 0.0, 50.0)}
    effects = allocator.allocate([effector], [threat], positions)
    assert len(effects) >= 1
    assert effects[0].effector_id == effector.id
    assert effects[0].target_id == threat.id


def test_inactive_effector_skipped():
    """An effector with active=False must not produce any effects."""
    allocator = EWAllocator()
    effector = _effector(active=False)
    threat = _threat()
    positions = {threat.id: (100.0, 0.0, 0.0)}
    effects = allocator.allocate([effector], [threat], positions)
    assert effects == [], "Inactive effector must be skipped"


def test_out_of_range_threat_produces_no_effect():
    """A threat outside range_m must not receive an EWEffect."""
    allocator = EWAllocator()
    effector = _effector(active=True, range_m=300.0)
    threat = _threat()
    positions = {threat.id: (1000.0, 0.0, 0.0)}
    effects = allocator.allocate([effector], [threat], positions)
    assert effects == [], "Out-of-range threat must not be engaged"


# ---- Energy tests ----

def test_energy_budget_decreases_after_allocation():
    """Energy remaining must drop after allocating against a threat."""
    allocator = EWAllocator()
    budget_j = 5000.0
    effector = _effector(
        active=True,
        energy_budget_j=budget_j,
        power_consumption_w=100.0,
        range_m=2000.0,
    )
    threat = _threat()
    positions = {threat.id: (200.0, 0.0, 0.0)}
    effects = allocator.allocate([effector], [threat], positions)
    assert len(effects) >= 1
    remaining = allocator._energy_remaining[effector.id]
    assert remaining < budget_j, "Energy budget must decrease after engagement"


def test_energy_budget_exhausted_effector_skipped():
    """An effector with zero energy budget must not produce effects."""
    allocator = EWAllocator()
    effector = _effector(
        active=True,
        energy_budget_j=0.0,  # empty budget
        power_consumption_w=100.0,
    )
    threat = _threat()
    positions = {threat.id: (100.0, 0.0, 0.0)}
    effects = allocator.allocate([effector], [threat], positions)
    assert effects == [], "Exhausted effector must be skipped"


def test_energy_cost_matches_effect_record():
    """The energy_cost_j on returned EWEffect must match power * dwell."""
    allocator = EWAllocator()
    effector = _effector(
        active=True,
        power_consumption_w=200.0,
        energy_budget_j=float("inf"),
    )
    threat = _threat()
    positions = {threat.id: (100.0, 0.0, 0.0)}
    effects = allocator.allocate([effector], [threat], positions)
    assert len(effects) >= 1
    expected_cost = effector.power_consumption_w * EWAllocator._DWELL_S
    assert effects[0].energy_cost_j == pytest.approx(expected_cost)


# ---- Adjacency matrix degradation tests ----

def test_apply_effects_degrades_adjacency():
    """EW effects with comm_degradation > 0 must reduce adjacency matrix values."""
    allocator = EWAllocator()
    n = 4
    adj = np.ones((n, n), dtype=float)
    np.fill_diagonal(adj, 0.0)
    original_sum = adj.sum()

    effects = [
        EWEffect(
            effector_id="ew-1",
            target_id="t-0",
            effect_type=EWEffectType.BARRAGE_JAM,
            effectiveness=0.9,
            energy_cost_j=1000.0,
            comm_degradation=0.9,
            nav_degradation=0.0,
        )
    ]
    degraded = allocator.apply_effects(effects, adj)
    assert degraded.sum() < original_sum, "Adjacency sum must decrease after EW"
    # Diagonal must remain zero.
    assert np.all(np.diag(degraded) == 0.0)


def test_apply_effects_gps_spoof_no_comm_change():
    """GPS spoof (comm_degradation=0) must not change adjacency matrix."""
    allocator = EWAllocator()
    n = 3
    adj = np.full((n, n), 0.7)
    np.fill_diagonal(adj, 0.0)

    effects = [
        EWEffect(
            effector_id="ew-1",
            target_id="t-0",
            effect_type=EWEffectType.GPS_SPOOF,
            effectiveness=0.8,
            energy_cost_j=500.0,
            comm_degradation=0.0,
            nav_degradation=0.8,
        )
    ]
    degraded = allocator.apply_effects(effects, adj)
    np.testing.assert_array_almost_equal(
        degraded, adj, err_msg="GPS spoof must not alter adjacency"
    )


def test_apply_effects_does_not_mutate_input():
    """apply_effects must return a new matrix without modifying the original."""
    allocator = EWAllocator()
    adj = np.ones((3, 3))
    np.fill_diagonal(adj, 0.0)
    original = adj.copy()

    effects = [
        EWEffect(
            effector_id="ew-1",
            target_id="t-0",
            effect_type=EWEffectType.BARRAGE_JAM,
            effectiveness=0.5,
            energy_cost_j=100.0,
            comm_degradation=0.5,
            nav_degradation=0.0,
        )
    ]
    _ = allocator.apply_effects(effects, adj)
    np.testing.assert_array_equal(adj, original, err_msg="Input matrix must not be mutated")


# ---- Barrage vs spot coverage ----

def test_barrage_jam_covers_multiple_threats():
    """BARRAGE_JAM must produce effects for every in-range threat."""
    allocator = EWAllocator()
    effector = _effector(
        effect_type=EWEffectType.BARRAGE_JAM,
        active=True,
        range_m=2000.0,
        energy_budget_j=float("inf"),
    )
    threats = [_threat(tid=f"t-{i}", score=0.5) for i in range(5)]
    positions = {t.id: (float(100 * (i + 1)), 0.0, 0.0) for i, t in enumerate(threats)}
    effects = allocator.allocate([effector], threats, positions)
    target_ids = {e.target_id for e in effects}
    for t in threats:
        assert t.id in target_ids, f"Barrage must cover threat {t.id}"


def test_spot_jam_targets_highest_score():
    """SPOT_JAM must target the highest-score in-range threat."""
    allocator = EWAllocator()
    effector = _effector(
        effect_type=EWEffectType.SPOT_JAM,
        active=True,
        range_m=2000.0,
        energy_budget_j=float("inf"),
    )
    low = _threat(tid="low", score=0.2)
    high = _threat(tid="high", score=0.95)
    positions = {
        "low":  (100.0, 0.0, 0.0),
        "high": (200.0, 0.0, 0.0),
    }
    effects = allocator.allocate([effector], [low, high], positions)
    assert len(effects) == 1
    assert effects[0].target_id == "high", "SPOT_JAM must target highest-score threat"


def test_deauth_produces_zero_effectiveness_on_non_wifi():
    """DEAUTH effector must yield 0.0 effectiveness against non-WiFi protocols."""
    allocator = EWAllocator()
    effector = _effector(effect_type=EWEffectType.DEAUTH, power_dbm=40.0, range_m=500.0)
    pos = (100.0, 0.0, 0.0)
    eff = allocator.compute_effectiveness(
        effector, pos, target_frequency_ghz=0.9, target_protocol="mavlink"
    )
    assert eff == 0.0, "DEAUTH must not affect non-WiFi protocol drones"
