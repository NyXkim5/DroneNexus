"""
Rules of Engagement (ROE) engine for OVERWATCH/BULWARK.

Every engagement decision must pass through this gate before execution.
Each evaluation is written to an immutable audit log — this is the legal record.
No target is engaged without a traceable authorization chain.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from csontology import Vec3

if TYPE_CHECKING:
    from csontology import Track
    from decision.models import EngagementOrder, EngagementPriority


class ROECondition(Enum):
    POSITIVE_ID = "positive_id"          # target positively identified as hostile
    WITHIN_CORRIDOR = "within_corridor"  # target within authorized engagement zone
    THREAT_IMMINENT = "threat_imminent"  # threat score above threshold
    AUTHORIZED_WEAPON = "authorized_weapon"  # effector type is authorized
    ALTITUDE_FLOOR = "altitude_floor"    # target above minimum engagement altitude
    CIVILIAN_CLEAR = "civilian_clear"    # no civilian assets in blast radius


@dataclass
class ROERule:
    name: str
    conditions: List[ROECondition]       # ALL must be met (AND logic)
    authorized_effectors: List[str]      # which effector types this rule permits
    max_cascade_personnel: int = 0       # max personnel_at_risk allowed (0 = no limit)
    min_confidence: float = 0.8          # minimum track confidence to engage
    authorization_level: str = "CO"      # who authorized this rule


@dataclass
class ROEEvaluation:
    target_id: str
    rule_name: str
    conditions_met: Dict[ROECondition, bool]
    authorized: bool
    reason: str                          # human-readable explanation
    timestamp: float
    authorization_level: str


def _distance_3d(a: Vec3, b: Vec3) -> float:
    return math.sqrt(
        (a[0] - b[0]) ** 2
        + (a[1] - b[1]) ** 2
        + (a[2] - b[2]) ** 2
    )


def _in_any_corridor(
    position: Vec3,
    corridors: List[Tuple[Vec3, float]],
) -> bool:
    """Return True if position falls within at least one (center, radius_m) zone."""
    for center, radius_m in corridors:
        if _distance_3d(position, center) <= radius_m:
            return True
    return False


def _build_default_rules() -> List[ROERule]:
    """Standard defensive ROE: positive ID + imminent threat + within corridor."""
    return [
        ROERule(
            name="DEFENSIVE_STANDARD",
            conditions=[
                ROECondition.POSITIVE_ID,
                ROECondition.THREAT_IMMINENT,
                ROECondition.WITHIN_CORRIDOR,
            ],
            authorized_effectors=["any"],
            max_cascade_personnel=0,
            min_confidence=0.8,
            authorization_level="CO",
        )
    ]


class ROEEngine:
    def __init__(self, rules: Optional[List[ROERule]] = None) -> None:
        self._rules: List[ROERule] = rules if rules is not None else _build_default_rules()
        self._log: List[ROEEvaluation] = []

    def evaluate(
        self,
        target_id: str,
        engagement_priority: "EngagementPriority",
        track_confidence: float,
        target_position: Vec3,
        personnel_at_risk: int,
        corridors: List[Tuple[Vec3, float]],
        timestamp: float,
    ) -> ROEEvaluation:
        """
        Evaluate whether engagement is authorized under current ROE.

        Checks each rule's conditions in order. Returns the first matching
        rule's authorization. If no rule matches, returns a denial. Every
        call is appended to the audit log regardless of outcome.
        """
        last_evaluation: Optional[ROEEvaluation] = None

        for rule in self._rules:
            evaluation = self._check_rule(
                rule=rule,
                target_id=target_id,
                engagement_priority=engagement_priority,
                track_confidence=track_confidence,
                target_position=target_position,
                personnel_at_risk=personnel_at_risk,
                corridors=corridors,
                timestamp=timestamp,
            )
            # Log every rule check so the record is complete.
            self._log.append(evaluation)
            last_evaluation = evaluation
            if evaluation.authorized:
                return evaluation

        # No rule authorized. Return the last rule's denial so the specific
        # reason (low confidence, personnel limit, unmet conditions) is preserved.
        if last_evaluation is not None:
            return last_evaluation

        # Rule list was empty — synthesize a generic denial.
        denial = ROEEvaluation(
            target_id=target_id,
            rule_name="DENY_NO_MATCHING_RULE",
            conditions_met={},
            authorized=False,
            reason="No ROE rule authorized this engagement.",
            timestamp=timestamp,
            authorization_level="NONE",
        )
        self._log.append(denial)
        return denial

    def _check_rule(
        self,
        rule: ROERule,
        target_id: str,
        engagement_priority: "EngagementPriority",
        track_confidence: float,
        target_position: Vec3,
        personnel_at_risk: int,
        corridors: List[Tuple[Vec3, float]],
        timestamp: float,
    ) -> ROEEvaluation:
        """Evaluate a single ROERule against the provided engagement context."""
        conditions_met: Dict[ROECondition, bool] = {}
        denial_reasons: List[str] = []

        # --- Confidence gate (not a condition flag, but a hard prerequisite) ---
        if track_confidence < rule.min_confidence:
            # Fill all required conditions as False and deny immediately.
            for cond in rule.conditions:
                conditions_met[cond] = False
            reason = (
                f"Track confidence {track_confidence:.2f} below "
                f"minimum {rule.min_confidence:.2f} required by rule '{rule.name}'."
            )
            return ROEEvaluation(
                target_id=target_id,
                rule_name=rule.name,
                conditions_met=conditions_met,
                authorized=False,
                reason=reason,
                timestamp=timestamp,
                authorization_level=rule.authorization_level,
            )

        # --- Personnel limit gate ---
        if rule.max_cascade_personnel > 0 and personnel_at_risk > rule.max_cascade_personnel:
            for cond in rule.conditions:
                conditions_met[cond] = False
            reason = (
                f"Personnel at risk ({personnel_at_risk}) exceeds rule "
                f"'{rule.name}' maximum ({rule.max_cascade_personnel})."
            )
            return ROEEvaluation(
                target_id=target_id,
                rule_name=rule.name,
                conditions_met=conditions_met,
                authorized=False,
                reason=reason,
                timestamp=timestamp,
                authorization_level=rule.authorization_level,
            )

        # --- Evaluate each declared condition ---
        for cond in rule.conditions:
            met = self._eval_condition(
                cond=cond,
                engagement_priority=engagement_priority,
                track_confidence=track_confidence,
                target_position=target_position,
                corridors=corridors,
                rule=rule,
            )
            conditions_met[cond] = met
            if not met:
                denial_reasons.append(cond.value)

        authorized = len(denial_reasons) == 0

        if authorized:
            reason = (
                f"Authorized under rule '{rule.name}' "
                f"(level: {rule.authorization_level}). "
                f"All {len(rule.conditions)} conditions satisfied."
            )
        else:
            reason = (
                f"Denied under rule '{rule.name}'. "
                f"Unmet conditions: {', '.join(denial_reasons)}."
            )

        return ROEEvaluation(
            target_id=target_id,
            rule_name=rule.name,
            conditions_met=conditions_met,
            authorized=authorized,
            reason=reason,
            timestamp=timestamp,
            authorization_level=rule.authorization_level,
        )

    def _eval_condition(
        self,
        cond: ROECondition,
        engagement_priority: "EngagementPriority",
        track_confidence: float,
        target_position: Vec3,
        corridors: List[Tuple[Vec3, float]],
        rule: ROERule,
    ) -> bool:
        if cond == ROECondition.POSITIVE_ID:
            # Confidence at or above the rule minimum constitutes positive ID.
            return track_confidence >= rule.min_confidence

        if cond == ROECondition.WITHIN_CORRIDOR:
            return _in_any_corridor(target_position, corridors)

        if cond == ROECondition.THREAT_IMMINENT:
            # normalized_score >= 0.5 constitutes an imminent threat.
            return engagement_priority.normalized_score >= 0.5

        if cond == ROECondition.AUTHORIZED_WEAPON:
            effector = engagement_priority.recommended_effector
            return (
                "any" in rule.authorized_effectors
                or effector in rule.authorized_effectors
            )

        if cond == ROECondition.ALTITUDE_FLOOR:
            # z-axis is Up in ENU. Require target above 10 m (ground clutter floor).
            return target_position[2] >= 10.0

        if cond == ROECondition.CIVILIAN_CLEAR:
            # personnel_impact on EngagementPriority is civilians proximate to target.
            # Zero means clear. This condition is evaluated via the priority object.
            return engagement_priority.personnel_impact == 0

        return False

    def gate_engagement_order(
        self,
        order: "EngagementOrder",
        tracks: Dict[str, "Track"],
        corridors: List[Tuple[Vec3, float]],
        timestamp: float,
    ) -> "EngagementOrder":
        """
        Filter an EngagementOrder through ROE.

        Removes unauthorized targets from the priority list. Adds ROE status
        to the order's rationale dict so every decision has a traceable basis.
        Returns the same EngagementOrder object with unauthorized priorities
        stripped and rationale updated in place.
        """
        authorized_priorities = []

        for priority in order.priorities:
            track = tracks.get(priority.target_id)
            track_confidence = track.confidence if track is not None else 0.0
            target_position: Vec3 = track.position if track is not None else (0.0, 0.0, 0.0)

            evaluation = self.evaluate(
                target_id=priority.target_id,
                engagement_priority=priority,
                track_confidence=track_confidence,
                target_position=target_position,
                personnel_at_risk=priority.personnel_impact,
                corridors=corridors,
                timestamp=timestamp,
            )

            roe_status = (
                f"ROE={'AUTHORIZED' if evaluation.authorized else 'DENIED'} "
                f"rule={evaluation.rule_name} | {evaluation.reason}"
            )

            existing = order.rationale.get(priority.target_id, "")
            order.rationale[priority.target_id] = (
                f"{existing} | {roe_status}" if existing else roe_status
            )

            if evaluation.authorized:
                authorized_priorities.append(priority)

        order.priorities = authorized_priorities
        return order

    @property
    def audit_log(self) -> List[ROEEvaluation]:
        return list(self._log)

    def export_audit(self) -> List[dict]:
        """Export audit log as list of dicts for after-action review."""
        return [
            {
                "target_id": e.target_id,
                "rule_name": e.rule_name,
                "conditions_met": {
                    cond.value: met for cond, met in e.conditions_met.items()
                },
                "authorized": e.authorized,
                "reason": e.reason,
                "timestamp": e.timestamp,
                "authorization_level": e.authorization_level,
            }
            for e in self._log
        ]
