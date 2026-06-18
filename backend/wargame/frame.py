"""
Frame snapshots and live metrics for the wargame.

Metrics is the scoreboard the runner recomputes each tick: how many hostiles are
still flying, how many tracks the fusion engine holds, how many leakers reached
the site, the intercept rate, and the cost figures that drive the headline
cost-exchange ratio. Frame bundles those metrics with the renderable geometry the
HUD needs: tracks, defenders, and assignment lines.

Everything here is JSON-serializable through to_dict so the websocket and the CLI
share one shape. Positions ship as ENU meters and as lat/lon for the map layer.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from csontology import (
    Defender,
    Engagement,
    SwarmIntent,
    Threat,
    Track,
    Vec3,
    enu_to_latlon,
)


def _safe_ratio(value: Optional[float]) -> Optional[float]:
    """Return a JSON-safe rounded ratio or None for undefined, NaN, or inf.

    The websocket ships this as JSON and JSON has no NaN or inf, so any
    non-finite ratio becomes null rather than producing invalid JSON.
    """
    if value is None or not math.isfinite(value):
        return None
    return round(value, 4)


@dataclass(frozen=True)
class Metrics:
    """The live scoreboard for one tick."""

    tick: int
    sim_time_s: float
    active_hostiles: int
    tracks_held: int
    leakers: int
    engagements_made: int
    intercepts: int
    intercept_rate: float
    defender_spent: float
    attacker_destroyed: float
    cost_exchange_ratio: Optional[float]

    def to_dict(self) -> Dict[str, object]:
        """Serialize the scoreboard to plain JSON types."""
        return {
            "tick": self.tick,
            "sim_time_s": round(self.sim_time_s, 2),
            "active_hostiles": self.active_hostiles,
            "tracks_held": self.tracks_held,
            "leakers": self.leakers,
            "engagements_made": self.engagements_made,
            "intercepts": self.intercepts,
            "intercept_rate": round(self.intercept_rate, 4),
            "defender_spent": round(self.defender_spent, 2),
            "attacker_destroyed": round(self.attacker_destroyed, 2),
            "cost_exchange_ratio": _safe_ratio(self.cost_exchange_ratio),
            "cost_exchange_win": self._is_win(),
        }

    def _is_win(self) -> Optional[bool]:
        """Report whether the cost exchange favors the defense, or None if undefined.

        A ratio below 1.0 means the defense spent less than the airframe value it
        destroyed, the headline win state. None means no kills have priced it yet.
        """
        ratio = self.cost_exchange_ratio
        if ratio is None or not math.isfinite(ratio):
            return None
        return ratio < 1.0


def _track_to_dict(
    track: Track, threat: Optional[Threat] = None,
) -> Dict[str, object]:
    """Serialize one track with ENU and geodetic position for the HUD.

    When the threat layer scored this track, attach its swarm intent, swarm id,
    threat score, and time to impact so the HUD can color and label by intent.
    Unscored tracks report UNKNOWN intent and null time to impact.
    """
    lat, lon, _alt = enu_to_latlon(*track.position)
    intent = threat.intent.value if threat is not None else SwarmIntent.UNKNOWN.value
    return {
        "id": track.id,
        "enu": [round(c, 1) for c in track.position],
        "lat": lat,
        "lon": lon,
        "velocity": [round(c, 2) for c in track.velocity],
        "classification": track.classification.value,
        "confidence": round(track.confidence, 3),
        "intent": intent,
        "swarm_id": threat.swarm_id if threat is not None else None,
        "threat_score": round(threat.score, 3) if threat is not None else None,
        "time_to_impact_s": _impact_seconds(threat),
    }


def _impact_seconds(threat: Optional[Threat]) -> Optional[float]:
    """Return a JSON-safe time-to-impact in seconds, or None when not closing."""
    if threat is None or threat.time_to_impact_s is None:
        return None
    if not math.isfinite(threat.time_to_impact_s):
        return None
    return round(threat.time_to_impact_s, 1)


def _defender_to_dict(defender: Defender) -> Dict[str, object]:
    """Serialize one defender with ENU and geodetic position for the HUD."""
    lat, lon, _alt = enu_to_latlon(*defender.position)
    return {
        "id": defender.id,
        "kind": defender.kind.value,
        "enu": [round(c, 1) for c in defender.position],
        "lat": lat,
        "lon": lon,
        "capacity": defender.capacity,
        "status": defender.status.value,
    }


@dataclass
class Frame:
    """One renderable snapshot of the wargame for the HUD and the CLI."""

    metrics: Metrics
    tracks: List[Track]
    defenders: List[Defender]
    # Scored threats for this tick, used to attach intent and time to impact to
    # the matching tracks. Optional so a Frame built without the threat layer, or
    # an older caller, still serializes cleanly.
    threats: List[Threat] = field(default_factory=list)
    # Assignment lines as (defender_id, track_id, status) for the HUD to draw.
    assignments: List[Tuple[str, str, str]] = field(default_factory=list)
    site_enu: Vec3 = (0.0, 0.0, 0.0)
    scenario_name: str = ""
    done: bool = False
    cascade_results: List["CascadeResult"] = field(default_factory=list)
    engagement_order: Optional["EngagementOrder"] = None
    visual_targets: List[dict] = field(default_factory=list)
    heatmap_data: Optional[dict] = None

    def to_dict(self) -> Dict[str, object]:
        """Serialize the whole frame to a JSON-ready dict for the websocket."""
        slat, slon, _ = enu_to_latlon(*self.site_enu)
        threat_by_track = self._threats_by_track()
        return {
            "type": "WARGAME_FRAME",
            "scenario": self.scenario_name,
            "done": self.done,
            "metrics": self.metrics.to_dict() if self.metrics is not None else None,
            "site": {"enu": list(self.site_enu), "lat": slat, "lon": slon},
            "tracks": [
                _track_to_dict(t, threat_by_track.get(t.id)) for t in self.tracks
            ],
            "defenders": [_defender_to_dict(d) for d in self.defenders],
            "assignments": [
                {"defender_id": d, "track_id": t, "status": s}
                for d, t, s in self.assignments
            ],
            "cascade_results": [cr.to_dict() for cr in self.cascade_results],
            "engagement_order": self.engagement_order.to_dict() if self.engagement_order else None,
            "visual_targets": self.visual_targets,
            "heatmap_data": self.heatmap_data,
        }

    def _threats_by_track(self) -> Dict[str, Threat]:
        """Index this tick's threats by track id, keeping the highest scored one.

        One track can back at most one live threat here, but if duplicates appear
        we keep the most dangerous so the HUD labels the track by its worst case.
        """
        by_track: Dict[str, Threat] = {}
        for threat in self.threats:
            if threat.track_id is None:
                continue
            current = by_track.get(threat.track_id)
            if current is None or threat.score > current.score:
                by_track[threat.track_id] = threat
        return by_track


def assignment_lines(
    engagements: List[Engagement],
    threats: List[Threat],
) -> List[Tuple[str, str, str]]:
    """Map engagements to (defender_id, track_id, status) lines for the HUD.

    A swarm threat has no single track, so it draws no line. Track threats draw a
    line from the defender to the threatened track.
    """
    threat_track = {t.id: t.track_id for t in threats}
    lines: List[Tuple[str, str, str]] = []
    for eng in engagements:
        track_id = threat_track.get(eng.target_threat_id)
        if track_id is None:
            continue
        lines.append((eng.defender_id, track_id, eng.status.value))
    return lines
