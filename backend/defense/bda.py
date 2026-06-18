"""
Battle Damage Assessment (BDA) for OVERWATCH/BULWARK.

The BDA system registers engagements and, after a configurable assessment delay,
evaluates each one by querying the live track picture. Outcomes range from
CONFIRMED_KILL (target gone) through DAMAGED and MISSED (target still up) to
RE_ENGAGE (caller should schedule a follow-on shot). Targets that survived are
surfaced via get_re_engage_targets(), sorted by descending re-engage priority.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger("overwatch.bda")


class BDAStatus(Enum):
    PENDING = "pending"          # engagement just occurred, awaiting assessment
    CONFIRMED_KILL = "confirmed" # target destroyed/neutralized
    PROBABLE_KILL = "probable"   # target likely destroyed but unconfirmed
    DAMAGED = "damaged"          # target hit but still operational
    MISSED = "missed"            # no observable effect
    RE_ENGAGE = "re_engage"      # target survived, recommend re-engagement


@dataclass
class BDAReport:
    engagement_id: str
    target_id: str
    effector_id: str
    status: BDAStatus
    confidence: float           # 0-1
    assessment_time: float
    evidence: str               # what observation led to this assessment
    re_engage_priority: float   # 0-1, 0 = no re-engage needed


# Thresholds used during assessment logic.
_SPEED_NEAR_ZERO: float = 0.5          # m/s threshold to consider "stationary"
_CONFIDENCE_LOW: float = 0.3           # track confidence below which fading/probable kill
_SPEED_REDUCTION_DAMAGED: float = 0.5  # >50% speed drop signals DAMAGED


class BDASystem:
    """Assesses engagement outcomes and recommends re-engagement."""

    def __init__(self, assessment_delay_s: float = 3.0) -> None:
        # (timestamp_of_engagement, engagement_id, target_id, effector_id)
        self._pending: List[Tuple[float, str, str, str]] = []
        self._reports: List[BDAReport] = []
        self._delay = assessment_delay_s
        # Snapshot of target speed at engagement time for later comparison.
        self._speed_at_engagement: dict[str, float] = {}

    def register_engagement(
        self,
        engagement_id: str,
        target_id: str,
        effector_id: str,
        timestamp: float,
        initial_speed: Optional[float] = None,
    ) -> None:
        """Register a new engagement for assessment.

        initial_speed is the target's speed at engagement time; when supplied
        it enables the DAMAGED heuristic. Callers that cannot supply it may
        omit it; the heuristic degrades gracefully to MISSED in that case.
        """
        self._pending.append((timestamp, engagement_id, target_id, effector_id))
        if initial_speed is not None:
            self._speed_at_engagement[engagement_id] = initial_speed
        logger.debug(
            "BDA registered engagement=%s target=%s effector=%s",
            engagement_id, target_id, effector_id,
        )

    def assess(
        self,
        timestamp: float,
        track_exists: Callable[[str], bool],
        track_speed: Callable[[str], float],
        track_confidence: Callable[[str], float],
    ) -> List[BDAReport]:
        """Assess all pending engagements where enough time has passed.

        Logic (in priority order):
        - Target no longer tracked: CONFIRMED_KILL
        - Target confidence below threshold: PROBABLE_KILL (fading track)
        - Target tracked, speed near 0 and confidence dropping: PROBABLE_KILL
        - Target tracked, speed reduced >50% vs engagement snapshot: DAMAGED
        - Target tracked, speed unchanged: MISSED
        """
        ready: List[Tuple[float, str, str, str]] = []
        still_pending: List[Tuple[float, str, str, str]] = []

        for entry in self._pending:
            eng_ts, eng_id, target_id, effector_id = entry
            if timestamp - eng_ts >= self._delay:
                ready.append(entry)
            else:
                still_pending.append(entry)

        self._pending = still_pending

        new_reports: List[BDAReport] = []
        for eng_ts, eng_id, target_id, effector_id in ready:
            report = self._assess_one(
                eng_id, target_id, effector_id, timestamp,
                track_exists, track_speed, track_confidence,
            )
            self._reports.append(report)
            new_reports.append(report)
            logger.info(
                "BDA result engagement=%s target=%s status=%s confidence=%.2f",
                eng_id, target_id, report.status.value, report.confidence,
            )

        return new_reports

    def _assess_one(
        self,
        engagement_id: str,
        target_id: str,
        effector_id: str,
        timestamp: float,
        track_exists: Callable[[str], bool],
        track_speed: Callable[[str], float],
        track_confidence: Callable[[str], float],
    ) -> BDAReport:
        """Produce one BDAReport for a single matured engagement."""
        exists = track_exists(target_id)

        if not exists:
            return BDAReport(
                engagement_id=engagement_id,
                target_id=target_id,
                effector_id=effector_id,
                status=BDAStatus.CONFIRMED_KILL,
                confidence=0.95,
                assessment_time=timestamp,
                evidence="Target track lost after engagement.",
                re_engage_priority=0.0,
            )

        confidence = track_confidence(target_id)

        if confidence < _CONFIDENCE_LOW:
            return BDAReport(
                engagement_id=engagement_id,
                target_id=target_id,
                effector_id=effector_id,
                status=BDAStatus.PROBABLE_KILL,
                confidence=0.65,
                assessment_time=timestamp,
                evidence=f"Track confidence dropped to {confidence:.2f} post-engagement.",
                re_engage_priority=0.1,
            )

        speed = track_speed(target_id)

        if speed <= _SPEED_NEAR_ZERO and confidence < 0.6:
            return BDAReport(
                engagement_id=engagement_id,
                target_id=target_id,
                effector_id=effector_id,
                status=BDAStatus.PROBABLE_KILL,
                confidence=0.7,
                assessment_time=timestamp,
                evidence=(
                    f"Target stationary (speed={speed:.1f} m/s) "
                    f"with fading confidence={confidence:.2f}."
                ),
                re_engage_priority=0.15,
            )

        initial_speed = self._speed_at_engagement.get(engagement_id)
        if initial_speed is not None and initial_speed > 0.0:
            reduction = (initial_speed - speed) / initial_speed
            if reduction > _SPEED_REDUCTION_DAMAGED:
                return BDAReport(
                    engagement_id=engagement_id,
                    target_id=target_id,
                    effector_id=effector_id,
                    status=BDAStatus.DAMAGED,
                    confidence=0.75,
                    assessment_time=timestamp,
                    evidence=(
                        f"Speed reduced {reduction*100:.0f}% "
                        f"({initial_speed:.1f} -> {speed:.1f} m/s)."
                    ),
                    re_engage_priority=0.7,
                )

        return BDAReport(
            engagement_id=engagement_id,
            target_id=target_id,
            effector_id=effector_id,
            status=BDAStatus.MISSED,
            confidence=0.8,
            assessment_time=timestamp,
            evidence=(
                f"Target still tracked at speed={speed:.1f} m/s "
                f"confidence={confidence:.2f}. No observable effect."
            ),
            re_engage_priority=0.9,
        )

    def get_re_engage_targets(self) -> List[BDAReport]:
        """Return all targets needing re-engagement, sorted by priority descending."""
        candidates = [
            r for r in self._reports
            if r.re_engage_priority > 0.0 and r.status in {
                BDAStatus.MISSED, BDAStatus.DAMAGED, BDAStatus.RE_ENGAGE,
            }
        ]
        return sorted(candidates, key=lambda r: r.re_engage_priority, reverse=True)

    @property
    def reports(self) -> List[BDAReport]:
        return list(self._reports)
