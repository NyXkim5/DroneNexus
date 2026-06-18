"""
Adversarial adaptive AI for the BULWARK/OVERWATCH red force.

This module is a standalone decision unit. It observes defender behavior and
chooses a tactic each tick. The tactic drives per-drone target waypoints that
the flocking system then executes.

The AI is stateful but has no side effects. It only reads observations and
returns waypoints. The runner feeds it world-state observations and applies
the returned waypoints to the swarm.

Coordinate frame: ENU meters about the site origin, same as csontology.Vec3.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

from csontology import Vec3

logger = logging.getLogger("overwatch.attacker.adaptive")

# Minimum seconds between tactic switches (hysteresis).
TACTIC_COOLDOWN_S = 10.0
# Engagement rate (kills/s) above which losses are considered severe.
HIGH_ENGAGEMENT_RATE = 0.5
# Loss fraction above which the AI considers itself taking heavy casualties.
HIGH_LOSS_FRACTION = 0.50
# Minimum distance to classify a region as a coverage gap, in meters.
GAP_DISTANCE_M = 400.0
# Fraction of drones used as decoys in FEINT_AND_STRIKE.
FEINT_DECOY_FRACTION = 0.30
# Fraction of drones sent as scouts in SACRIFICE_PROBE.
PROBE_SCOUT_FRACTION = 0.10
# Spread radius for probe scouts, in meters.
PROBE_SPREAD_M = 300.0
# Flanking angle offset for SPLIT_AND_FLANK groups, in radians.
FLANK_ANGLE_RAD = math.pi / 3.0  # 60 degrees
# Number of flanking groups.
FLANK_GROUP_COUNT = 3
# LOW_AND_SLOW altitude in meters AGL.
LOW_ALTITUDE_M = 10.0
# LOW_AND_SLOW speed fraction of normal approach speed.
LOW_SPEED_FRACTION = 0.5


class AdaptiveTactic(Enum):
    SATURATE = "saturate"
    FEINT_AND_STRIKE = "feint"
    SPLIT_AND_FLANK = "split"
    LOW_AND_SLOW = "low_slow"
    SACRIFICE_PROBE = "probe"
    SWARM_REFORM = "reform"


@dataclass
class AttackerObservation:
    """What the attacker can observe about the defender."""
    drones_destroyed: int
    drones_remaining: int
    drones_leaked_through: int
    active_effectors: int
    effector_positions: List[Vec3]
    engagement_rate: float  # kills per second
    coverage_gaps: List[Vec3]  # areas with low defender density in ENU meters


class AdaptiveAttackerAI:
    """Observes defender behavior and adapts tactics in real-time.

    The AI is a pure decision module. It takes observations and returns
    waypoints. It does not move drones or modify world state.

    aggression: 0-1, higher means more willing to take losses for objectives.
    adaptation_rate: how fast the tactic score shifts toward new evidence (0-1).
    """

    def __init__(
        self,
        aggression: float = 0.7,
        adaptation_rate: float = 0.1,
    ) -> None:
        if not 0.0 <= aggression <= 1.0:
            raise ValueError(f"aggression must be in [0, 1], got {aggression}")
        if not 0.0 <= adaptation_rate <= 1.0:
            raise ValueError(
                f"adaptation_rate must be in [0, 1], got {adaptation_rate}"
            )
        self._aggression = aggression
        self._adaptation_rate = adaptation_rate
        self._current_tactic = AdaptiveTactic.SATURATE
        self._tactic_history: List[Tuple[float, AdaptiveTactic]] = []
        self._observations: List[AttackerObservation] = []
        self._last_switch_time: Optional[float] = None
        self._discovered_weak_point: Optional[Vec3] = None

    # ---- public API ----

    def observe(self, obs: AttackerObservation) -> None:
        """Record a defender state observation."""
        self._observations.append(obs)
        if obs.coverage_gaps:
            self._discovered_weak_point = obs.coverage_gaps[0]

    def decide(self, timestamp: float) -> AdaptiveTactic:
        """Choose a tactic based on accumulated observations.

        Switching is gated by a 10-second hysteresis to prevent oscillation.
        The most recent observation drives the decision.
        """
        if not self._observations:
            return self._current_tactic

        # Enforce hysteresis.
        if (
            self._last_switch_time is not None
            and timestamp - self._last_switch_time < TACTIC_COOLDOWN_S
        ):
            return self._current_tactic

        candidate = self._evaluate(self._observations[-1], timestamp)
        if candidate != self._current_tactic:
            self._current_tactic = candidate
            self._last_switch_time = timestamp
            self._tactic_history.append((timestamp, candidate))
            logger.info(
                "Tactic switch at t=%.1f -> %s", timestamp, candidate.value
            )

        return self._current_tactic

    def apply_tactic(
        self,
        tactic: AdaptiveTactic,
        drone_positions: List[Vec3],
        drone_velocities: List[Vec3],
        target: Vec3,
    ) -> List[Vec3]:
        """Return one target waypoint per drone for the given tactic.

        The output list length always matches the input drone_positions length.
        The flocking system steers each drone toward its assigned waypoint.
        """
        if not drone_positions:
            return []

        handlers = {
            AdaptiveTactic.SATURATE: self._apply_saturate,
            AdaptiveTactic.FEINT_AND_STRIKE: self._apply_feint_and_strike,
            AdaptiveTactic.SPLIT_AND_FLANK: self._apply_split_and_flank,
            AdaptiveTactic.LOW_AND_SLOW: self._apply_low_and_slow,
            AdaptiveTactic.SACRIFICE_PROBE: self._apply_sacrifice_probe,
            AdaptiveTactic.SWARM_REFORM: self._apply_swarm_reform,
        }
        return handlers[tactic](drone_positions, drone_velocities, target)

    @property
    def current_tactic(self) -> AdaptiveTactic:
        return self._current_tactic

    @property
    def tactic_history(self) -> List[Tuple[float, AdaptiveTactic]]:
        return list(self._tactic_history)

    # ---- decision logic ----

    def _evaluate(
        self, obs: AttackerObservation, timestamp: float
    ) -> AdaptiveTactic:
        """Select the best tactic given the latest observation."""
        total = obs.drones_destroyed + obs.drones_remaining
        loss_fraction = obs.drones_destroyed / max(1, total)
        high_losses = loss_fraction > HIGH_LOSS_FRACTION
        high_rate = obs.engagement_rate > HIGH_ENGAGEMENT_RATE
        has_gaps = bool(obs.coverage_gaps)
        effectors_clustered = self._effectors_are_clustered(obs.effector_positions)
        completely_stalled = (
            obs.drones_leaked_through == 0
            and obs.drones_remaining > 0
            and high_losses
        )

        # Highest-priority: discovered weak point from a prior probe.
        if self._discovered_weak_point is not None and self._current_tactic == AdaptiveTactic.SACRIFICE_PROBE:
            return AdaptiveTactic.SWARM_REFORM

        # Completely stalled: send scouts to map defenses.
        if completely_stalled and not has_gaps:
            return AdaptiveTactic.SACRIFICE_PROBE

        # Coverage gaps found: exploit them directly.
        if has_gaps and effectors_clustered:
            return AdaptiveTactic.FEINT_AND_STRIKE

        if has_gaps:
            return AdaptiveTactic.SPLIT_AND_FLANK

        # Taking heavy losses: need a different approach vector.
        if high_losses and high_rate:
            if effectors_clustered:
                return AdaptiveTactic.FEINT_AND_STRIKE
            return AdaptiveTactic.SPLIT_AND_FLANK

        # Low losses but nothing getting through: try stealth approach.
        if not high_losses and obs.drones_leaked_through == 0 and obs.drones_remaining > 0:
            return AdaptiveTactic.LOW_AND_SLOW

        return AdaptiveTactic.SATURATE

    def _effectors_are_clustered(self, positions: List[Vec3]) -> bool:
        """Return True if effector positions are tightly grouped.

        Clustered means the mean pairwise distance is less than GAP_DISTANCE_M.
        With fewer than two effectors there is no spread to exploit.
        """
        if len(positions) < 2:
            return False
        total_dist = 0.0
        count = 0
        for i in range(len(positions)):
            for j in range(i + 1, len(positions)):
                total_dist += _distance(positions[i], positions[j])
                count += 1
        mean_dist = total_dist / count
        return mean_dist < GAP_DISTANCE_M

    # ---- tactic application ----

    def _apply_saturate(
        self,
        drone_positions: List[Vec3],
        drone_velocities: List[Vec3],
        target: Vec3,
    ) -> List[Vec3]:
        """All drones aim directly at the target."""
        return [target for _ in drone_positions]

    def _apply_feint_and_strike(
        self,
        drone_positions: List[Vec3],
        drone_velocities: List[Vec3],
        target: Vec3,
    ) -> List[Vec3]:
        """30% aim at the strongest effector cluster (decoy), 70% aim at the gap.

        If no weak point is known, the strike group targets the main target.
        """
        n = len(drone_positions)
        decoy_count = max(1, round(n * FEINT_DECOY_FRACTION))
        strike_target = self._discovered_weak_point if self._discovered_weak_point else target

        # Decoy target: centroid of known effector positions, or offset from target.
        if self._observations and self._observations[-1].effector_positions:
            decoy_target = _centroid(self._observations[-1].effector_positions)
        else:
            decoy_target = _offset(target, 200.0, 0.0)

        waypoints: List[Vec3] = []
        for i, pos in enumerate(drone_positions):
            if i < decoy_count:
                waypoints.append(decoy_target)
            else:
                waypoints.append(strike_target)
        return waypoints

    def _apply_split_and_flank(
        self,
        drone_positions: List[Vec3],
        drone_velocities: List[Vec3],
        target: Vec3,
    ) -> List[Vec3]:
        """Divide into FLANK_GROUP_COUNT groups approaching from different angles.

        Each group gets a waypoint that is the target offset by a different
        bearing angle, so groups converge from multiple directions.
        """
        n = len(drone_positions)
        waypoints: List[Vec3] = []
        for i, pos in enumerate(drone_positions):
            group = i % FLANK_GROUP_COUNT
            angle_offset = group * (2.0 * math.pi / FLANK_GROUP_COUNT)
            approach = _approach_waypoint(pos, target, angle_offset)
            waypoints.append(approach)
        return waypoints

    def _apply_low_and_slow(
        self,
        drone_positions: List[Vec3],
        drone_velocities: List[Vec3],
        target: Vec3,
    ) -> List[Vec3]:
        """All drones descend to LOW_ALTITUDE_M AGL and approach at reduced speed.

        The waypoint has the same x/y as target but z clamped to LOW_ALTITUDE_M.
        The caller's flocking system must also reduce speed; this module signals
        intent via the low z coordinate.
        """
        low_target = (target[0], target[1], LOW_ALTITUDE_M)
        return [low_target for _ in drone_positions]

    def _apply_sacrifice_probe(
        self,
        drone_positions: List[Vec3],
        drone_velocities: List[Vec3],
        target: Vec3,
    ) -> List[Vec3]:
        """Send 10% of force ahead spread out; rest holds back at current position.

        Scouts advance spread across a wide front to map defense positions.
        The remaining drones hold in place until the AI switches tactic.
        """
        n = len(drone_positions)
        scout_count = max(1, round(n * PROBE_SCOUT_FRACTION))
        waypoints: List[Vec3] = []
        for i, pos in enumerate(drone_positions):
            if i < scout_count:
                # Spread scouts laterally across the front.
                spread_offset = (i - scout_count / 2.0) * (PROBE_SPREAD_M / max(1, scout_count))
                waypoints.append((
                    target[0] + spread_offset,
                    target[1],
                    target[2],
                ))
            else:
                # Hold in place.
                waypoints.append(pos)
        return waypoints

    def _apply_swarm_reform(
        self,
        drone_positions: List[Vec3],
        drone_velocities: List[Vec3],
        target: Vec3,
    ) -> List[Vec3]:
        """All drones converge on the discovered weak point.

        Falls back to the main target if no weak point is known.
        """
        rally = self._discovered_weak_point if self._discovered_weak_point else target
        return [rally for _ in drone_positions]


# ---- Vec3 math helpers ----

def _distance(a: Vec3, b: Vec3) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _centroid(positions: List[Vec3]) -> Vec3:
    n = len(positions)
    return (
        sum(p[0] for p in positions) / n,
        sum(p[1] for p in positions) / n,
        sum(p[2] for p in positions) / n,
    )


def _offset(point: Vec3, dx: float, dy: float) -> Vec3:
    return (point[0] + dx, point[1] + dy, point[2])


def _approach_waypoint(pos: Vec3, target: Vec3, angle_offset: float) -> Vec3:
    """Compute a waypoint that approaches target from a rotated bearing.

    The drone approaches from the direction of pos relative to target, rotated
    by angle_offset radians, passing through a midpoint en route. This creates
    a multi-angle flanking approach without requiring the caller to change speed.
    """
    dx = pos[0] - target[0]
    dy = pos[1] - target[1]
    dist = math.sqrt(dx * dx + dy * dy)
    if dist < 1.0:
        return target
    bearing = math.atan2(dy, dx) + angle_offset
    # Waypoint is halfway between current distance and the target, on the new bearing.
    mid_dist = dist * 0.5
    wx = target[0] + mid_dist * math.cos(bearing)
    wy = target[1] + mid_dist * math.sin(bearing)
    return (wx, wy, target[2])
