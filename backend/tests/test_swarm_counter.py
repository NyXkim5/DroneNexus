"""Tests for the Swarm Forge counter-intercept planner."""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from defense.swarm_counter import (
    DefenderDrone,
    FormationPattern,
    HostileTrack,
    SwarmCounterPlanner,
    compute_intercept_point,
    hungarian_assign,
    select_formation,
    _distance,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hostile(hid: str, pos: tuple, vel: tuple = (0, 0, 0)) -> HostileTrack:
    return HostileTrack(id=hid, position=pos, velocity=vel)


def _defender(did: str, pos: tuple, speed: float = 25.0) -> DefenderDrone:
    return DefenderDrone(id=did, position=pos, speed=speed)


# ---------------------------------------------------------------------------
# Hungarian assignment
# ---------------------------------------------------------------------------

def test_hungarian_1x1():
    pairs = hungarian_assign([[5.0]])
    assert pairs == [(0, 0)]


def test_hungarian_2x2_optimal():
    """Verify the assignment minimizes total cost, not per-row cost."""
    cost = [
        [1.0, 100.0],
        [2.0, 3.0],
    ]
    pairs = hungarian_assign(cost)
    assigned = dict(pairs)
    # Optimal: row0->col0 (1), row1->col1 (3) = total 4
    assert assigned[0] == 0
    assert assigned[1] == 1


def test_hungarian_3x3():
    cost = [
        [10.0, 5.0, 13.0],
        [3.0, 7.0, 15.0],
        [12.0, 11.0, 2.0],
    ]
    pairs = hungarian_assign(cost)
    total = sum(cost[r][c] for r, c in pairs)
    # Optimal assignment: 0->1(5), 1->0(3), 2->2(2) = 10
    assert total == 10.0


def test_hungarian_empty():
    assert hungarian_assign([]) == []


def test_hungarian_rectangular_more_cols():
    """More hostiles than defenders yields partial assignment."""
    cost = [
        [10.0, 1.0, 5.0],
    ]
    pairs = hungarian_assign(cost)
    assert len(pairs) == 1
    assert pairs[0] == (0, 1)


# ---------------------------------------------------------------------------
# Intercept point computation
# ---------------------------------------------------------------------------

def test_intercept_stationary_target():
    """Stationary hostile: intercept point equals hostile position."""
    hostile = _hostile("h1", (100.0, 0.0, 50.0))
    defender = _defender("d1", (0.0, 0.0, 50.0), speed=25.0)
    point, eta = compute_intercept_point(hostile, defender, launch_delay=0.0)
    assert abs(point[0] - 100.0) < 0.01
    assert abs(point[1] - 0.0) < 0.01
    assert eta > 0


def test_intercept_moving_target():
    """Moving hostile: intercept point is ahead of current position."""
    hostile = _hostile("h1", (200.0, 0.0, 50.0), vel=(-10.0, 0.0, 0.0))
    defender = _defender("d1", (0.0, 0.0, 50.0), speed=25.0)
    point, eta = compute_intercept_point(hostile, defender, launch_delay=0.0)
    # Hostile moves toward defender, intercept should be closer than 200m
    assert point[0] < 200.0


def test_intercept_with_launch_delay():
    """Launch delay pushes the intercept point further along hostile path."""
    hostile = _hostile("h1", (200.0, 0.0, 50.0), vel=(10.0, 0.0, 0.0))
    defender = _defender("d1", (0.0, 0.0, 50.0), speed=25.0)
    pt_no_delay, _ = compute_intercept_point(hostile, defender, launch_delay=0.0)
    pt_delayed, _ = compute_intercept_point(hostile, defender, launch_delay=5.0)
    # With delay the hostile travels further along +X
    assert pt_delayed[0] > pt_no_delay[0]


def test_intercept_eta_positive():
    hostile = _hostile("h1", (500.0, 500.0, 30.0), vel=(-5.0, -5.0, 0.0))
    defender = _defender("d1", (0.0, 0.0, 30.0), speed=20.0)
    _, eta = compute_intercept_point(hostile, defender, launch_delay=2.0)
    assert eta > 2.0  # at least the launch delay


# ---------------------------------------------------------------------------
# Formation pattern selection
# ---------------------------------------------------------------------------

def test_screen_when_site_defended_and_outnumber():
    hostiles = [_hostile(f"h{i}", (500.0, float(i * 20), 50.0)) for i in range(3)]
    defenders = [_defender(f"d{i}", (0.0, float(i * 10), 50.0)) for i in range(4)]
    pattern = select_formation(hostiles, defenders, defended_site=(0.0, 0.0, 0.0))
    assert pattern == FormationPattern.SCREEN


def test_pincer_when_enough_defenders_and_spread():
    hostiles = [
        _hostile("h0", (500.0, -100.0, 50.0)),
        _hostile("h1", (500.0, 100.0, 50.0)),
    ]
    defenders = [_defender(f"d{i}", (0.0, float(i * 10), 50.0)) for i in range(4)]
    pattern = select_formation(hostiles, defenders, defended_site=None)
    assert pattern == FormationPattern.PINCER


def test_line_abreast_default():
    hostiles = [_hostile("h0", (500.0, 0.0, 50.0))]
    defenders = [_defender("d0", (0.0, 0.0, 50.0))]
    pattern = select_formation(hostiles, defenders, defended_site=None)
    assert pattern == FormationPattern.LINE_ABREAST


# ---------------------------------------------------------------------------
# SwarmCounterPlanner end-to-end
# ---------------------------------------------------------------------------

def test_planner_basic_assignment():
    hostiles = [
        _hostile("h0", (100.0, 0.0, 50.0)),
        _hostile("h1", (0.0, 100.0, 50.0)),
    ]
    defenders = [
        _defender("d0", (80.0, 0.0, 50.0)),
        _defender("d1", (0.0, 80.0, 50.0)),
    ]
    planner = SwarmCounterPlanner(launch_delay=0.0)
    orders = planner.plan(hostiles, defenders)
    assert len(orders) == 2
    pairs = {o.defender_id: o.target_id for o in orders}
    # d0 is closest to h0, d1 is closest to h1
    assert pairs["d0"] == "h0"
    assert pairs["d1"] == "h1"


def test_planner_more_hostiles_than_defenders():
    """Planner picks the closest threats when outnumbered."""
    hostiles = [
        _hostile("far", (1000.0, 0.0, 50.0)),
        _hostile("close", (100.0, 0.0, 50.0)),
        _hostile("mid", (500.0, 0.0, 50.0)),
    ]
    defenders = [_defender("d0", (0.0, 0.0, 50.0))]
    planner = SwarmCounterPlanner(launch_delay=0.0)
    orders = planner.plan(hostiles, defenders)
    assert len(orders) == 1
    assert orders[0].target_id == "close"


def test_planner_empty_inputs():
    planner = SwarmCounterPlanner()
    assert planner.plan([], []) == []
    assert planner.plan([_hostile("h0", (0, 0, 0))], []) == []
    assert planner.plan([], [_defender("d0", (0, 0, 0))]) == []


def test_planner_all_orders_have_eta():
    hostiles = [_hostile(f"h{i}", (float(i * 100), 0.0, 50.0)) for i in range(5)]
    defenders = [_defender(f"d{i}", (0.0, float(i * 50), 50.0)) for i in range(5)]
    planner = SwarmCounterPlanner(launch_delay=2.0)
    orders = planner.plan(hostiles, defenders)
    for order in orders:
        assert order.eta_s >= 2.0
        assert order.intercept_point is not None


def test_planner_formation_exposed():
    hostiles = [_hostile(f"h{i}", (500.0, float(i * 20), 50.0)) for i in range(3)]
    defenders = [_defender(f"d{i}", (0.0, float(i * 10), 50.0)) for i in range(5)]
    planner = SwarmCounterPlanner(defended_site=(0.0, 0.0, 0.0))
    pattern = planner.select_formation(hostiles, defenders)
    assert pattern == FormationPattern.SCREEN
