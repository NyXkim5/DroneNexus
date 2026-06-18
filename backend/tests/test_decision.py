import pytest
from csontology import Threat, SwarmIntent
from vision.cascade import CascadeResult
from decision.models import EngagementMode, EngagementPriority, EngagementOrder
from decision.engine import DecisionEngine


def _make_threat(id: str, score: float, tti: float = 30.0) -> Threat:
    return Threat(
        id=id,
        score=score,
        time_to_impact_s=tti,
        value_at_risk=1_000_000.0,
        priority_rank=1,
        track_id=f"track-{id}",
        intent=SwarmIntent.SATURATION,
    )


def _make_cascade(target_id: str, expected_value: float, personnel: int = 0) -> CascadeResult:
    return CascadeResult(
        target_id=target_id,
        direct_value=expected_value * 0.5,
        cascade_value=expected_value,
        cascade_chain=[target_id],
        cascade_probability=1.0,
        expected_value=expected_value,
        personnel_at_risk=personnel,
    )


class TestDecisionEngine:
    def test_defensive_threats_only(self):
        engine = DecisionEngine(mode=EngagementMode.ADVISORY)
        threats = [_make_threat("d1", 0.9, 10.0), _make_threat("d2", 0.5, 50.0)]
        order = engine.merge(threats=threats, cascade_results=[], now=0.0)
        assert len(order.priorities) == 2
        assert order.priorities[0].target_id == "d1"
        assert order.priorities[0].source == "bulwark"
        assert order.mode == EngagementMode.ADVISORY

    def test_offensive_targets_only(self):
        engine = DecisionEngine(mode=EngagementMode.AUTO)
        cascades = [
            _make_cascade("t1", 3_000_000),
            _make_cascade("t2", 30_000),
        ]
        order = engine.merge(threats=[], cascade_results=cascades, now=0.0)
        assert len(order.priorities) == 2
        assert order.priorities[0].target_id == "t1"
        assert order.priorities[0].source == "vision"
        assert order.mode == EngagementMode.AUTO

    def test_merged_order_defensive_urgency_wins(self):
        engine = DecisionEngine(mode=EngagementMode.ADVISORY)
        threats = [_make_threat("d1", 0.95, 5.0)]
        cascades = [_make_cascade("t1", 10_000_000)]
        order = engine.merge(threats=threats, cascade_results=cascades, now=0.0)
        assert order.priorities[0].target_id == "d1"

    def test_merged_order_both_sources_present(self):
        engine = DecisionEngine(mode=EngagementMode.AUTO)
        threats = [_make_threat("d1", 0.5, 60.0)]
        cascades = [_make_cascade("t1", 500_000)]
        order = engine.merge(threats=threats, cascade_results=cascades, now=0.0)
        sources = {p.source for p in order.priorities}
        assert sources == {"bulwark", "vision"}

    def test_time_sensitivity_breaks_ties(self):
        engine = DecisionEngine(mode=EngagementMode.ADVISORY)
        threats = [
            _make_threat("d1", 0.7, 60.0),
            _make_threat("d2", 0.7, 10.0),
        ]
        order = engine.merge(threats=threats, cascade_results=[], now=0.0)
        assert order.priorities[0].target_id == "d2"

    def test_engagement_order_to_dict(self):
        engine = DecisionEngine(mode=EngagementMode.AUTO)
        threats = [_make_threat("d1", 0.8)]
        order = engine.merge(threats=threats, cascade_results=[], now=0.0)
        d = order.to_dict()
        assert d["mode"] == "auto"
        assert len(d["priorities"]) == 1
        assert "rationale" in d

    def test_personnel_impact_in_priority(self):
        engine = DecisionEngine(mode=EngagementMode.ADVISORY)
        cascades = [_make_cascade("t1", 500_000, personnel=20)]
        order = engine.merge(threats=[], cascade_results=cascades, now=0.0)
        assert order.priorities[0].personnel_impact == 20

    def test_empty_inputs(self):
        engine = DecisionEngine(mode=EngagementMode.AUTO)
        order = engine.merge(threats=[], cascade_results=[], now=0.0)
        assert len(order.priorities) == 0
