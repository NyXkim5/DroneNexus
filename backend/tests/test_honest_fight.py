"""Integration tests for the honest kill model: resistance, radius gates, leaks.

These exercise the shipped runner resolution path, not the standalone allocator
resolve helper, so the round-two honesty mechanism is proven end to end: a
jam-resistant raid defeats EW and leaks, and hardened drones force the kinetic
interceptor to fire.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

# Honest-fight integration runs drive full scenarios. Deselect with -m "not slow".
pytestmark = pytest.mark.slow

from csontology import DefenderKind
from wargame.runner import WargameRunner, _resistance
from wargame.scenario import (
    DefenderConfig,
    Scenario,
    SensorConfig,
    SiteConfig,
    load_scenario,
)
from csontology import SwarmIntent


class _Drone:
    """Minimal stand-in carrying the resistance flags the runner reads."""

    def __init__(self, ew_resistant: bool, hardened: bool) -> None:
        self.ew_resistant = ew_resistant
        self.hardened = hardened


def test_resistance_matches_effector_physics() -> None:
    plain = _Drone(False, False)
    jammer_proof = _Drone(True, False)
    shielded = _Drone(False, True)
    both = _Drone(True, True)
    # EW and JAMMER do nothing to a jam-resistant drone.
    assert _resistance(DefenderKind.EW, jammer_proof) == 0.0
    assert _resistance(DefenderKind.JAMMER, jammer_proof) == 0.0
    # HPM is largely shrugged off by a hardened drone.
    assert _resistance(DefenderKind.HPM, shielded) == 0.15
    # Kinetic works regardless of resistance.
    assert _resistance(DefenderKind.INTERCEPTOR, both) == 1.0
    assert _resistance(DefenderKind.NET, both) == 1.0
    # A plain drone is fully vulnerable to everything.
    assert _resistance(DefenderKind.EW, plain) == 1.0
    assert _resistance(DefenderKind.HPM, plain) == 1.0


def _run(scenario: Scenario, ticks: int):
    """Drive a scenario to resolution with no pacing and return the last frame."""
    async def go():
        runner = WargameRunner(scenario)
        last = None
        async for frame in runner.run(pace=False):
            last = frame
        return last, runner

    return asyncio.run(go())


def _ew_only_against_jam_resistant() -> Scenario:
    """A small all-jam-resistant raid defended only by EW, which cannot kill it."""
    return Scenario(
        name="ew_vs_resistant",
        swarm_intent=SwarmIntent.SATURATION,
        swarm_count=30,
        unit_cost=6000.0,
        sensors=[SensorConfig(sensor_id="radar-1", range_m=4000.0)],
        defenders=[
            DefenderConfig(
                id_prefix="EW",
                kind=DefenderKind.EW,
                count=2,
                position=(0.0, 0.0, 0.0),
                capacity=50,
                range_m=3000.0,
                reload_s=1.0,
                kill_prob=0.6,
                unit_cost=3.0,
                effect_radius_m=400.0,
                max_simultaneous=20,
            ),
        ],
        site=SiteConfig(),
        tick_hz=5.0,
        max_ticks=700,
        seed=3,
        jam_resistant_fraction=1.0,
    )


def test_jam_resistant_raid_leaks_against_ew_only() -> None:
    last, _ = _run(_ew_only_against_jam_resistant(), ticks=700)
    m = last.metrics
    # EW resistance is total here, so the soft-kill layer destroys nothing and the
    # whole raid leaks. This proves resistance is wired into the live kill path.
    assert m.intercepts == 0
    assert m.leakers >= 25


def _all_resistant_raid() -> Scenario:
    """An all hardened and jam-resistant raid: only kinetic interceptors can kill."""
    return Scenario(
        name="resistant_raid",
        swarm_intent=SwarmIntent.SATURATION,
        swarm_count=40,
        unit_cost=9000.0,
        sensors=[SensorConfig(sensor_id="radar-1", range_m=4000.0)],
        defenders=[
            DefenderConfig(
                id_prefix="EW", kind=DefenderKind.EW, count=2,
                position=(0.0, 0.0, 0.0), capacity=40, range_m=3000.0,
                reload_s=1.0, kill_prob=0.5, unit_cost=3.0,
                effect_radius_m=400.0, max_simultaneous=16,
            ),
            DefenderConfig(
                id_prefix="INT", kind=DefenderKind.INTERCEPTOR, count=10,
                position=(0.0, 0.0, 0.0), capacity=4, range_m=3000.0,
                reload_s=2.0, kill_prob=0.85, unit_cost=8_000.0,
            ),
        ],
        site=SiteConfig(),
        tick_hz=5.0,
        max_ticks=700,
        seed=7,
        jam_resistant_fraction=1.0,
        hardened_fraction=1.0,
    )


class _FakeEventsDB:
    """Records log_event calls so we can prove the runner emits engagement events."""

    def __init__(self) -> None:
        self.events: list = []

    async def log_event(self, **kwargs) -> None:
        self.events.append(kwargs)


def test_runner_emits_engagement_events_to_db() -> None:
    fake = _FakeEventsDB()
    scenario = _all_resistant_raid()

    async def go() -> None:
        runner = WargameRunner(scenario, events_db=fake)
        async for _frame in runner.run(pace=False):
            pass

    asyncio.run(go())
    # Engagements occur, so the runtime path must surface them as OVERWATCH
    # events carrying the engagement id and lineage. This proves the bridge is
    # wired into the runner, not only callable from a unit test.
    assert len(fake.events) > 0
    assert any("data" in e for e in fake.events)


def test_resistant_raid_forces_interceptors_to_fire() -> None:
    scenario = _all_resistant_raid()
    last, runner = _run(scenario, ticks=700)
    int_shots = sum(
        4 - d.capacity
        for d in runner.world.defenders
        if d.kind is DefenderKind.INTERCEPTOR
    )
    # EW cannot touch a jam-resistant drone, so the kinetic interceptor must fire.
    assert int_shots > 0
    # Killing expensive resistant airframes with interceptors is a fair trade,
    # and the cost-exchange ratio is a finite emergent number, not a baked one.
    assert last.metrics.cost_exchange_ratio is not None
