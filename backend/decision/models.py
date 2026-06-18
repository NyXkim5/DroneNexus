from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Any


class EngagementMode(Enum):
    AUTO = "auto"
    ADVISORY = "advisory"


@dataclass
class EngagementPriority:
    target_id: str
    source: str
    normalized_score: float
    time_sensitivity: float
    personnel_impact: int
    cascade_depth: int
    recommended_effector: str = "any"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_id": self.target_id,
            "source": self.source,
            "normalized_score": round(self.normalized_score, 4),
            "time_sensitivity": round(self.time_sensitivity, 2),
            "personnel_impact": self.personnel_impact,
            "cascade_depth": self.cascade_depth,
            "recommended_effector": self.recommended_effector,
        }


@dataclass
class EngagementOrder:
    priorities: List[EngagementPriority]
    mode: EngagementMode
    timestamp: float
    rationale: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "ENGAGEMENT_ORDER",
            "mode": self.mode.value,
            "timestamp": self.timestamp,
            "priorities": [p.to_dict() for p in self.priorities],
            "rationale": self.rationale,
        }
