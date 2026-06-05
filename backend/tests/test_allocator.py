"""Tests for the BULWARK defender allocation engine.

Covers range gating, capacity limits, greedy allocation quality on problems with
known good solutions, engagement resolution outcomes, the cost-exchange ledger,
and runtime sanity at swarm scale.
"""
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csontology import (
    Defender,
    DefenderKind,
    DefenderStatus,
    EngagementStatus,
    Threat,
    Vec3,
)
from defense import (
    CostLedger,
    DEFAULT_THREAT_VALUE,
    GreedyAllocator,
    resolve,
)


def _threat(tid: str, score: float, rank: int, value: float = 1000.0) -> Threat:
    return Threat(
        id=tid,
        score=score,
        time_to_impact_s=10.0,
        value_at_risk=value,
        priority_rank=rank,
        track_id=f"trk-{tid}",
    )


def _defender(
    did: str,
    position: Vec3,
    capacity: int = 1,
    range_m: float = 1000.0,
    kill_prob: float = 0.9,
    unit_cost: float = 50.0,
    kind: DefenderKind = DefenderKind.INTERCEPTOR,
    status: DefenderStatus = DefenderStatus.READY,
) -> Defender:
    return Defender(
        id=did,
        position=position,
        kind=kind,
        capacity=capacity,
        range_m=range_m,
        reload_s=5.0,
        kill_prob=kill_prob,
        unit_cost=unit_cost,
        status=status,
    )


# ---- Range gating ----

def test_out_of_range_threat_is_not_engaged():
    positions = {"t1": (5000.0, 0.0, 100.0)}
    allocator = GreedyAllocator(resolve_position=lambda t: positions[t.id])
    defender = _defender("d1", (0.0, 0.0, 0.0), range_m=1000.0)
    out = allocator.allocate([_threat("t1", 0.9, 1)], [defender], now=0.0)
    assert out == []


def test_in_range_threat_is_engaged():
    positions = {"t1": (500.0, 0.0, 0.0)}
    allocator = GreedyAllocator(resolve_position=lambda t: positions[t.id])
    defender = _defender("d1", (0.0, 0.0, 0.0), range_m=1000.0)
    out = allocator.allocate([_threat("t1", 0.9, 1)], [defender], now=0.0)
    assert len(out) == 1
    assert out[0].defender_id == "d1"
    assert out[0].target_threat_id == "t1"
    assert out[0].status is EngagementStatus.PENDING


def test_unknown_position_falls_back_to_in_range():
    allocator = GreedyAllocator()
    defender = _defender("d1", (0.0, 0.0, 0.0), range_m=1.0)
    out = allocator.allocate([_threat("t1", 0.9, 1)], [defender], now=0.0)
    assert len(out) == 1


# ---- Capacity limits ----

def test_capacity_caps_engagements_per_defender():
    allocator = GreedyAllocator()
    defender = _defender("d1", (0.0, 0.0, 0.0), capacity=2)
    threats = [_threat(f"t{i}", 0.9, i) for i in range(5)]
    out = allocator.allocate(threats, [defender], now=0.0)
    assert len(out) == 2
    assert all(e.defender_id == "d1" for e in out)


def test_depleted_and_offline_defenders_are_skipped():
    allocator = GreedyAllocator()
    depleted = _defender("d1", (0.0, 0.0, 0.0), status=DefenderStatus.DEPLETED)
    offline = _defender("d2", (0.0, 0.0, 0.0), status=DefenderStatus.OFFLINE)
    zero_cap = _defender("d3", (0.0, 0.0, 0.0), capacity=0)
    out = allocator.allocate([_threat("t1", 0.9, 1)], [depleted, offline, zero_cap], now=0.0)
    assert out == []


# ---- Allocation quality on a known good problem ----

def test_greedy_matches_known_optimum_one_to_one():
    # Three threats, three defenders, each defender only reaches its own threat.
    # The only feasible full solution pairs each defender to its threat.
    positions = {
        "t1": (0.0, 0.0, 0.0),
        "t2": (2000.0, 0.0, 0.0),
        "t3": (4000.0, 0.0, 0.0),
    }
    allocator = GreedyAllocator(resolve_position=lambda t: positions[t.id])
    defenders = [
        _defender("d1", (0.0, 0.0, 0.0), range_m=500.0),
        _defender("d2", (2000.0, 0.0, 0.0), range_m=500.0),
        _defender("d3", (4000.0, 0.0, 0.0), range_m=500.0),
    ]
    threats = [_threat("t1", 0.9, 1), _threat("t2", 0.8, 2), _threat("t3", 0.7, 3)]
    out = allocator.allocate(threats, defenders, now=0.0)
    assert len(out) == 3
    pairs = {(e.defender_id, e.target_threat_id) for e in out}
    assert pairs == {("d1", "t1"), ("d2", "t2"), ("d3", "t3")}


def test_greedy_prefers_cheaper_effector_on_tie():
    # Two defenders both reach the threat with equal kill_prob. The cheaper one
    # should win because of the cost penalty in the marginal score.
    allocator = GreedyAllocator()
    cheap = _defender("jammer", (0.0, 0.0, 0.0), unit_cost=5.0)
    pricey = _defender("missile", (0.0, 0.0, 0.0), unit_cost=5000.0)
    out = allocator.allocate([_threat("t1", 0.9, 1)], [pricey, cheap], now=0.0)
    assert len(out) == 1
    assert out[0].defender_id == "jammer"


def test_highest_priority_threat_served_first_under_scarcity():
    # One defender, two threats. The higher-score threat appears first in the
    # priority-ordered list and must get the single available engagement.
    allocator = GreedyAllocator()
    defender = _defender("d1", (0.0, 0.0, 0.0), capacity=1)
    threats = [_threat("t-high", 0.95, 1), _threat("t-low", 0.40, 2)]
    out = allocator.allocate(threats, [defender], now=0.0)
    assert len(out) == 1
    assert out[0].target_threat_id == "t-high"


# ---- Resolution and cost ledger ----

def test_resolve_hit_credits_attacker_value():
    allocator = GreedyAllocator()
    defender = _defender("d1", (0.0, 0.0, 0.0), kill_prob=1.0, unit_cost=50.0)
    threat = _threat("t1", 0.9, 1, value=1000.0)
    engagements = allocator.allocate([threat], [defender], now=0.0)
    ledger = resolve(engagements, [defender], [threat], now=1.0, rng=random.Random(1))
    assert engagements[0].status is EngagementStatus.HIT
    assert ledger.defender_spent == 50.0
    assert ledger.attacker_destroyed == 1000.0
    assert ledger.cost_exchange_ratio == 50.0 / 1000.0


def test_resolve_miss_spends_but_destroys_nothing():
    allocator = GreedyAllocator()
    defender = _defender("d1", (0.0, 0.0, 0.0), kill_prob=0.0, unit_cost=50.0)
    threat = _threat("t1", 0.9, 1, value=1000.0)
    engagements = allocator.allocate([threat], [defender], now=0.0)
    ledger = resolve(engagements, [defender], [threat], now=1.0, rng=random.Random(1))
    assert engagements[0].status is EngagementStatus.MISS
    assert ledger.defender_spent == 50.0
    assert ledger.attacker_destroyed == 0.0
    assert ledger.cost_exchange_ratio is None


def test_resolve_marks_leak_when_defender_missing():
    threat = _threat("t1", 0.9, 1)
    from csontology import Engagement
    orphan = Engagement(
        id="e1", defender_id="ghost", target_threat_id="t1", start_time=0.0,
    )
    ledger = resolve([orphan], [], [threat], now=1.0, rng=random.Random(1))
    assert orphan.status is EngagementStatus.LEAK
    assert ledger.leaks == 1


def test_resolve_skips_already_resolved_engagements():
    threat = _threat("t1", 0.9, 1)
    defender = _defender("d1", (0.0, 0.0, 0.0), kill_prob=1.0)
    from csontology import Engagement
    done = Engagement(
        id="e1", defender_id="d1", target_threat_id="t1", start_time=0.0,
        status=EngagementStatus.HIT, cost=50.0,
    )
    ledger = resolve([done], [defender], [threat], now=1.0)
    assert ledger.defender_spent == 0.0
    assert ledger.hits == 0


def test_threat_without_value_uses_default():
    allocator = GreedyAllocator()
    defender = _defender("d1", (0.0, 0.0, 0.0), kill_prob=1.0, unit_cost=10.0)
    threat = _threat("t1", 0.9, 1, value=0.0)
    engagements = allocator.allocate([threat], [defender], now=0.0)
    ledger = resolve(engagements, [defender], [threat], now=1.0, rng=random.Random(1))
    assert ledger.attacker_destroyed == DEFAULT_THREAT_VALUE


def test_cost_exchange_ratio_below_one_when_defense_wins():
    # Cheap jammer kills an expensive attacker. Ratio should beat 1.0.
    ledger = CostLedger()
    ledger.record_spend(5.0)
    ledger.record_outcome(EngagementStatus.HIT, 5000.0)
    assert ledger.cost_exchange_ratio is not None
    assert ledger.cost_exchange_ratio < 1.0


# ---- Runtime sanity at scale ----

def test_scale_thousand_threats_runs_fast():
    rng = random.Random(7)
    positions = {
        f"t{i}": (rng.uniform(-3000, 3000), rng.uniform(-3000, 3000), rng.uniform(0, 300))
        for i in range(1000)
    }
    allocator = GreedyAllocator(resolve_position=lambda t: positions[t.id])
    threats = [_threat(f"t{i}", rng.random(), i) for i in range(1000)]
    defenders = [
        _defender(f"d{j}", (rng.uniform(-2000, 2000), rng.uniform(-2000, 2000), 0.0),
                  capacity=8, range_m=2500.0)
        for j in range(20)
    ]
    start = time.perf_counter()
    out = allocator.allocate(threats, defenders, now=0.0)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0
    # Total capacity is 20 * 8 = 160. Allocation must not exceed it.
    assert len(out) <= 160
    # Per-defender capacity must be respected.
    counts: dict[str, int] = {}
    for e in out:
        counts[e.defender_id] = counts.get(e.defender_id, 0) + 1
    assert all(c <= 8 for c in counts.values())


def test_saturation_leaves_predicted_leakers():
    # Far more threats than capacity. Excess threats stay unengaged as leakers.
    allocator = GreedyAllocator()
    defenders = [_defender("d1", (0.0, 0.0, 0.0), capacity=3)]
    threats = [_threat(f"t{i}", 0.9, i) for i in range(10)]
    out = allocator.allocate(threats, defenders, now=0.0)
    engaged_ids = {e.target_threat_id for e in out}
    leakers = [t for t in threats if t.id not in engaged_ids]
    assert len(out) == 3
    assert len(leakers) == 7
