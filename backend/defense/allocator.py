"""
Defender allocation engine for BULWARK.

The allocator assigns finite Defender effectors to ranked Threats. Each defender
has a range, a capacity (remaining shots or simultaneous targets), a reload time,
a single-shot kill probability, and a per-engagement dollar cost. The allocator
honors range and capacity and aims to maximize expected threats neutralized.

Threat objects carry no position. They reference a track or a swarm. The caller
supplies a PositionResolver that maps a Threat to its current ENU position so the
allocator can apply range gates. A resolver that returns None for a threat means
the position is unknown and the defender cannot range it.

Allocators are pluggable. Allocator is the interface. GreedyAllocator is the
first implementation. The seam is clean for a future auction or Hungarian
allocator that solves the assignment problem to optimality.
"""
from __future__ import annotations

import logging
import math
import random
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from csontology import (
    Defender,
    DefenderStatus,
    Engagement,
    EngagementStatus,
    Threat,
    Vec3,
)

logger = logging.getLogger("overwatch.defense")


# A PositionResolver maps a Threat to its current ENU position, or None if the
# position is not known. The allocator uses it for range gating.
PositionResolver = Callable[[Threat], Optional[Vec3]]


# Fallback dollar value for a destroyed attacker when a threat carries no
# value_at_risk usable as attacker cost. A cheap FPV drone is on this order.
DEFAULT_THREAT_VALUE = 500.0


def _distance(a: Vec3, b: Vec3) -> float:
    """Euclidean distance between two ENU points in meters."""
    return math.sqrt(
        (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
    )


def _is_engageable(defender: Defender) -> bool:
    """True when a defender is ready and has capacity to engage."""
    return defender.status is DefenderStatus.READY and defender.capacity > 0


@dataclass
class CostLedger:
    """Running tally of dollars spent on defense versus dollars destroyed.

    defender_spent is the sum of engagement costs the allocator committed.
    attacker_destroyed is the sum of attacker value removed on confirmed hits.
    The cost-exchange ratio is defender dollars per attacker dollar killed.
    Below 1.0 means defense is cheaper than the attack, the goal of the system.
    """

    defender_spent: float = 0.0
    attacker_destroyed: float = 0.0
    hits: int = 0
    misses: int = 0
    leaks: int = 0

    def record_spend(self, cost: float) -> None:
        """Add a committed engagement cost to defender spend."""
        self.defender_spent += cost

    def record_outcome(self, status: EngagementStatus, attacker_value: float) -> None:
        """Tally one resolved engagement outcome and any value destroyed."""
        if status is EngagementStatus.HIT:
            self.hits += 1
            self.attacker_destroyed += attacker_value
        elif status is EngagementStatus.MISS:
            self.misses += 1
        elif status is EngagementStatus.LEAK:
            self.leaks += 1

    @property
    def cost_exchange_ratio(self) -> Optional[float]:
        """Defender dollars spent per attacker dollar destroyed.

        Returns None when nothing has been destroyed yet, since the ratio is
        undefined. Lower is better. Below 1.0 means defense wins on cost.
        """
        if self.attacker_destroyed <= 0.0:
            return None
        return self.defender_spent / self.attacker_destroyed


class Allocator(ABC):
    """Interface for any defender-to-threat allocation strategy.

    An allocator reads ranked threats and available defenders and returns a list
    of Engagement objects. It must respect each defender range and capacity. The
    returned engagements are PENDING. Outcomes are rolled later by resolve().
    """

    @abstractmethod
    def allocate(
        self,
        threats: List[Threat],
        defenders: List[Defender],
        now: float,
    ) -> List[Engagement]:
        """Assign defenders to threats and return PENDING engagements.

        threats are assumed ordered by priority, most dangerous first, matching
        Threat.priority_rank. defenders are the effectors that may be used. now
        is the shared world-model timestamp stamped on each engagement.
        """
        raise NotImplementedError


@dataclass
class _DefenderState:
    """Mutable per-allocation bookkeeping for one defender.

    Wraps a Defender so a single allocate() call can spend capacity without
    mutating the shared world-model object. assigned counts engagements made to
    this defender in the current pass.
    """

    defender: Defender
    remaining_capacity: int
    assigned: int = 0

    def can_engage(self) -> bool:
        """True while this defender still has capacity left in this pass."""
        return self.remaining_capacity > 0

    def commit(self) -> None:
        """Consume one capacity unit for an assignment."""
        self.remaining_capacity -= 1
        self.assigned += 1


class GreedyAllocator(Allocator):
    """Greedy allocator that walks threats by priority and picks the best defender.

    For each threat in priority order it considers every defender that is ready,
    has capacity, and is within range of the threat. Among those it picks the
    defender that maximizes a marginal score balancing kill probability against
    cost. This is a fast heuristic, not a global optimum. It degrades gracefully
    under saturation. Threats that find no in-range defender with capacity are
    left unengaged and surface as predicted leakers to the caller.

    The future auction or Hungarian allocator plugs in by subclassing Allocator
    and reusing the same range and capacity gates exposed here.
    """

    def __init__(
        self,
        resolve_position: Optional[PositionResolver] = None,
        cost_weight: float = 0.0001,
    ) -> None:
        """Build a greedy allocator.

        resolve_position maps a threat to its ENU position for range gating. When
        omitted, every threat is treated as in range of every defender, which
        suits unit tests that gate on capacity alone. cost_weight scales the
        dollar penalty in the marginal score so cheaper effectors win ties.
        """
        self._resolve_position = resolve_position
        self._cost_weight = cost_weight

    def allocate(
        self,
        threats: List[Threat],
        defenders: List[Defender],
        now: float,
    ) -> List[Engagement]:
        states = [
            _DefenderState(defender=d, remaining_capacity=d.capacity)
            for d in defenders
            if _is_engageable(d)
        ]
        engagements: List[Engagement] = []
        for threat in threats:
            position = self._threat_position(threat)
            best = self._best_defender_for(threat, position, states)
            if best is None:
                logger.debug("No defender available for threat %s", threat.id)
                continue
            best.commit()
            engagements.append(self._make_engagement(best.defender, threat, now))
        return engagements

    def _threat_position(self, threat: Threat) -> Optional[Vec3]:
        """Resolve a threat position, or None when no resolver is configured."""
        if self._resolve_position is None:
            return None
        return self._resolve_position(threat)

    def _best_defender_for(
        self,
        threat: Threat,
        position: Optional[Vec3],
        states: List[_DefenderState],
    ) -> Optional[_DefenderState]:
        """Pick the defender that maximizes marginal score for one threat.

        Returns None when no defender is ready, in range, and has capacity.
        """
        best_state: Optional[_DefenderState] = None
        best_score = -math.inf
        for state in states:
            if not state.can_engage():
                continue
            if not self._in_range(state.defender, position):
                continue
            score = self._marginal_score(state.defender, threat)
            if score > best_score:
                best_score = score
                best_state = state
        return best_state

    def _in_range(self, defender: Defender, position: Optional[Vec3]) -> bool:
        """True when the threat is within the defender reach.

        When the threat position is unknown the gate cannot be applied, so the
        defender is treated as in range and the kill probability carries the
        risk. This keeps the allocator usable without a position resolver.
        """
        if position is None:
            return True
        return _distance(defender.position, position) <= defender.range_m

    def _marginal_score(self, defender: Defender, threat: Threat) -> float:
        """Expected value of assigning this defender to this threat.

        Reward is threat score weighted by single-shot kill probability. A small
        cost penalty favors cheaper effectors so non-kinetic jammers win ties.
        """
        reward = threat.score * defender.kill_prob
        penalty = self._cost_weight * defender.unit_cost
        return reward - penalty

    def _make_engagement(
        self,
        defender: Defender,
        threat: Threat,
        now: float,
    ) -> Engagement:
        """Build one PENDING engagement with its committed cost."""
        return Engagement(
            id=f"eng-{uuid.uuid4().hex[:12]}",
            defender_id=defender.id,
            target_threat_id=threat.id,
            start_time=now,
            status=EngagementStatus.PENDING,
            cost=defender.unit_cost,
        )


def resolve(
    engagements: List[Engagement],
    defenders: List[Defender],
    threats: List[Threat],
    now: float,
    ledger: Optional[CostLedger] = None,
    rng: Optional[random.Random] = None,
) -> CostLedger:
    """Roll each PENDING engagement to an outcome and update the cost ledger.

    For every PENDING engagement we draw against the defender single-shot kill
    probability. A draw under kill_prob is a HIT and removes the attacker value.
    Otherwise it is a MISS. An engagement whose defender is unknown is marked
    LEAK, since the shot could not be taken. Already-resolved engagements are
    left untouched. The engagement objects are mutated in place. The returned
    ledger holds defender spend, attacker value destroyed, and the ratio.

    rng is injectable so tests can pin outcomes. now is the shared timestamp.
    """
    ledger = ledger if ledger is not None else CostLedger()
    rng = rng if rng is not None else random.Random()
    defender_by_id: Dict[str, Defender] = {d.id: d for d in defenders}
    value_by_threat = _attacker_values(threats)
    for engagement in engagements:
        if engagement.status is not EngagementStatus.PENDING:
            continue
        _resolve_one(engagement, defender_by_id, value_by_threat, ledger, rng)
    logger.info(
        "Resolved %d engagements at t=%.3f ratio=%s",
        len(engagements),
        now,
        ledger.cost_exchange_ratio,
    )
    return ledger


def _attacker_values(threats: List[Threat]) -> Dict[str, float]:
    """Map each threat id to the attacker dollar value it represents.

    Uses value_at_risk when positive, else a default per-drone value so a hit
    still credits the cost-exchange metric.
    """
    values: Dict[str, float] = {}
    for threat in threats:
        value = threat.value_at_risk if threat.value_at_risk > 0 else DEFAULT_THREAT_VALUE
        values[threat.id] = value
    return values


def _resolve_one(
    engagement: Engagement,
    defender_by_id: Dict[str, Defender],
    value_by_threat: Dict[str, float],
    ledger: CostLedger,
    rng: random.Random,
) -> None:
    """Resolve a single PENDING engagement and fold it into the ledger."""
    ledger.record_spend(engagement.cost)
    defender = defender_by_id.get(engagement.defender_id)
    if defender is None:
        engagement.status = EngagementStatus.LEAK
        ledger.record_outcome(EngagementStatus.LEAK, 0.0)
        return
    attacker_value = value_by_threat.get(engagement.target_threat_id, DEFAULT_THREAT_VALUE)
    if rng.random() < defender.kill_prob:
        engagement.status = EngagementStatus.HIT
        ledger.record_outcome(EngagementStatus.HIT, attacker_value)
    else:
        engagement.status = EngagementStatus.MISS
        ledger.record_outcome(EngagementStatus.MISS, attacker_value)
