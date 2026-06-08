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
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from csontology import (
    Defender,
    DefenderStatus,
    Engagement,
    EngagementStatus,
    SwarmIntent,
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
    returned engagements are PENDING. The WargameRunner resolves their outcomes,
    rolling each shot against the effector kill probability and target resistance.
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


class _Grid:
    """Tiny uniform spatial hash for counting threats near an aim point."""

    def __init__(self, cell_m: float) -> None:
        self._cell = max(1.0, cell_m)
        self._buckets: Dict[tuple, List[int]] = {}

    def insert(self, index: int, pos: Vec3) -> None:
        self._buckets.setdefault(self._key(pos), []).append(index)

    def neighbors(self, pos: Vec3) -> List[int]:
        cx, cy, cz = self._key(pos)
        out: List[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    out.extend(self._buckets.get((cx + dx, cy + dy, cz + dz), ()))
        return out

    def _key(self, pos: Vec3) -> tuple:
        c = self._cell
        return (int(pos[0] // c), int(pos[1] // c), int(pos[2] // c))


class LayeredAllocator(Allocator):
    """Layered counter-swarm allocation: cheap area effects first, kinetic last.

    Phase one assigns non-kinetic area effectors (effect_radius_m > 0) to the
    densest clusters of unengaged threats, maximizing drones neutralized per shot.
    This is a greedy max-coverage approximation, the layer that wins on cost since
    one reusable shot kills many drones. Phase two assigns the remaining point
    effectors to the highest-value surviving threats with an auction that solves
    the assignment near optimally. Kinetic interceptors are the expensive last
    resort for leakers. This is the optimization a human operator cannot do at
    swarm scale: set cover plus optimal assignment, every tick, in milliseconds.
    """

    def __init__(
        self,
        resolve_position: Optional[PositionResolver] = None,
        cost_weight: float = 1e-6,
        auction_eps: float = 1e-3,
        imminent_s: float = 12.0,
        attacker_cost_ref: float = 500.0,
        min_kinetic_confidence: float = 0.4,
    ) -> None:
        self._resolve_position = resolve_position
        self._cost_weight = cost_weight
        self._auction_eps = auction_eps
        self._imminent_s = imminent_s
        self._attacker_cost_ref = attacker_cost_ref
        self._min_kinetic_confidence = min_kinetic_confidence

    def allocate(
        self,
        threats: List[Threat],
        defenders: List[Defender],
        now: float,
    ) -> List[Engagement]:
        positions = self._threat_positions(threats)
        ranked = [t for t in threats if positions.get(t.id) is not None]
        engaged: set = set()
        engagements: List[Engagement] = []
        area = [d for d in defenders if _is_engageable(d) and d.effect_radius_m > 0.0]
        point = [d for d in defenders if _is_engageable(d) and d.effect_radius_m <= 0.0]
        engagements += self._area_phase(area, ranked, positions, engaged, now)
        engagements += self._point_phase(point, ranked, positions, engaged, now)
        return engagements

    def _threat_positions(self, threats: List[Threat]) -> Dict[str, Optional[Vec3]]:
        """Resolve every threat position once for the whole pass."""
        if self._resolve_position is None:
            return {t.id: t.id and None for t in threats}
        return {t.id: self._resolve_position(t) for t in threats}

    def _area_phase(
        self,
        defenders: List[Defender],
        threats: List[Threat],
        positions: Dict[str, Optional[Vec3]],
        engaged: set,
        now: float,
    ) -> List[Engagement]:
        """Greedy max-coverage: each cheap area shot covers the densest cluster."""
        engagements: List[Engagement] = []
        order = sorted(defenders, key=self._cost_per_kill)
        for defender in order:
            shots = defender.capacity
            while shots > 0:
                aim, covered = self._best_aim(defender, threats, positions, engaged)
                if aim is None or not covered:
                    break
                for tid in covered:
                    engaged.add(tid)
                engagements.append(self._area_engagement(defender, aim, covered, now))
                shots -= 1
        return engagements

    def _cost_per_kill(self, defender: Defender) -> float:
        """Expected dollars per kill for ranking which area effector fires first."""
        expected = max(1, defender.max_simultaneous) * max(0.05, defender.kill_prob)
        return defender.unit_cost / expected

    def _best_aim(
        self,
        defender: Defender,
        threats: List[Threat],
        positions: Dict[str, Optional[Vec3]],
        engaged: set,
    ) -> tuple:
        """Pick the aim point covering the most unengaged in-reach threats.

        Candidate aim points are the unengaged threat positions themselves. A
        spatial grid counts how many unengaged threats fall within the effect
        radius of each candidate, capped at the effector simultaneous limit.
        """
        kind = defender.kind.value
        live = [
            t for t in threats
            if t.id not in engaged
            and kind not in t.ineffective_kinds
            and self._in_range(defender, positions[t.id])
        ]
        if not live:
            return None, []
        grid = _Grid(defender.effect_radius_m)
        pts = [positions[t.id] for t in live]
        for i, pos in enumerate(pts):
            grid.insert(i, pos)
        radius_sq = defender.effect_radius_m**2
        best_aim: Optional[Vec3] = None
        best_cov: List[str] = []
        for i, center in enumerate(pts):
            covered = [
                live[j].id for j in grid.neighbors(center)
                if _dist_sq(pts[j], center) <= radius_sq
            ]
            if len(covered) > len(best_cov):
                best_cov = covered[: defender.max_simultaneous]
                best_aim = center
        return best_aim, best_cov

    def _point_phase(
        self,
        defenders: List[Defender],
        threats: List[Threat],
        positions: Dict[str, Optional[Vec3]],
        engaged: set,
        now: float,
    ) -> List[Engagement]:
        """Assign point effectors to surviving threats by auction."""
        slots = self._expand_slots(defenders)
        live = [t for t in threats if t.id not in engaged]
        if not slots or not live:
            return []
        cand = live[: max(1, 3 * len(slots))]
        benefit = self._benefit_matrix(slots, cand, positions)
        assignment = _auction(benefit, len(slots), len(cand), self._auction_eps)
        engagements: List[Engagement] = []
        for si, ci in enumerate(assignment):
            if ci < 0:
                continue
            threat = cand[ci]
            engaged.add(threat.id)
            engagements.append(self._point_engagement(slots[si], threat, now))
        return engagements

    def _expand_slots(self, defenders: List[Defender]) -> List[Defender]:
        """One slot per available shot so a defender can take several targets."""
        slots: List[Defender] = []
        for defender in defenders:
            slots.extend([defender] * max(0, defender.capacity))
        return slots

    def _benefit_matrix(
        self,
        slots: List[Defender],
        threats: List[Threat],
        positions: Dict[str, Optional[Vec3]],
    ) -> List[List[float]]:
        """Benefit of each slot-threat pair, with -inf for out-of-range pairs."""
        matrix: List[List[float]] = []
        for defender in slots:
            row: List[float] = []
            for threat in threats:
                if not self._in_range(defender, positions[threat.id]):
                    row.append(-math.inf)
                    continue
                if not self._worth_spending(defender, threat):
                    row.append(-math.inf)
                    continue
                reward = threat.score * defender.kill_prob
                row.append(reward - self._cost_weight * defender.unit_cost)
            matrix.append(row)
        return matrix

    def _worth_spending(self, defender: Defender, threat: Threat) -> bool:
        """True when committing this point effector to this threat wins on cost.

        The cost war is defender dollars per kill against attacker dollars per
        airframe. An effector whose cost per expected kill beats the attacker
        airframe cost is always worth firing. An expensive one, like a kinetic
        interceptor, fires only as a last resort against an imminent leaker, and
        never against a known decoy or a low-confidence track, so it does not blow
        the cost-exchange ratio on a cheap, fake, or uncertain target.
        """
        if defender.kind.value in threat.ineffective_kinds:
            return False
        cost_per_kill = defender.unit_cost / max(0.05, defender.kill_prob)
        if cost_per_kill <= self._attacker_cost_ref:
            return True
        if threat.intent is SwarmIntent.DECOY:
            return False
        if threat.confidence < self._min_kinetic_confidence:
            return False
        tti = threat.time_to_impact_s
        return tti is not None and tti <= self._imminent_s

    def _in_range(self, defender: Defender, position: Optional[Vec3]) -> bool:
        """True when the threat is within the defender reach, or position unknown."""
        if position is None:
            return True
        return _distance(defender.position, position) <= defender.range_m

    def _area_engagement(
        self, defender: Defender, aim: Vec3, covered: List[str], now: float,
    ) -> Engagement:
        """Build one PENDING area engagement over a covered threat set."""
        return Engagement(
            id=f"eng-{uuid.uuid4().hex[:12]}",
            defender_id=defender.id,
            target_threat_id=covered[0],
            start_time=now,
            status=EngagementStatus.PENDING,
            cost=defender.unit_cost,
            aim_point=aim,
            neutralized_threat_ids=list(covered),
        )

    def _point_engagement(
        self, defender: Defender, threat: Threat, now: float,
    ) -> Engagement:
        """Build one PENDING single-target engagement."""
        return Engagement(
            id=f"eng-{uuid.uuid4().hex[:12]}",
            defender_id=defender.id,
            target_threat_id=threat.id,
            start_time=now,
            status=EngagementStatus.PENDING,
            cost=defender.unit_cost,
            neutralized_threat_ids=[threat.id],
        )


def _dist_sq(a: Vec3, b: Vec3) -> float:
    """Squared Euclidean distance between two ENU points."""
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def _auction(
    benefit: List[List[float]], n_slots: int, n_items: int, eps: float,
) -> List[int]:
    """Bertsekas auction for max-benefit assignment of slots to items.

    Returns the item index assigned to each slot, or -1 when a slot wins nothing.
    Items are exclusive, so two slots never share a threat. Ineligible pairs carry
    a benefit of negative infinity and are never bid on. This solves the point
    assignment near optimally in time bounded by the slot and item counts.
    """
    prices = [0.0] * n_items
    owner = [-1] * n_items
    slot_item = [-1] * n_slots
    queue = list(range(n_slots))
    guard = 0
    limit = 50 * max(1, n_slots) * max(1, n_items)
    while queue and guard < limit:
        guard += 1
        s = queue.pop()
        best_j, best_v, second_v = _best_bid(benefit[s], prices)
        if best_j < 0:
            continue
        prices[best_j] += (best_v - second_v) + eps
        prev = owner[best_j]
        if prev >= 0:
            slot_item[prev] = -1
            queue.append(prev)
        owner[best_j] = s
        slot_item[s] = best_j
    return slot_item


def _best_bid(row: List[float], prices: List[float]) -> tuple:
    """Find the best and second-best net value item for one slot."""
    best_j, best_v, second_v = -1, -math.inf, -math.inf
    for j, b in enumerate(row):
        if b == -math.inf:
            continue
        v = b - prices[j]
        if v > best_v:
            second_v = best_v
            best_v, best_j = v, j
        elif v > second_v:
            second_v = v
    if second_v == -math.inf:
        second_v = best_v
    return best_j, best_v, second_v


# Engagement outcomes are resolved by the WargameRunner, which owns the physical
# truth needed to decide kills: the effector lethal radius and each drone's
# resistance to that effector kind. See wargame.runner._resolve_engagements. The
# CostLedger above is the shared accounting the runner updates, kept here next to
# the allocator that produces the engagements it scores.
