"""Tests for the electronic warfare effects simulator."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csontology import DefenderKind
from attacker.ew_effects import (
    DroneFailsafe,
    DroneProfile,
    Environment,
    EWEffect,
    EWEffectSimulator,
)


def _sim() -> EWEffectSimulator:
    return EWEffectSimulator(reference_range_m=500.0, effector_frequency_ghz=2.4)


def _rng(seed: int = 42) -> random.Random:
    return random.Random(seed)


def _default_profile(**kwargs) -> DroneProfile:
    return DroneProfile(**kwargs)


# -- Basic effect resolution --

def test_ew_effector_produces_effect():
    sim = _sim()
    result = sim.compute_effect(
        DefenderKind.EW, (0, 0, 0), _default_profile(), (100, 0, 0), _rng(),
    )
    assert result.effect in EWEffect
    assert 0.0 <= result.probability <= 1.0
    assert 0.0 <= result.effective_power_ratio <= 1.0


def test_jammer_produces_effect():
    sim = _sim()
    result = sim.compute_effect(
        DefenderKind.JAMMER, (0, 0, 0), _default_profile(), (100, 0, 0), _rng(),
    )
    assert result.effect in EWEffect


def test_hpm_produces_effect():
    sim = _sim()
    result = sim.compute_effect(
        DefenderKind.HPM, (0, 0, 0), _default_profile(), (100, 0, 0), _rng(),
    )
    assert result.effect in EWEffect


def test_kinetic_returns_no_effect():
    sim = _sim()
    result = sim.compute_effect(
        DefenderKind.INTERCEPTOR, (0, 0, 0), _default_profile(), (100, 0, 0), _rng(),
    )
    assert result.effect is EWEffect.NO_EFFECT
    assert result.is_neutralized is False
    assert result.effective_power_ratio == 0.0


def test_laser_returns_no_effect():
    sim = _sim()
    result = sim.compute_effect(
        DefenderKind.LASER, (0, 0, 0), _default_profile(), (100, 0, 0), _rng(),
    )
    assert result.effect is EWEffect.NO_EFFECT


# -- Distance falloff --

def test_close_range_higher_power_than_far():
    sim = _sim()
    profile = _default_profile()
    r1 = sim.compute_effect(
        DefenderKind.EW, (0, 0, 0), profile, (50, 0, 0), _rng(1),
    )
    r2 = sim.compute_effect(
        DefenderKind.EW, (0, 0, 0), profile, (2000, 0, 0), _rng(1),
    )
    assert r1.effective_power_ratio > r2.effective_power_ratio


def test_power_ratio_capped_at_one():
    sim = _sim()
    result = sim.compute_effect(
        DefenderKind.EW, (0, 0, 0), _default_profile(), (1, 0, 0), _rng(),
    )
    assert result.effective_power_ratio <= 1.0


def test_very_far_range_mostly_no_effect():
    sim = _sim()
    profile = _default_profile()
    no_effect_count = 0
    for seed in range(200):
        r = sim.compute_effect(
            DefenderKind.EW, (0, 0, 0), profile, (5000, 0, 0), _rng(seed),
        )
        if r.effect is EWEffect.NO_EFFECT:
            no_effect_count += 1
    assert no_effect_count > 150, f"Expected mostly no effect at 5km, got {no_effect_count}/200"


# -- EW resistance --

def test_ew_resistant_drone_resists_jam():
    sim = _sim()
    profile = _default_profile(ew_resistant=True)
    jam_count = 0
    for seed in range(200):
        r = sim.compute_effect(
            DefenderKind.JAMMER, (0, 0, 0), profile, (100, 0, 0), _rng(seed),
        )
        if r.effect is EWEffect.SIGNAL_JAM:
            jam_count += 1
    assert jam_count < 20, f"EW-resistant drone jammed {jam_count}/200 times"


def test_hardened_drone_resists_full_denial():
    sim = _sim()
    profile = _default_profile(hardened=True)
    denial_count = 0
    for seed in range(200):
        r = sim.compute_effect(
            DefenderKind.HPM, (0, 0, 0), profile, (100, 0, 0), _rng(seed),
        )
        if r.effect is EWEffect.FULL_DENIAL:
            denial_count += 1
    normal_profile = _default_profile()
    normal_denial = 0
    for seed in range(200):
        r = sim.compute_effect(
            DefenderKind.HPM, (0, 0, 0), normal_profile, (100, 0, 0), _rng(seed),
        )
        if r.effect is EWEffect.FULL_DENIAL:
            normal_denial += 1
    assert denial_count < normal_denial


def test_gps_imu_fusion_resists_spoofing():
    sim = _sim()
    profile = _default_profile(has_gps_imu_fusion=True)
    spoof_count = 0
    for seed in range(200):
        r = sim.compute_effect(
            DefenderKind.EW, (0, 0, 0), profile, (100, 0, 0), _rng(seed),
        )
        if r.effect is EWEffect.GPS_SPOOF:
            spoof_count += 1
    assert spoof_count < 15, f"GPS/IMU fusion drone spoofed {spoof_count}/200 times"


# -- Frequency match --

def test_frequency_mismatch_reduces_effectiveness():
    sim_matched = EWEffectSimulator(reference_range_m=500.0, effector_frequency_ghz=2.4)
    sim_mismatched = EWEffectSimulator(reference_range_m=500.0, effector_frequency_ghz=5.8)
    profile = _default_profile(frequency_ghz=2.4)
    effects_matched = 0
    effects_mismatched = 0
    for seed in range(200):
        r1 = sim_matched.compute_effect(
            DefenderKind.JAMMER, (0, 0, 0), profile, (200, 0, 0), _rng(seed),
        )
        r2 = sim_mismatched.compute_effect(
            DefenderKind.JAMMER, (0, 0, 0), profile, (200, 0, 0), _rng(seed),
        )
        if r1.effect is not EWEffect.NO_EFFECT:
            effects_matched += 1
        if r2.effect is not EWEffect.NO_EFFECT:
            effects_mismatched += 1
    assert effects_matched > effects_mismatched


def test_hpm_ignores_frequency_match():
    sim = EWEffectSimulator(reference_range_m=500.0, effector_frequency_ghz=5.8)
    profile = _default_profile(frequency_ghz=2.4)
    result = sim.compute_effect(
        DefenderKind.HPM, (0, 0, 0), profile, (100, 0, 0), _rng(),
    )
    assert result.effective_power_ratio > 0.5


# -- Terrain shielding --

def test_terrain_shielding_reduces_effectiveness():
    sim = _sim()
    profile = _default_profile()
    shielded = Environment(terrain_shielding_db=20.0)
    r_open = sim.compute_effect(
        DefenderKind.EW, (0, 0, 0), profile, (200, 0, 0), _rng(7),
    )
    r_shielded = sim.compute_effect(
        DefenderKind.EW, (0, 0, 0), profile, (200, 0, 0), _rng(7),
        environment=shielded,
    )
    assert r_open.effective_power_ratio > r_shielded.effective_power_ratio


# -- Failsafe resolution --

def test_full_denial_causes_crash():
    sim = _sim()
    profile = _default_profile(failsafe=DroneFailsafe.RTH)
    for seed in range(500):
        r = sim.compute_effect(
            DefenderKind.HPM, (0, 0, 0), profile, (50, 0, 0), _rng(seed),
        )
        if r.effect is EWEffect.FULL_DENIAL:
            assert r.failsafe is DroneFailsafe.CRASH
            assert r.is_neutralized is True
            return
    raise AssertionError("FULL_DENIAL never rolled in 500 tries")


def test_signal_jam_uses_drone_failsafe():
    sim = _sim()
    profile = _default_profile(failsafe=DroneFailsafe.LAND)
    for seed in range(500):
        r = sim.compute_effect(
            DefenderKind.JAMMER, (0, 0, 0), profile, (50, 0, 0), _rng(seed),
        )
        if r.effect is EWEffect.SIGNAL_JAM:
            assert r.failsafe is DroneFailsafe.LAND
            return
    raise AssertionError("SIGNAL_JAM never rolled in 500 tries")


def test_ew_resistant_continues_unless_full_denial():
    sim = _sim()
    profile = _default_profile(ew_resistant=True)
    for seed in range(500):
        r = sim.compute_effect(
            DefenderKind.EW, (0, 0, 0), profile, (50, 0, 0), _rng(seed),
        )
        if r.effect is not EWEffect.NO_EFFECT and r.effect is not EWEffect.FULL_DENIAL:
            assert r.failsafe is DroneFailsafe.CONTINUE
            return


# -- Neutralization flag --

def test_neutralizing_effects_flagged():
    sim = _sim()
    profile = _default_profile()
    for seed in range(500):
        r = sim.compute_effect(
            DefenderKind.EW, (0, 0, 0), profile, (50, 0, 0), _rng(seed),
        )
        if r.effect is EWEffect.SIGNAL_JAM:
            assert r.is_neutralized is True
            return


def test_video_disrupt_not_neutralized():
    sim = _sim()
    profile = _default_profile()
    for seed in range(2000):
        r = sim.compute_effect(
            DefenderKind.JAMMER, (0, 0, 0), profile, (50, 0, 0), _rng(seed),
        )
        if r.effect is EWEffect.VIDEO_DISRUPT:
            assert r.is_neutralized is False
            return


# -- Determinism --

def test_same_seed_same_result():
    sim = _sim()
    profile = _default_profile()
    r1 = sim.compute_effect(
        DefenderKind.EW, (0, 0, 0), profile, (200, 0, 0), _rng(99),
    )
    r2 = sim.compute_effect(
        DefenderKind.EW, (0, 0, 0), profile, (200, 0, 0), _rng(99),
    )
    assert r1.effect == r2.effect
    assert r1.probability == r2.probability
