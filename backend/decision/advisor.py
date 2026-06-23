"""Operator decision support for BULWARK.

Analyzes the current tactical picture (threats, defenders, engagement history)
and produces a ranked list of recommended actions for human operators. Runs
pure decision rules with no side effects.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from csontology import Defender, DefenderStatus, Threat, Vec3
from defense.allocator import CostLedger
from wargame.frame import Frame


class RecommendationType(Enum):
    REPOSITION_DEFENDER = "reposition_defender"
    ADD_EFFECTOR = "add_effector"
    CHANGE_ROE = "change_roe"
    INCREASE_COVERAGE = "increase_coverage"
    CONSERVE_AMMO = "conserve_ammo"
    CALL_REINFORCEMENT = "call_reinforcement"
    EVACUATE_SITE = "evacuate_site"


@dataclass
class TacticalRecommendation:
    type: RecommendationType
    priority: int  # 1 = highest
    description: str
    rationale: str
    confidence: float  # 0..1
    estimated_impact: str


_N_SECTORS = 8
_SECTOR_LABELS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _bearing_deg(origin: Vec3, target: Vec3) -> float:
    return math.degrees(math.atan2(target[0] - origin[0], target[1] - origin[1])) % 360.0


def _sector_index(bearing: float) -> int:
    return int(bearing / (360.0 / _N_SECTORS)) % _N_SECTORS


def _by_status(defenders: List[Defender], status: DefenderStatus) -> List[Defender]:
    return [d for d in defenders if d.status is status]


def _uncovered_sector(
    threats: List[Threat], defenders: List[Defender],
    site: Vec3, positions: dict[str, Vec3],
) -> Optional[str]:
    """Return compass label if threats approach from a sector with no defender."""
    covered: set[int] = set()
    for d in defenders:
        if d.status in (DefenderStatus.READY, DefenderStatus.ENGAGING):
            covered.add(_sector_index(_bearing_deg(site, d.position)))
    for t in threats:
        pos = positions.get(t.track_id or "")
        if pos is None:
            continue
        idx = _sector_index(_bearing_deg(site, pos))
        if idx not in covered:
            return _SECTOR_LABELS[idx]
    return None


class TacticalAdvisor:
    """Produces ranked operator recommendations from the current situation."""

    def __init__(self, site: Vec3 = (0.0, 0.0, 0.0)) -> None:
        self._site = site

    def analyze(
        self, frame: Frame, ledger: Optional[CostLedger] = None,
        threat_positions: Optional[dict[str, Vec3]] = None,
    ) -> List[TacticalRecommendation]:
        """Return recommendations sorted by priority (1 = most urgent)."""
        recs: List[TacticalRecommendation] = []
        m = frame.metrics
        defs = frame.defenders

        # Leak rate > 20% -> CALL_REINFORCEMENT
        if m.engagements_made > 0:
            rate = m.leakers / m.engagements_made
            if rate > 0.20:
                recs.append(TacticalRecommendation(
                    RecommendationType.CALL_REINFORCEMENT, 1,
                    "Request reinforcement assets",
                    f"Leak rate {rate:.0%} exceeds 20% threshold",
                    min(1.0, 0.6 + rate), "Reduce leaker count with fresh effectors",
                ))

        # Cost ratio > 2.0 -> CONSERVE_AMMO
        if ledger is not None:
            ratio = ledger.cost_exchange_ratio
            if ratio is not None and ratio > 2.0:
                recs.append(TacticalRecommendation(
                    RecommendationType.CONSERVE_AMMO, 2,
                    "Switch to ammo conservation posture",
                    f"Cost ratio {ratio:.2f} exceeds 2.0; defense overspending",
                    min(1.0, 0.5 + 0.1 * ratio),
                    "Reduce cost ratio by reserving expensive effectors",
                ))

        # Any defender DEPLETED -> ADD_EFFECTOR
        depleted = _by_status(defs, DefenderStatus.DEPLETED)
        if depleted:
            ids = ", ".join(d.id for d in depleted[:3])
            recs.append(TacticalRecommendation(
                RecommendationType.ADD_EFFECTOR, 2,
                f"Replace depleted effector(s): {ids}",
                f"{len(depleted)} defender(s) fully depleted", 0.95,
                "Restore engagement capacity at depleted positions",
            ))

        # Uncovered sector -> REPOSITION_DEFENDER
        pos_map = threat_positions or {}
        if pos_map:
            sector = _uncovered_sector(
                frame.threats, defs, self._site, pos_map,
            )
            if sector is not None:
                recs.append(TacticalRecommendation(
                    RecommendationType.REPOSITION_DEFENDER, 3,
                    f"Reposition defender to cover {sector} sector",
                    f"Threats approaching from {sector} with no defender coverage",
                    0.80, f"Close coverage gap in {sector} sector",
                ))

        # All engaged + new threats -> INCREASE_COVERAGE
        if defs:
            ready = _by_status(defs, DefenderStatus.READY)
            engaging = _by_status(defs, DefenderStatus.ENGAGING)
            all_busy = len(engaging) == len(defs) or not ready
            if all_busy and m.active_hostiles > m.engagements_made:
                recs.append(TacticalRecommendation(
                    RecommendationType.INCREASE_COVERAGE, 2,
                    "Expand defensive coverage",
                    "All defenders engaged with new threats still appearing",
                    0.85, "Prevent unengaged threats from leaking through",
                ))

        # >50% depleted + hostiles active -> EVACUATE_SITE
        if defs and depleted:
            frac = len(depleted) / len(defs)
            if frac > 0.50 and m.active_hostiles > 0:
                recs.append(TacticalRecommendation(
                    RecommendationType.EVACUATE_SITE, 1,
                    "Consider site evacuation",
                    f"{len(depleted)}/{len(defs)} defenders depleted "
                    f"with {m.active_hostiles} hostiles still active",
                    min(1.0, 0.70 + 0.2 * frac),
                    "Preserve personnel if defense is no longer viable",
                ))

        recs.sort(key=lambda r: r.priority)
        return recs
