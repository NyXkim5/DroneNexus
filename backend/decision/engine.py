from __future__ import annotations

from typing import List

from csontology import Threat
from vision.cascade import CascadeResult
from decision.models import EngagementMode, EngagementPriority, EngagementOrder


class DecisionEngine:
    def __init__(self, mode: EngagementMode = EngagementMode.ADVISORY) -> None:
        self._mode = mode

    @property
    def mode(self) -> EngagementMode:
        return self._mode

    @mode.setter
    def mode(self, value: EngagementMode) -> None:
        self._mode = value

    def merge(
        self,
        threats: List[Threat],
        cascade_results: List[CascadeResult],
        now: float,
    ) -> EngagementOrder:
        priorities: List[EngagementPriority] = []
        rationale: dict[str, str] = {}

        for t in threats:
            time_sens = max(0.0, 1.0 - (t.time_to_impact_s or 300.0) / 300.0)
            score = t.score * 0.8 + time_sens * 0.2
            priorities.append(EngagementPriority(
                target_id=t.id,
                source="bulwark",
                normalized_score=min(1.0, score),
                time_sensitivity=t.time_to_impact_s or 300.0,
                personnel_impact=0,
                cascade_depth=0,
            ))
            rationale[t.id] = (
                f"Defensive threat: score={t.score:.2f}, "
                f"TTI={t.time_to_impact_s:.0f}s, intent={t.intent.value}"
            )

        if cascade_results:
            max_ev = max(c.expected_value for c in cascade_results)
            for c in cascade_results:
                norm_score = c.expected_value / max_ev if max_ev > 0 else 0.0
                priorities.append(EngagementPriority(
                    target_id=c.target_id,
                    source="vision",
                    normalized_score=min(1.0, norm_score * 0.7),
                    time_sensitivity=300.0,
                    personnel_impact=c.personnel_at_risk,
                    cascade_depth=len(c.cascade_chain) - 1,
                ))
                rationale[c.target_id] = (
                    f"Offensive target: EV=${c.expected_value:,.0f}, "
                    f"chain={len(c.cascade_chain)}, "
                    f"personnel={c.personnel_at_risk}"
                )

        priorities.sort(key=lambda p: (-p.normalized_score, p.time_sensitivity))

        return EngagementOrder(
            priorities=priorities,
            mode=self._mode,
            timestamp=now,
            rationale=rationale,
        )
