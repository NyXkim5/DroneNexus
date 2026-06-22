"""Swarm-vs-swarm counter-intercept planner for OVERWATCH (Swarm Forge).

Coordinates multiple defender drones as a unified swarm to intercept an
attacking swarm. Uses the Hungarian algorithm for optimal assignment,
computes predictive intercept points, and selects formation patterns
based on swarm geometry.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

from csontology import Vec3

logger = logging.getLogger("overwatch.swarm_counter")

DEFAULT_DEFENDER_SPEED = 25.0  # m/s
DEFAULT_LAUNCH_DELAY = 2.0  # seconds


class FormationPattern(str, Enum):
    """Intercept formation geometries for the defender swarm."""
    LINE_ABREAST = "LINE_ABREAST"
    PINCER = "PINCER"
    SCREEN = "SCREEN"


@dataclass
class HostileTrack:
    """Minimal hostile track for intercept planning."""
    id: str
    position: Vec3
    velocity: Vec3


@dataclass
class DefenderDrone:
    """Available defender drone for swarm assignment."""
    id: str
    position: Vec3
    speed: float = DEFAULT_DEFENDER_SPEED


@dataclass
class InterceptOrder:
    """One defender assigned to intercept one hostile."""
    defender_id: str
    target_id: str
    intercept_point: Vec3
    eta_s: float


def _distance(a: Vec3, b: Vec3) -> float:
    return math.sqrt(
        (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
    )


def _vec_scale(v: Vec3, s: float) -> Vec3:
    return (v[0] * s, v[1] * s, v[2] * s)


def _vec_add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def compute_intercept_point(
    hostile: HostileTrack,
    defender: DefenderDrone,
    launch_delay: float = DEFAULT_LAUNCH_DELAY,
) -> Tuple[Vec3, float]:
    """Predict where the hostile will be when the defender arrives.

    Iterative refinement: estimate flight time from distance, project
    hostile forward by flight_time + launch_delay, recompute.
    Returns (intercept_point, eta_seconds).
    """
    pos = hostile.position
    total_time = launch_delay
    for _ in range(3):
        dist = _distance(defender.position, pos)
        if defender.speed <= 0:
            break
        total_time = dist / defender.speed + launch_delay
        pos = _vec_add(hostile.position, _vec_scale(hostile.velocity, total_time))
    eta = total_time if defender.speed > 0 else float("inf")
    return pos, eta


def _build_cost_matrix(
    defenders: List[DefenderDrone],
    hostiles: List[HostileTrack],
    launch_delay: float,
) -> List[List[float]]:
    """Build distance-based cost matrix for Hungarian assignment."""
    matrix: List[List[float]] = []
    for defender in defenders:
        row: List[float] = []
        for hostile in hostiles:
            pt, _ = compute_intercept_point(hostile, defender, launch_delay)
            row.append(_distance(defender.position, pt))
        matrix.append(row)
    return matrix


def hungarian_assign(cost_matrix: List[List[float]]) -> List[Tuple[int, int]]:
    """Optimal assignment via scipy, greedy fallback if unavailable."""
    rows = len(cost_matrix)
    cols = len(cost_matrix[0]) if rows > 0 else 0
    if rows == 0 or cols == 0:
        return []
    try:
        from scipy.optimize import linear_sum_assignment
        row_ind, col_ind = linear_sum_assignment(cost_matrix)
        return list(zip(row_ind.tolist(), col_ind.tolist()))
    except ImportError:
        return _greedy_assign(cost_matrix)


def _greedy_assign(cost_matrix: List[List[float]]) -> List[Tuple[int, int]]:
    """Greedy fallback: globally sort all cells, assign unused pairs."""
    candidates = [
        (cost_matrix[r][c], r, c)
        for r in range(len(cost_matrix))
        for c in range(len(cost_matrix[0]))
    ]
    candidates.sort()
    used_rows: set[int] = set()
    used_cols: set[int] = set()
    pairs: List[Tuple[int, int]] = []
    for _, r, c in candidates:
        if r in used_rows or c in used_cols:
            continue
        pairs.append((r, c))
        used_rows.add(r)
        used_cols.add(c)
    return pairs


def _swarm_centroid(hostiles: List[HostileTrack]) -> Vec3:
    n = len(hostiles)
    if n == 0:
        return (0.0, 0.0, 0.0)
    return (
        sum(h.position[0] for h in hostiles) / n,
        sum(h.position[1] for h in hostiles) / n,
        sum(h.position[2] for h in hostiles) / n,
    )


def _swarm_spread(hostiles: List[HostileTrack]) -> float:
    centroid = _swarm_centroid(hostiles)
    if not hostiles:
        return 0.0
    return max(_distance(h.position, centroid) for h in hostiles)


def select_formation(
    hostiles: List[HostileTrack],
    defenders: List[DefenderDrone],
    defended_site: Optional[Vec3] = None,
) -> FormationPattern:
    """Auto-select intercept formation based on geometry.

    SCREEN when defenders outnumber hostiles and a site needs shielding.
    PINCER when defenders can split into flanking groups (>= 4) and spread > 50m.
    LINE_ABREAST as the default head-on engagement pattern.
    """
    spread = _swarm_spread(hostiles)
    n_def = len(defenders)
    if defended_site is not None and n_def >= len(hostiles):
        return FormationPattern.SCREEN
    if n_def >= 4 and spread > 50.0:
        return FormationPattern.PINCER
    return FormationPattern.LINE_ABREAST


class SwarmCounterPlanner:
    """Plans coordinated swarm-vs-swarm intercepts."""

    def __init__(
        self,
        launch_delay: float = DEFAULT_LAUNCH_DELAY,
        defended_site: Optional[Vec3] = None,
    ) -> None:
        self._launch_delay = launch_delay
        self._defended_site = defended_site

    def plan(
        self,
        hostiles: List[HostileTrack],
        defenders: List[DefenderDrone],
    ) -> List[InterceptOrder]:
        """Produce optimal intercept orders for all available defenders."""
        if not hostiles or not defenders:
            return []
        targets = self._prioritize(hostiles, defenders)
        cost_matrix = _build_cost_matrix(defenders, targets, self._launch_delay)
        assignments = hungarian_assign(cost_matrix)
        return self._build_orders(assignments, defenders, targets)

    def select_formation(
        self,
        hostiles: List[HostileTrack],
        defenders: List[DefenderDrone],
    ) -> FormationPattern:
        """Expose formation selection for callers."""
        return select_formation(hostiles, defenders, self._defended_site)

    def _prioritize(
        self,
        hostiles: List[HostileTrack],
        defenders: List[DefenderDrone],
    ) -> List[HostileTrack]:
        """When hostiles outnumber defenders, pick closest threats."""
        if len(hostiles) <= len(defenders):
            return list(hostiles)
        center = _swarm_centroid(
            [HostileTrack("d", d.position, (0, 0, 0)) for d in defenders],
        )
        ranked = sorted(hostiles, key=lambda h: _distance(h.position, center))
        return ranked[: len(defenders)]

    def _build_orders(
        self,
        assignments: List[Tuple[int, int]],
        defenders: List[DefenderDrone],
        targets: List[HostileTrack],
    ) -> List[InterceptOrder]:
        """Convert assignment pairs into InterceptOrder objects."""
        orders: List[InterceptOrder] = []
        for def_idx, tgt_idx in assignments:
            defender = defenders[def_idx]
            hostile = targets[tgt_idx]
            point, eta = compute_intercept_point(
                hostile, defender, self._launch_delay,
            )
            orders.append(InterceptOrder(
                defender_id=defender.id,
                target_id=hostile.id,
                intercept_point=point,
                eta_s=round(eta, 2),
            ))
        return orders
