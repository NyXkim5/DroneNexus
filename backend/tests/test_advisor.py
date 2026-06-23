"""Tests for the TacticalAdvisor decision support module."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csontology import (
    Defender,
    DefenderKind,
    DefenderStatus,
    SwarmIntent,
    Threat,
    Vec3,
)
from decision.advisor import (
    RecommendationType,
    TacticalAdvisor,
    TacticalRecommendation,
)
from defense.allocator import CostLedger
from wargame.frame import Frame, Metrics


def _metrics(
    tick: int = 1,
    active_hostiles: int = 0,
    leakers: int = 0,
    engagements_made: int = 0,
    intercepts: int = 0,
) -> Metrics:
    return Metrics(
        tick=tick,
        sim_time_s=float(tick),
        active_hostiles=active_hostiles,
        tracks_held=active_hostiles,
        leakers=leakers,
        engagements_made=engagements_made,
        intercepts=intercepts,
        intercept_rate=intercepts / max(1, engagements_made),
        defender_spent=0.0,
        attacker_destroyed=0.0,
        cost_exchange_ratio=None,
    )


def _defender(
    did: str,
    position: Vec3 = (0.0, 0.0, 0.0),
    status: DefenderStatus = DefenderStatus.READY,
    capacity: int = 5,
) -> Defender:
    return Defender(
        id=did,
        position=position,
        kind=DefenderKind.INTERCEPTOR,
        capacity=capacity,
        range_m=1000.0,
        reload_s=2.0,
        kill_prob=0.9,
        unit_cost=50.0,
        status=status,
    )


def _threat(
    tid: str,
    score: float = 0.8,
    track_id: str = "",
    intent: SwarmIntent = SwarmIntent.UNKNOWN,
) -> Threat:
    return Threat(
        id=tid,
        score=score,
        time_to_impact_s=10.0,
        value_at_risk=1000.0,
        priority_rank=1,
        track_id=track_id or f"trk-{tid}",
        intent=intent,
    )


def _frame(
    defenders: list[Defender] | None = None,
    threats: list[Threat] | None = None,
    metrics: Metrics | None = None,
) -> Frame:
    return Frame(
        metrics=metrics or _metrics(),
        tracks=[],
        defenders=defenders or [],
        threats=threats or [],
    )


# -- leak rate triggers CALL_REINFORCEMENT --

def test_no_recommendation_when_situation_normal():
    frame = _frame(
        defenders=[_defender("d1")],
        metrics=_metrics(engagements_made=10, intercepts=9, leakers=1),
    )
    advisor = TacticalAdvisor()
    recs = advisor.analyze(frame)
    types = [r.type for r in recs]
    assert RecommendationType.CALL_REINFORCEMENT not in types


def test_call_reinforcement_on_high_leak_rate():
    frame = _frame(
        defenders=[_defender("d1")],
        metrics=_metrics(engagements_made=10, intercepts=5, leakers=5),
    )
    advisor = TacticalAdvisor()
    recs = advisor.analyze(frame)
    types = [r.type for r in recs]
    assert RecommendationType.CALL_REINFORCEMENT in types
    rec = next(r for r in recs if r.type is RecommendationType.CALL_REINFORCEMENT)
    assert rec.priority == 1


# -- cost ratio triggers CONSERVE_AMMO --

def test_conserve_ammo_on_high_cost_ratio():
    ledger = CostLedger(defender_spent=5000.0, attacker_destroyed=1000.0)
    frame = _frame(defenders=[_defender("d1")])
    advisor = TacticalAdvisor()
    recs = advisor.analyze(frame, ledger=ledger)
    types = [r.type for r in recs]
    assert RecommendationType.CONSERVE_AMMO in types


def test_no_conserve_ammo_when_ratio_acceptable():
    ledger = CostLedger(defender_spent=500.0, attacker_destroyed=1000.0)
    frame = _frame(defenders=[_defender("d1")])
    advisor = TacticalAdvisor()
    recs = advisor.analyze(frame, ledger=ledger)
    types = [r.type for r in recs]
    assert RecommendationType.CONSERVE_AMMO not in types


# -- depleted defenders trigger ADD_EFFECTOR --

def test_add_effector_when_defender_depleted():
    frame = _frame(
        defenders=[
            _defender("d1", status=DefenderStatus.DEPLETED),
            _defender("d2"),
        ],
    )
    advisor = TacticalAdvisor()
    recs = advisor.analyze(frame)
    types = [r.type for r in recs]
    assert RecommendationType.ADD_EFFECTOR in types
    rec = next(r for r in recs if r.type is RecommendationType.ADD_EFFECTOR)
    assert "d1" in rec.description


# -- uncovered sector triggers REPOSITION_DEFENDER --

def test_reposition_defender_for_uncovered_sector():
    site: Vec3 = (0.0, 0.0, 0.0)
    defenders = [_defender("d1", position=(100.0, 0.0, 0.0))]  # east
    threats = [_threat("t1", track_id="trk-t1")]
    threat_positions = {"trk-t1": (0.0, -200.0, 50.0)}  # south
    frame = _frame(
        defenders=defenders,
        threats=threats,
        metrics=_metrics(active_hostiles=1),
    )
    advisor = TacticalAdvisor(site=site)
    recs = advisor.analyze(frame, threat_positions=threat_positions)
    types = [r.type for r in recs]
    assert RecommendationType.REPOSITION_DEFENDER in types


def test_no_reposition_when_sector_covered():
    site: Vec3 = (0.0, 0.0, 0.0)
    defenders = [_defender("d1", position=(0.0, -100.0, 0.0))]  # south
    threats = [_threat("t1", track_id="trk-t1")]
    threat_positions = {"trk-t1": (0.0, -200.0, 50.0)}  # also south
    frame = _frame(
        defenders=defenders,
        threats=threats,
        metrics=_metrics(active_hostiles=1),
    )
    advisor = TacticalAdvisor(site=site)
    recs = advisor.analyze(frame, threat_positions=threat_positions)
    types = [r.type for r in recs]
    assert RecommendationType.REPOSITION_DEFENDER not in types


# -- all engaging + new threats triggers INCREASE_COVERAGE --

def test_increase_coverage_when_all_engaged_and_new_threats():
    frame = _frame(
        defenders=[
            _defender("d1", status=DefenderStatus.ENGAGING),
            _defender("d2", status=DefenderStatus.ENGAGING),
        ],
        metrics=_metrics(active_hostiles=10, engagements_made=5),
    )
    advisor = TacticalAdvisor()
    recs = advisor.analyze(frame)
    types = [r.type for r in recs]
    assert RecommendationType.INCREASE_COVERAGE in types


# -- majority depleted + hostiles active triggers EVACUATE_SITE --

def test_evacuate_when_majority_depleted_and_hostiles_active():
    frame = _frame(
        defenders=[
            _defender("d1", status=DefenderStatus.DEPLETED),
            _defender("d2", status=DefenderStatus.DEPLETED),
            _defender("d3"),
        ],
        metrics=_metrics(active_hostiles=5),
    )
    advisor = TacticalAdvisor()
    recs = advisor.analyze(frame)
    types = [r.type for r in recs]
    assert RecommendationType.EVACUATE_SITE in types
    rec = next(r for r in recs if r.type is RecommendationType.EVACUATE_SITE)
    assert rec.priority == 1


def test_no_evacuate_when_hostiles_cleared():
    frame = _frame(
        defenders=[
            _defender("d1", status=DefenderStatus.DEPLETED),
            _defender("d2", status=DefenderStatus.DEPLETED),
            _defender("d3"),
        ],
        metrics=_metrics(active_hostiles=0),
    )
    advisor = TacticalAdvisor()
    recs = advisor.analyze(frame)
    types = [r.type for r in recs]
    assert RecommendationType.EVACUATE_SITE not in types


# -- priority ordering --

def test_recommendations_sorted_by_priority():
    ledger = CostLedger(defender_spent=5000.0, attacker_destroyed=1000.0)
    frame = _frame(
        defenders=[
            _defender("d1", status=DefenderStatus.DEPLETED),
            _defender("d2", status=DefenderStatus.DEPLETED),
            _defender("d3", status=DefenderStatus.ENGAGING),
        ],
        metrics=_metrics(
            active_hostiles=10,
            engagements_made=10,
            intercepts=3,
            leakers=5,
        ),
    )
    advisor = TacticalAdvisor()
    recs = advisor.analyze(frame, ledger=ledger)
    assert len(recs) >= 3
    priorities = [r.priority for r in recs]
    assert priorities == sorted(priorities)


# -- empty state produces no recommendations --

def test_empty_state_no_recommendations():
    frame = _frame()
    advisor = TacticalAdvisor()
    recs = advisor.analyze(frame)
    assert recs == []


# -- confidence bounds --

def test_confidence_within_bounds():
    ledger = CostLedger(defender_spent=50000.0, attacker_destroyed=1000.0)
    frame = _frame(
        defenders=[
            _defender("d1", status=DefenderStatus.DEPLETED),
            _defender("d2", status=DefenderStatus.DEPLETED),
            _defender("d3", status=DefenderStatus.DEPLETED),
        ],
        metrics=_metrics(
            active_hostiles=20,
            engagements_made=20,
            leakers=15,
        ),
    )
    advisor = TacticalAdvisor()
    recs = advisor.analyze(frame, ledger=ledger)
    for rec in recs:
        assert 0.0 <= rec.confidence <= 1.0


# -- multiple triggers combine correctly --

def test_multiple_triggers_combine():
    ledger = CostLedger(defender_spent=10000.0, attacker_destroyed=1000.0)
    frame = _frame(
        defenders=[
            _defender("d1", status=DefenderStatus.DEPLETED),
        ],
        metrics=_metrics(
            active_hostiles=10,
            engagements_made=10,
            leakers=5,
        ),
    )
    advisor = TacticalAdvisor()
    recs = advisor.analyze(frame, ledger=ledger)
    types = {r.type for r in recs}
    assert RecommendationType.CALL_REINFORCEMENT in types
    assert RecommendationType.CONSERVE_AMMO in types
    assert RecommendationType.ADD_EFFECTOR in types
    assert RecommendationType.EVACUATE_SITE in types
