"""
Tests for the ROE engine (backend/decision/roe.py).

Run from backend/:
    python3 -m pytest tests/test_roe.py -v
"""
from __future__ import annotations

import time
from typing import Dict, List, Tuple

import pytest

from csontology import Track, TrackClass, Vec3
from decision.models import EngagementMode, EngagementOrder, EngagementPriority
from decision.roe import ROECondition, ROEEngine, ROEEvaluation, ROERule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_track(
    track_id: str,
    position: Vec3 = (0.0, 0.0, 50.0),
    confidence: float = 0.9,
) -> Track:
    return Track(
        id=track_id,
        position=position,
        velocity=(0.0, 0.0, 0.0),
        covariance=(1.0, 1.0, 1.0),
        last_update=time.time(),
        confidence=confidence,
        classification=TrackClass.HOSTILE,
    )


def _make_priority(
    target_id: str = "T1",
    normalized_score: float = 0.8,
    personnel_impact: int = 0,
    recommended_effector: str = "any",
) -> EngagementPriority:
    return EngagementPriority(
        target_id=target_id,
        source="test",
        normalized_score=normalized_score,
        time_sensitivity=30.0,
        personnel_impact=personnel_impact,
        cascade_depth=0,
        recommended_effector=recommended_effector,
    )


def _corridor_at_origin(radius_m: float = 500.0) -> List[Tuple[Vec3, float]]:
    return [((0.0, 0.0, 0.0), radius_m)]


def _make_order(
    target_ids: List[str],
    normalized_score: float = 0.8,
    personnel_impact: int = 0,
) -> EngagementOrder:
    priorities = [
        _make_priority(
            target_id=tid,
            normalized_score=normalized_score,
            personnel_impact=personnel_impact,
        )
        for tid in target_ids
    ]
    return EngagementOrder(
        priorities=priorities,
        mode=EngagementMode.AUTO,
        timestamp=time.time(),
        rationale={tid: f"Initial rationale for {tid}" for tid in target_ids},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestROEAuthorization:
    def test_authorized_engagement(self) -> None:
        """All conditions met returns authorized=True."""
        engine = ROEEngine()
        priority = _make_priority(normalized_score=0.9)
        result = engine.evaluate(
            target_id="T1",
            engagement_priority=priority,
            track_confidence=0.95,
            target_position=(0.0, 0.0, 50.0),
            personnel_at_risk=0,
            corridors=_corridor_at_origin(),
            timestamp=time.time(),
        )
        assert result.authorized is True
        assert result.target_id == "T1"

    def test_denied_low_confidence(self) -> None:
        """Track confidence below min_confidence denies engagement."""
        engine = ROEEngine()
        priority = _make_priority(normalized_score=0.9)
        result = engine.evaluate(
            target_id="T2",
            engagement_priority=priority,
            track_confidence=0.5,   # below default 0.8
            target_position=(0.0, 0.0, 50.0),
            personnel_at_risk=0,
            corridors=_corridor_at_origin(),
            timestamp=time.time(),
        )
        assert result.authorized is False
        assert "confidence" in result.reason.lower()

    def test_denied_outside_corridor(self) -> None:
        """Target outside all corridors is denied."""
        engine = ROEEngine()
        priority = _make_priority(normalized_score=0.9)
        # Target is 1000 m away; corridor radius is only 100 m.
        result = engine.evaluate(
            target_id="T3",
            engagement_priority=priority,
            track_confidence=0.95,
            target_position=(1000.0, 0.0, 50.0),
            personnel_at_risk=0,
            corridors=[((0.0, 0.0, 0.0), 100.0)],
            timestamp=time.time(),
        )
        assert result.authorized is False
        assert ROECondition.WITHIN_CORRIDOR in result.conditions_met
        assert result.conditions_met[ROECondition.WITHIN_CORRIDOR] is False

    def test_denied_personnel_limit(self) -> None:
        """Engagement denied when personnel_at_risk exceeds rule maximum."""
        rule = ROERule(
            name="STRICT_RULE",
            conditions=[
                ROECondition.POSITIVE_ID,
                ROECondition.THREAT_IMMINENT,
                ROECondition.WITHIN_CORRIDOR,
            ],
            authorized_effectors=["any"],
            max_cascade_personnel=2,
            min_confidence=0.8,
            authorization_level="CO",
        )
        engine = ROEEngine(rules=[rule])
        priority = _make_priority(normalized_score=0.9, personnel_impact=5)
        result = engine.evaluate(
            target_id="T4",
            engagement_priority=priority,
            track_confidence=0.95,
            target_position=(0.0, 0.0, 50.0),
            personnel_at_risk=5,   # exceeds max of 2
            corridors=_corridor_at_origin(),
            timestamp=time.time(),
        )
        assert result.authorized is False
        assert "personnel" in result.reason.lower()


class TestROEAuditLog:
    def test_audit_log_records_all(self) -> None:
        """Every evaluation call is appended to the audit log."""
        engine = ROEEngine()
        priority = _make_priority()
        ts = time.time()
        engine.evaluate(
            target_id="T1",
            engagement_priority=priority,
            track_confidence=0.95,
            target_position=(0.0, 0.0, 50.0),
            personnel_at_risk=0,
            corridors=_corridor_at_origin(),
            timestamp=ts,
        )
        engine.evaluate(
            target_id="T2",
            engagement_priority=priority,
            track_confidence=0.3,  # denied
            target_position=(0.0, 0.0, 50.0),
            personnel_at_risk=0,
            corridors=_corridor_at_origin(),
            timestamp=ts,
        )
        assert len(engine.audit_log) == 2

    def test_audit_log_is_copy(self) -> None:
        """audit_log property returns a copy, not the internal list."""
        engine = ROEEngine()
        log1 = engine.audit_log
        log2 = engine.audit_log
        assert log1 is not log2

    def test_export_audit_serializable(self) -> None:
        """export_audit produces valid dicts with expected keys."""
        engine = ROEEngine()
        priority = _make_priority()
        engine.evaluate(
            target_id="T1",
            engagement_priority=priority,
            track_confidence=0.95,
            target_position=(0.0, 0.0, 50.0),
            personnel_at_risk=0,
            corridors=_corridor_at_origin(),
            timestamp=time.time(),
        )
        exported = engine.export_audit()
        assert isinstance(exported, list)
        assert len(exported) == 1
        entry = exported[0]
        assert isinstance(entry, dict)
        for key in ("target_id", "rule_name", "conditions_met", "authorized", "reason",
                    "timestamp", "authorization_level"):
            assert key in entry
        # conditions_met values must be plain strings (serializable).
        for k, v in entry["conditions_met"].items():
            assert isinstance(k, str)
            assert isinstance(v, bool)


class TestROEGating:
    def test_gate_filters_order(self) -> None:
        """Unauthorized targets are removed from the engagement order."""
        engine = ROEEngine()
        tracks: Dict[str, Track] = {
            "T1": _make_track("T1", position=(0.0, 0.0, 50.0), confidence=0.9),
            "T2": _make_track("T2", position=(9999.0, 0.0, 50.0), confidence=0.9),
        }
        order = _make_order(["T1", "T2"])
        result = engine.gate_engagement_order(
            order=order,
            tracks=tracks,
            corridors=_corridor_at_origin(radius_m=500.0),
            timestamp=time.time(),
        )
        remaining_ids = [p.target_id for p in result.priorities]
        assert "T1" in remaining_ids
        assert "T2" not in remaining_ids

    def test_gate_preserves_authorized(self) -> None:
        """Authorized targets remain in the engagement order."""
        engine = ROEEngine()
        tracks: Dict[str, Track] = {
            "T1": _make_track("T1", position=(0.0, 0.0, 50.0), confidence=0.9),
            "T2": _make_track("T2", position=(10.0, 0.0, 50.0), confidence=0.95),
        }
        order = _make_order(["T1", "T2"])
        result = engine.gate_engagement_order(
            order=order,
            tracks=tracks,
            corridors=_corridor_at_origin(radius_m=500.0),
            timestamp=time.time(),
        )
        remaining_ids = [p.target_id for p in result.priorities]
        assert "T1" in remaining_ids
        assert "T2" in remaining_ids

    def test_gate_adds_roe_status_to_rationale(self) -> None:
        """gate_engagement_order appends ROE status to each target's rationale."""
        engine = ROEEngine()
        tracks: Dict[str, Track] = {
            "T1": _make_track("T1", position=(0.0, 0.0, 50.0), confidence=0.9),
        }
        order = _make_order(["T1"])
        engine.gate_engagement_order(
            order=order,
            tracks=tracks,
            corridors=_corridor_at_origin(),
            timestamp=time.time(),
        )
        assert "ROE=" in order.rationale.get("T1", "")

    def test_gate_missing_track_denies(self) -> None:
        """Target with no track entry is denied (confidence defaults to 0)."""
        engine = ROEEngine()
        order = _make_order(["UNKNOWN_TARGET"])
        result = engine.gate_engagement_order(
            order=order,
            tracks={},
            corridors=_corridor_at_origin(),
            timestamp=time.time(),
        )
        assert result.priorities == []


class TestROEDefaultRules:
    def test_default_rules_exist(self) -> None:
        """Default ROE has at least one rule."""
        engine = ROEEngine()
        assert len(engine._rules) >= 1

    def test_default_rule_requires_positive_id(self) -> None:
        """Default rule includes POSITIVE_ID condition."""
        engine = ROEEngine()
        default = engine._rules[0]
        assert ROECondition.POSITIVE_ID in default.conditions

    def test_default_rule_requires_within_corridor(self) -> None:
        """Default rule includes WITHIN_CORRIDOR condition."""
        engine = ROEEngine()
        default = engine._rules[0]
        assert ROECondition.WITHIN_CORRIDOR in default.conditions

    def test_default_rule_requires_threat_imminent(self) -> None:
        """Default rule includes THREAT_IMMINENT condition."""
        engine = ROEEngine()
        default = engine._rules[0]
        assert ROECondition.THREAT_IMMINENT in default.conditions


class TestROEMultipleRules:
    def test_multiple_rules_first_match_wins(self) -> None:
        """First rule that authorizes the engagement is returned."""
        rule_a = ROERule(
            name="RULE_A",
            conditions=[ROECondition.POSITIVE_ID, ROECondition.THREAT_IMMINENT,
                        ROECondition.WITHIN_CORRIDOR],
            authorized_effectors=["any"],
            min_confidence=0.95,  # high bar — will fail for 0.9 confidence track
            authorization_level="GENERAL",
        )
        rule_b = ROERule(
            name="RULE_B",
            conditions=[ROECondition.POSITIVE_ID, ROECondition.THREAT_IMMINENT,
                        ROECondition.WITHIN_CORRIDOR],
            authorized_effectors=["any"],
            min_confidence=0.8,   # lower bar — will pass
            authorization_level="CO",
        )
        engine = ROEEngine(rules=[rule_a, rule_b])
        priority = _make_priority(normalized_score=0.9)
        result = engine.evaluate(
            target_id="T1",
            engagement_priority=priority,
            track_confidence=0.9,   # passes rule_b but not rule_a
            target_position=(0.0, 0.0, 50.0),
            personnel_at_risk=0,
            corridors=_corridor_at_origin(),
            timestamp=time.time(),
        )
        assert result.authorized is True
        assert result.rule_name == "RULE_B"

    def test_all_rules_fail_returns_denied(self) -> None:
        """When no rule authorizes, result is denied."""
        rule = ROERule(
            name="STRICT",
            conditions=[ROECondition.POSITIVE_ID, ROECondition.THREAT_IMMINENT,
                        ROECondition.WITHIN_CORRIDOR],
            authorized_effectors=["any"],
            min_confidence=0.99,  # impossible bar
            authorization_level="POTUS",
        )
        engine = ROEEngine(rules=[rule])
        priority = _make_priority(normalized_score=0.9)
        result = engine.evaluate(
            target_id="T1",
            engagement_priority=priority,
            track_confidence=0.9,
            target_position=(0.0, 0.0, 50.0),
            personnel_at_risk=0,
            corridors=_corridor_at_origin(),
            timestamp=time.time(),
        )
        assert result.authorized is False

    def test_denied_low_threat_score(self) -> None:
        """normalized_score below 0.5 fails THREAT_IMMINENT condition."""
        engine = ROEEngine()
        priority = _make_priority(normalized_score=0.3)
        result = engine.evaluate(
            target_id="T5",
            engagement_priority=priority,
            track_confidence=0.95,
            target_position=(0.0, 0.0, 50.0),
            personnel_at_risk=0,
            corridors=_corridor_at_origin(),
            timestamp=time.time(),
        )
        assert result.authorized is False
        assert result.conditions_met.get(ROECondition.THREAT_IMMINENT) is False
