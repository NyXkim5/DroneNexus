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
    SwarmIntent,
    Threat,
    Vec3,
)
from defense import (
    CostLedger,
    GreedyAllocator,
    LayeredAllocator,
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


# ---- Cost ledger (the shipped accounting the runner updates) ----
# Engagement outcomes are resolved by WargameRunner._resolve_engagements, which is
# covered end to end in test_honest_fight. These tests pin the CostLedger that the
# runner feeds, so the shipped accounting is verified directly.

def test_ledger_hit_credits_spend_and_value():
    ledger = CostLedger()
    ledger.record_spend(50.0)
    ledger.record_outcome(EngagementStatus.HIT, 1000.0)
    assert ledger.defender_spent == 50.0
    assert ledger.attacker_destroyed == 1000.0
    assert ledger.hits == 1
    assert ledger.cost_exchange_ratio == 50.0 / 1000.0


def test_ledger_miss_spends_but_destroys_nothing():
    ledger = CostLedger()
    ledger.record_spend(50.0)
    ledger.record_outcome(EngagementStatus.MISS, 0.0)
    assert ledger.defender_spent == 50.0
    assert ledger.attacker_destroyed == 0.0
    assert ledger.misses == 1
    assert ledger.cost_exchange_ratio is None


def test_ledger_counts_leaks():
    ledger = CostLedger()
    ledger.record_outcome(EngagementStatus.LEAK, 0.0)
    assert ledger.leaks == 1


def test_ledger_accumulates_multiple_hits():
    ledger = CostLedger()
    for _ in range(3):
        ledger.record_spend(8.0)
        ledger.record_outcome(EngagementStatus.HIT, 500.0)
    assert ledger.hits == 3
    assert ledger.defender_spent == 24.0
    assert ledger.attacker_destroyed == 1500.0
    assert ledger.cost_exchange_ratio == 24.0 / 1500.0


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


# ---- Layered allocator: area effects and cost discipline ----

def _area_threat(tid: str, pos: Vec3, rank: int) -> Threat:
    """A closing, imminent threat at a position, used for layered tests."""
    return Threat(
        id=tid, score=0.9, time_to_impact_s=8.0, value_at_risk=50_000.0,
        priority_rank=rank, track_id=f"trk-{tid}",
    )


def test_area_effector_covers_cluster_in_one_shot():
    positions = {f"t{i}": (float(i * 30), 0.0, 80.0) for i in range(8)}
    threats = [_area_threat(f"t{i}", positions[f"t{i}"], i) for i in range(8)]
    hpm = _defender(
        "hpm1", (0.0, 0.0, 0.0), capacity=1, range_m=3000.0,
        unit_cost=8.0, kind=DefenderKind.HPM,
    )
    hpm.effect_radius_m = 400.0
    hpm.max_simultaneous = 25
    allocator = LayeredAllocator(resolve_position=lambda t: positions[t.id])
    out = allocator.allocate(threats, [hpm], now=0.0)
    assert len(out) == 1
    assert len(out[0].neutralized_threat_ids) == 8
    assert out[0].aim_point is not None


def test_area_shot_credits_every_kill_in_ledger():
    # An area shot charges its cost once but credits a kill per drone it removes,
    # the accounting the runner performs for an HPM cone. The ledger must show one
    # spend and many destroyed. The runner-side physics is covered in
    # test_honest_fight and test_area_effector_covers_cluster_in_one_shot.
    ledger = CostLedger()
    ledger.record_spend(8.0)
    for _ in range(5):
        ledger.record_outcome(EngagementStatus.HIT, 50_000.0)
    assert ledger.hits == 5
    assert ledger.attacker_destroyed == 5 * 50_000.0
    assert ledger.defender_spent == 8.0


def test_auction_assigns_distinct_threats_under_contention():
    # Three imminent, high-value threats, two interceptor slots. The auction must
    # assign each slot to a different threat, never double-booking one threat.
    positions = {f"t{i}": (100.0 * i, 0.0, 80.0) for i in range(3)}
    threats = [
        Threat(
            id=f"t{i}", score=0.9 - 0.01 * i, time_to_impact_s=5.0,
            value_at_risk=50_000.0, priority_rank=i + 1, track_id=f"trk{i}",
            intent=SwarmIntent.SATURATION,
        )
        for i in range(3)
    ]
    interceptor = _defender(
        "int1", (0.0, 0.0, 0.0), capacity=2, range_m=3000.0,
        unit_cost=8_000.0, kill_prob=0.85, kind=DefenderKind.INTERCEPTOR,
    )
    allocator = LayeredAllocator(
        resolve_position=lambda t: positions[t.id],
        attacker_cost_ref=2000.0,
    )
    out = allocator.allocate(threats, [interceptor], now=0.0)
    engaged = [e.target_threat_id for e in out]
    assert len(out) == 2
    assert len(set(engaged)) == 2


def test_cost_discipline_reserves_interceptor_for_imminent():
    positions = {"far": (1000.0, 0.0, 80.0), "near": (1000.0, 0.0, 80.0)}
    interceptor = _defender(
        "int1", (0.0, 0.0, 0.0), capacity=2, range_m=3000.0,
        unit_cost=8_000.0, kill_prob=0.85, kind=DefenderKind.INTERCEPTOR,
    )
    allocator = LayeredAllocator(
        resolve_position=lambda t: positions[t.id],
        attacker_cost_ref=2000.0,
    )
    distant = Threat(
        id="far", score=0.9, time_to_impact_s=120.0, value_at_risk=50_000.0,
        priority_rank=1, track_id="trk-far", intent=SwarmIntent.SATURATION,
    )
    out_distant = allocator.allocate([distant], [interceptor], now=0.0)
    assert out_distant == []  # too costly per kill, not imminent, so held back
    imminent = Threat(
        id="near", score=0.9, time_to_impact_s=5.0, value_at_risk=50_000.0,
        priority_rank=1, track_id="trk-near", intent=SwarmIntent.SATURATION,
    )
    out_imminent = allocator.allocate([imminent], [interceptor], now=0.0)
    assert len(out_imminent) == 1  # last-resort terminal defense fires


# ---- Intent-based threat value scaling ----

def _intent_threat(
    tid: str, score: float, rank: int, intent: SwarmIntent,
    tti: float = 5.0, confidence: float = 1.0,
) -> Threat:
    """Build a threat with an explicit intent for cost-discipline tests."""
    return Threat(
        id=tid, score=score, time_to_impact_s=tti,
        value_at_risk=1000.0, priority_rank=rank,
        track_id=f"trk-{tid}", intent=intent, confidence=confidence,
    )


def test_decoy_threat_gets_lower_benefit_than_saturation():
    """DECOY threats should produce a much lower benefit score in the auction."""
    positions = {"d1": (100.0, 0.0, 80.0), "s1": (100.0, 0.0, 80.0)}
    interceptor = _defender(
        "int1", (0.0, 0.0, 0.0), capacity=1, range_m=3000.0,
        unit_cost=100.0, kill_prob=0.9,
    )
    allocator = LayeredAllocator(
        resolve_position=lambda t: positions[t.id],
        attacker_cost_ref=500.0,
    )
    decoy = _intent_threat("d1", 0.9, 1, SwarmIntent.DECOY)
    satur = _intent_threat("s1", 0.9, 1, SwarmIntent.SATURATION)
    row = allocator._defender_row(interceptor, [decoy, satur], positions)
    # DECOY benefit should be roughly 0.1x of SATURATION benefit
    assert row[0] < row[1]
    assert row[0] < row[1] * 0.2


def test_expensive_area_effector_skips_decoys():
    """An expensive area effector should not waste shots on DECOY threats."""
    positions = {f"t{i}": (float(i * 30), 0.0, 80.0) for i in range(5)}
    threats = [
        _intent_threat(f"t{i}", 0.9, i, SwarmIntent.DECOY)
        for i in range(5)
    ]
    expensive_area = _defender(
        "exp1", (0.0, 0.0, 0.0), capacity=3, range_m=3000.0,
        unit_cost=5000.0, kill_prob=0.3, kind=DefenderKind.HPM,
    )
    expensive_area.effect_radius_m = 400.0
    expensive_area.max_simultaneous = 25
    allocator = LayeredAllocator(
        resolve_position=lambda t: positions[t.id],
        attacker_cost_ref=500.0,
    )
    out = allocator.allocate(threats, [expensive_area], now=0.0)
    assert out == []  # all decoys, expensive effector should skip them


def test_cheap_area_effector_engages_decoys():
    """A cheap area effector should still engage DECOY threats."""
    positions = {f"t{i}": (float(i * 30), 0.0, 80.0) for i in range(5)}
    threats = [
        _intent_threat(f"t{i}", 0.9, i, SwarmIntent.DECOY)
        for i in range(5)
    ]
    cheap_area = _defender(
        "hpm1", (0.0, 0.0, 0.0), capacity=3, range_m=3000.0,
        unit_cost=8.0, kill_prob=0.9, kind=DefenderKind.HPM,
    )
    cheap_area.effect_radius_m = 400.0
    cheap_area.max_simultaneous = 25
    allocator = LayeredAllocator(
        resolve_position=lambda t: positions[t.id],
        attacker_cost_ref=500.0,
    )
    out = allocator.allocate(threats, [cheap_area], now=0.0)
    assert len(out) >= 1  # cheap effector fires on decoys


def test_overspend_gate_4x_blocks_distant_probe():
    """A 5x overspend should be blocked for distant PROBE threats.

    At TTI=5.0 the urgency relaxation allows higher overspend ratios. A
    distant probe (TTI=18.0) should still be blocked because the urgency
    cap stays close to the base 4x.
    """
    positions = {"p1": (100.0, 0.0, 80.0)}
    # cost_per_kill = 10000 / 0.85 ~= 11765, attacker_ref = 2000, ratio ~5.9x
    interceptor = _defender(
        "int1", (0.0, 0.0, 0.0), capacity=1, range_m=3000.0,
        unit_cost=10_000.0, kill_prob=0.85, kind=DefenderKind.INTERCEPTOR,
    )
    allocator = LayeredAllocator(
        resolve_position=lambda t: positions[t.id],
        attacker_cost_ref=2000.0,
    )
    probe = _intent_threat("p1", 0.9, 1, SwarmIntent.PROBE, tti=18.0)
    out = allocator.allocate([probe], [interceptor], now=0.0)
    assert out == []  # 5.9x overspend > ~4.5x gate for distant non-SATURATION


def test_overspend_gate_8x_allows_saturation():
    """A 5x overspend should still be allowed for SATURATION threats."""
    positions = {"s1": (100.0, 0.0, 80.0)}
    interceptor = _defender(
        "int1", (0.0, 0.0, 0.0), capacity=1, range_m=3000.0,
        unit_cost=10_000.0, kill_prob=0.85, kind=DefenderKind.INTERCEPTOR,
    )
    allocator = LayeredAllocator(
        resolve_position=lambda t: positions[t.id],
        attacker_cost_ref=2000.0,
    )
    satur = _intent_threat("s1", 0.9, 1, SwarmIntent.SATURATION, tti=5.0)
    out = allocator.allocate([satur], [interceptor], now=0.0)
    assert len(out) == 1  # 5.9x overspend < 8x gate for SATURATION


def test_terminal_defense_fires_unconditionally_under_5s():
    """A threat inside 5s TTI fires regardless of overspend ratio."""
    positions = {"t1": (100.0, 0.0, 80.0)}
    # cost_per_kill = 25000 / 0.85 = 29412, attacker_ref = 500, ratio = 58.8x
    interceptor = _defender(
        "int1", (0.0, 0.0, 0.0), capacity=1, range_m=3000.0,
        unit_cost=25_000.0, kill_prob=0.85, kind=DefenderKind.INTERCEPTOR,
    )
    allocator = LayeredAllocator(
        resolve_position=lambda t: positions[t.id],
        attacker_cost_ref=500.0,
    )
    imminent = _intent_threat("t1", 0.9, 1, SwarmIntent.SATURATION, tti=3.0)
    out = allocator.allocate([imminent], [interceptor], now=0.0)
    assert len(out) == 1  # terminal defense fires regardless of cost


def test_tti_urgency_boosts_imminent_benefit():
    """Threats with low TTI should get higher benefit in the auction."""
    from defense.allocator import _tti_urgency
    assert _tti_urgency(3.0) == 3.0  # under 5s
    assert _tti_urgency(7.0) == 2.0  # under 10s
    assert _tti_urgency(15.0) == 1.0  # above 10s
    assert _tti_urgency(None) == 1.0  # unknown
