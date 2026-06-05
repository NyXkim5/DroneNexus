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

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from csontology import (
    Defender,
    Engagement,
    Threat,
    Track,
    Vec3,
    enu_to_latlon,
)


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
            "cost_exchange_ratio": (
                round(self.cost_exchange_ratio, 4)
                if self.cost_exchange_ratio is not None
                else None
            ),
        }


def _track_to_dict(track: Track) -> Dict[str, object]:
    """Serialize one track with ENU and geodetic position for the HUD."""
    lat, lon, _alt = enu_to_latlon(*track.position)
    return {
        "id": track.id,
        "enu": [round(c, 1) for c in track.position],
        "lat": lat,
        "lon": lon,
        "velocity": [round(c, 2) for c in track.velocity],
        "classification": track.classification.value,
        "confidence": round(track.confidence, 3),
    }


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
    # Assignment lines as (defender_id, track_id, status) for the HUD to draw.
    assignments: List[Tuple[str, str, str]] = field(default_factory=list)
    site_enu: Vec3 = (0.0, 0.0, 0.0)
    scenario_name: str = ""
    done: bool = False

    def to_dict(self) -> Dict[str, object]:
        """Serialize the whole frame to a JSON-ready dict for the websocket."""
        slat, slon, _ = enu_to_latlon(*self.site_enu)
        return {
            "type": "WARGAME_FRAME",
            "scenario": self.scenario_name,
            "done": self.done,
            "metrics": self.metrics.to_dict(),
            "site": {"enu": list(self.site_enu), "lat": slat, "lon": slon},
            "tracks": [_track_to_dict(t) for t in self.tracks],
            "defenders": [_defender_to_dict(d) for d in self.defenders],
            "assignments": [
                {"defender_id": d, "track_id": t, "status": s}
                for d, t, s in self.assignments
            ],
        }


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
