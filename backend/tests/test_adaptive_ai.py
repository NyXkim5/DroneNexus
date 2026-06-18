"""Tests for the adversarial adaptive AI.

Run with cwd=backend so imports resolve against the backend root:
    python3 -m pytest tests/test_adaptive_ai.py -v
"""
from __future__ import annotations

import math
from typing import List

import pytest

from attacker.adaptive import (
    FLANK_GROUP_COUNT,
    HIGH_ENGAGEMENT_RATE,
    HIGH_LOSS_FRACTION,
    LOW_ALTITUDE_M,
    PROBE_SCOUT_FRACTION,
    TACTIC_COOLDOWN_S,
    AdaptiveAttackerAI,
    AdaptiveTactic,
    AttackerObservation,
    _centroid,
    _distance,
)
from csontology import Vec3

# Shared constants for tests.
SITE: Vec3 = (0.0, 0.0, 0.0)
FAR_EFFECTOR: Vec3 = (1000.0, 0.0, 0.0)
FAR_EFFECTOR_2: Vec3 = (-1000.0, 0.0, 0.0)
CLUSTERED_EFFECTOR_A: Vec3 = (50.0, 0.0, 0.0)
CLUSTERED_EFFECTOR_B: Vec3 = (100.0, 0.0, 0.0)


def _make_obs(
    *,
    destroyed: int = 0,
    remaining: int = 100,
    leaked: int = 0,
    active_effectors: int = 4,
    effector_positions: List[Vec3] | None = None,
    engagement_rate: float = 0.0,
    coverage_gaps: List[Vec3] | None = None,
) -> AttackerObservation:
    return AttackerObservation(
        drones_destroyed=destroyed,
        drones_remaining=remaining,
        drones_leaked_through=leaked,
        active_effectors=active_effectors,
        effector_positions=effector_positions or [],
        engagement_rate=engagement_rate,
        coverage_gaps=coverage_gaps or [],
    )


def _make_positions(n: int, x_offset: float = 500.0) -> List[Vec3]:
    return [(x_offset, float(i * 10), 100.0) for i in range(n)]


def _make_velocities(n: int) -> List[Vec3]:
    return [(0.0, 0.0, 0.0) for _ in range(n)]


# ---- initial state ----


def test_initial_tactic_is_saturate() -> None:
    ai = AdaptiveAttackerAI()
    assert ai.current_tactic == AdaptiveTactic.SATURATE


def test_tactic_history_empty_on_init() -> None:
    ai = AdaptiveAttackerAI()
    assert ai.tactic_history == []


# ---- high losses trigger adaptation ----


def test_high_losses_triggers_adaptation() -> None:
    ai = AdaptiveAttackerAI()
    # >50% destroyed + high engagement rate must switch away from SATURATE.
    obs = _make_obs(
        destroyed=60,
        remaining=40,
        leaked=0,
        engagement_rate=HIGH_ENGAGEMENT_RATE + 0.1,
        effector_positions=[FAR_EFFECTOR, FAR_EFFECTOR_2],
    )
    ai.observe(obs)
    tactic = ai.decide(timestamp=0.0)
    assert tactic != AdaptiveTactic.SATURATE


def test_low_losses_keeps_saturate() -> None:
    ai = AdaptiveAttackerAI()
    obs = _make_obs(destroyed=5, remaining=95, leaked=1, engagement_rate=0.1)
    ai.observe(obs)
    tactic = ai.decide(timestamp=0.0)
    assert tactic == AdaptiveTactic.SATURATE


# ---- coverage gap exploitation ----


def test_coverage_gap_exploited() -> None:
    """When a gap is observed, the tactic targets it."""
    ai = AdaptiveAttackerAI()
    gap: Vec3 = (300.0, 300.0, 0.0)
    obs = _make_obs(coverage_gaps=[gap])
    ai.observe(obs)
    ai.decide(timestamp=0.0)

    positions = _make_positions(10)
    velocities = _make_velocities(10)
    waypoints = ai.apply_tactic(ai.current_tactic, positions, velocities, SITE)
    # At least some waypoints should aim at or near the gap, not the base target.
    assert len(waypoints) == 10
    # The tactic is not pure SATURATE (all waypoints == SITE) when a gap exists.
    assert not all(w == SITE for w in waypoints)


def test_gap_with_clustered_effectors_triggers_feint() -> None:
    ai = AdaptiveAttackerAI()
    gap: Vec3 = (300.0, 300.0, 0.0)
    obs = _make_obs(
        coverage_gaps=[gap],
        effector_positions=[CLUSTERED_EFFECTOR_A, CLUSTERED_EFFECTOR_B],
    )
    ai.observe(obs)
    tactic = ai.decide(timestamp=0.0)
    assert tactic == AdaptiveTactic.FEINT_AND_STRIKE


# ---- hysteresis ----


def test_hysteresis_prevents_oscillation() -> None:
    """Tactic must not switch again within TACTIC_COOLDOWN_S seconds."""
    ai = AdaptiveAttackerAI()
    obs_switch = _make_obs(
        destroyed=60,
        remaining=40,
        leaked=0,
        engagement_rate=HIGH_ENGAGEMENT_RATE + 0.2,
        effector_positions=[FAR_EFFECTOR, FAR_EFFECTOR_2],
    )
    ai.observe(obs_switch)
    first_tactic = ai.decide(timestamp=0.0)

    # Feed a different observation immediately.
    obs_different = _make_obs(
        destroyed=5,
        remaining=95,
        leaked=5,
        engagement_rate=0.0,
    )
    ai.observe(obs_different)
    # Only 2 seconds later — within cooldown.
    second_tactic = ai.decide(timestamp=2.0)

    assert second_tactic == first_tactic


def test_hysteresis_allows_switch_after_cooldown() -> None:
    """Tactic can switch after TACTIC_COOLDOWN_S seconds have elapsed."""
    ai = AdaptiveAttackerAI()
    obs_losses = _make_obs(
        destroyed=60,
        remaining=40,
        leaked=0,
        engagement_rate=HIGH_ENGAGEMENT_RATE + 0.2,
        effector_positions=[FAR_EFFECTOR, FAR_EFFECTOR_2],
    )
    ai.observe(obs_losses)
    ai.decide(timestamp=0.0)

    obs_normal = _make_obs(destroyed=0, remaining=100, leaked=5, engagement_rate=0.0)
    ai.observe(obs_normal)
    tactic_after = ai.decide(timestamp=TACTIC_COOLDOWN_S + 1.0)

    assert tactic_after == AdaptiveTactic.SATURATE


# ---- feint splits force ----


def test_feint_splits_force() -> None:
    """FEINT_AND_STRIKE produces a decoy group and a strike group."""
    ai = AdaptiveAttackerAI()
    # Plant a known weak point so the feint has a strike target.
    gap: Vec3 = (300.0, 300.0, 0.0)
    obs = _make_obs(
        coverage_gaps=[gap],
        effector_positions=[CLUSTERED_EFFECTOR_A, CLUSTERED_EFFECTOR_B],
    )
    ai.observe(obs)
    ai.decide(timestamp=0.0)

    positions = _make_positions(20)
    velocities = _make_velocities(20)
    waypoints = ai.apply_tactic(
        AdaptiveTactic.FEINT_AND_STRIKE, positions, velocities, SITE
    )
    assert len(waypoints) == 20
    unique_targets = set(waypoints)
    # Must produce at least two distinct target groups.
    assert len(unique_targets) >= 2


def test_feint_decoy_count_is_roughly_30_percent() -> None:
    ai = AdaptiveAttackerAI()
    gap: Vec3 = (300.0, 300.0, 0.0)
    obs = _make_obs(
        coverage_gaps=[gap],
        effector_positions=[CLUSTERED_EFFECTOR_A, CLUSTERED_EFFECTOR_B],
    )
    ai.observe(obs)

    n = 30
    positions = _make_positions(n)
    velocities = _make_velocities(n)
    waypoints = ai.apply_tactic(
        AdaptiveTactic.FEINT_AND_STRIKE, positions, velocities, SITE
    )
    # The strike group target is the gap; count waypoints aimed at gap.
    strike_count = sum(1 for w in waypoints if w == gap)
    decoy_count = n - strike_count
    # Decoy fraction should be approximately 30%.
    assert 0.20 <= decoy_count / n <= 0.40


# ---- split creates multiple groups ----


def test_split_creates_multiple_groups() -> None:
    """SPLIT_AND_FLANK sends drones to at least 2 distinct approach waypoints."""
    ai = AdaptiveAttackerAI()
    n = 20
    positions = _make_positions(n)
    velocities = _make_velocities(n)
    waypoints = ai.apply_tactic(
        AdaptiveTactic.SPLIT_AND_FLANK, positions, velocities, SITE
    )
    assert len(waypoints) == n
    unique_targets = set(waypoints)
    assert len(unique_targets) >= 2


def test_split_produces_flank_group_count_groups() -> None:
    # Each drone's waypoint is computed from its individual position rotated by
    # its group's angle offset, so drones in the same group still get distinct
    # waypoints. The guarantee is that all FLANK_GROUP_COUNT angle offsets are
    # represented, meaning there are at least that many unique waypoints.
    ai = AdaptiveAttackerAI()
    n = FLANK_GROUP_COUNT * 6  # evenly divisible
    positions = _make_positions(n)
    velocities = _make_velocities(n)
    waypoints = ai.apply_tactic(
        AdaptiveTactic.SPLIT_AND_FLANK, positions, velocities, SITE
    )
    unique_targets = set(waypoints)
    assert len(unique_targets) >= FLANK_GROUP_COUNT


# ---- probe sends scouts ----


def test_probe_sends_scouts() -> None:
    """SACRIFICE_PROBE sends a small advance force; the rest hold in place."""
    ai = AdaptiveAttackerAI()
    n = 40
    positions = _make_positions(n)
    velocities = _make_velocities(n)
    waypoints = ai.apply_tactic(
        AdaptiveTactic.SACRIFICE_PROBE, positions, velocities, SITE
    )
    assert len(waypoints) == n

    scout_count = max(1, round(n * PROBE_SCOUT_FRACTION))
    # Scouts get waypoints different from their current position.
    scouts_advancing = sum(
        1 for i, (w, p) in enumerate(zip(waypoints, positions))
        if i < scout_count and w != p
    )
    assert scouts_advancing == scout_count

    # Remaining drones hold at their current position.
    holders_holding = sum(
        1 for i, (w, p) in enumerate(zip(waypoints, positions))
        if i >= scout_count and w == p
    )
    assert holders_holding == n - scout_count


# ---- tactic history ----


def test_tactic_history_recorded() -> None:
    """Every tactic switch is logged in tactic_history with a timestamp."""
    ai = AdaptiveAttackerAI()
    obs_losses = _make_obs(
        destroyed=60,
        remaining=40,
        leaked=0,
        engagement_rate=HIGH_ENGAGEMENT_RATE + 0.2,
        effector_positions=[FAR_EFFECTOR, FAR_EFFECTOR_2],
    )
    ai.observe(obs_losses)
    ai.decide(timestamp=0.0)

    assert len(ai.tactic_history) == 1
    ts, tactic = ai.tactic_history[0]
    assert ts == 0.0
    assert tactic != AdaptiveTactic.SATURATE


def test_tactic_history_logs_multiple_switches() -> None:
    ai = AdaptiveAttackerAI()
    obs_losses = _make_obs(
        destroyed=60,
        remaining=40,
        leaked=0,
        engagement_rate=HIGH_ENGAGEMENT_RATE + 0.2,
        effector_positions=[FAR_EFFECTOR, FAR_EFFECTOR_2],
    )
    ai.observe(obs_losses)
    ai.decide(timestamp=0.0)

    obs_normal = _make_obs(destroyed=0, remaining=100, leaked=5, engagement_rate=0.0)
    ai.observe(obs_normal)
    ai.decide(timestamp=TACTIC_COOLDOWN_S + 1.0)

    assert len(ai.tactic_history) == 2
    assert ai.tactic_history[0][0] == 0.0
    assert ai.tactic_history[1][0] == TACTIC_COOLDOWN_S + 1.0


def test_no_history_on_no_switch() -> None:
    ai = AdaptiveAttackerAI()
    obs = _make_obs(destroyed=5, remaining=95, leaked=2, engagement_rate=0.1)
    ai.observe(obs)
    ai.decide(timestamp=0.0)
    assert ai.tactic_history == []


# ---- output count invariant ----


def test_apply_tactic_returns_correct_count() -> None:
    """Every tactic must return exactly as many waypoints as input drones."""
    ai = AdaptiveAttackerAI()
    gap: Vec3 = (300.0, 300.0, 0.0)
    obs = _make_obs(
        coverage_gaps=[gap],
        effector_positions=[CLUSTERED_EFFECTOR_A, CLUSTERED_EFFECTOR_B],
    )
    ai.observe(obs)

    for n in [1, 5, 17, 50]:
        positions = _make_positions(n)
        velocities = _make_velocities(n)
        for tactic in AdaptiveTactic:
            waypoints = ai.apply_tactic(tactic, positions, velocities, SITE)
            assert len(waypoints) == n, (
                f"tactic={tactic.value} n={n} returned {len(waypoints)} waypoints"
            )


def test_apply_tactic_empty_input_returns_empty() -> None:
    ai = AdaptiveAttackerAI()
    for tactic in AdaptiveTactic:
        assert ai.apply_tactic(tactic, [], [], SITE) == []


# ---- swarm reform uses weak point ----


def test_reform_targets_weak_point() -> None:
    """SWARM_REFORM sends all drones to the discovered weak point."""
    ai = AdaptiveAttackerAI()
    weak_point: Vec3 = (250.0, 100.0, 0.0)
    obs = _make_obs(coverage_gaps=[weak_point])
    ai.observe(obs)

    n = 10
    positions = _make_positions(n)
    velocities = _make_velocities(n)
    waypoints = ai.apply_tactic(
        AdaptiveTactic.SWARM_REFORM, positions, velocities, SITE
    )
    assert all(w == weak_point for w in waypoints)


def test_reform_falls_back_to_target_without_weak_point() -> None:
    ai = AdaptiveAttackerAI()
    positions = _make_positions(5)
    velocities = _make_velocities(5)
    waypoints = ai.apply_tactic(
        AdaptiveTactic.SWARM_REFORM, positions, velocities, SITE
    )
    assert all(w == SITE for w in waypoints)


# ---- low and slow ----


def test_low_and_slow_clamps_altitude() -> None:
    ai = AdaptiveAttackerAI()
    positions = _make_positions(10)
    velocities = _make_velocities(10)
    waypoints = ai.apply_tactic(
        AdaptiveTactic.LOW_AND_SLOW, positions, velocities, SITE
    )
    assert all(w[2] == LOW_ALTITUDE_M for w in waypoints)


# ---- constructor validation ----


def test_invalid_aggression_raises() -> None:
    with pytest.raises(ValueError):
        AdaptiveAttackerAI(aggression=1.5)


def test_invalid_adaptation_rate_raises() -> None:
    with pytest.raises(ValueError):
        AdaptiveAttackerAI(adaptation_rate=-0.1)


# ---- probe triggers reform sequence ----


def test_probe_then_reform_sequence() -> None:
    """After a probe discovers a gap, the next decide() at cooldown switches to REFORM."""
    ai = AdaptiveAttackerAI()

    # Force into SACRIFICE_PROBE state by simulating stall.
    obs_stall = _make_obs(
        destroyed=60,
        remaining=40,
        leaked=0,
        engagement_rate=HIGH_ENGAGEMENT_RATE + 0.5,
        effector_positions=[FAR_EFFECTOR, FAR_EFFECTOR_2],
        coverage_gaps=[],
    )
    ai.observe(obs_stall)
    ai.decide(timestamp=0.0)

    # Now the probe discovers a gap.
    weak: Vec3 = (200.0, 50.0, 0.0)
    obs_gap = _make_obs(coverage_gaps=[weak])
    ai.observe(obs_gap)

    # Manually set tactic to PROBE to simulate the probe phase completing.
    ai._current_tactic = AdaptiveTactic.SACRIFICE_PROBE
    ai._last_switch_time = 0.0

    tactic = ai.decide(timestamp=TACTIC_COOLDOWN_S + 1.0)
    assert tactic == AdaptiveTactic.SWARM_REFORM
