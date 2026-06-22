"""
Predictive threat modeling for OVERWATCH.

Forecasts hostile drone positions and identifies likely targets using linear
extrapolation, intent-based approach, historical attack patterns, and swarm
centroid coherence.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from csontology import Site, Swarm, Track, TrackClass, Vec3
from threat.intent import closing_speed_to_site

logger = logging.getLogger("overwatch.predictor")

PREDICTION_STEP_S = 5.0
SITE_HIT_RADIUS_M = 50.0
MIN_CORRIDOR_TRACKS = 3
SWARM_COHERENCE_WEIGHT = 0.6


@dataclass
class ThreatPrediction:
    """Forecast for a single hostile track."""
    track_id: str
    predicted_positions: List[Vec3]
    predicted_times: List[float]
    likely_target: Optional[str]
    impact_probability: float
    estimated_time_to_target: float
    confidence: float
    approach_vector: Vec3


@dataclass
class ThreatCorridor:
    """A detected convergence corridor toward a defended site."""
    origin_bearing: float
    width_deg: float
    depth_m: float
    estimated_count: int
    target_site: Optional[str]


def _bearing_deg(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _normalize(v: Vec3) -> Vec3:
    mag = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if mag < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / mag, v[1] / mag, v[2] / mag)


def _range_2d(a: Vec3, b: Vec3) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _extrapolate(pos: Vec3, vel: Vec3, dt: float) -> Vec3:
    return (pos[0] + vel[0] * dt, pos[1] + vel[1] * dt, pos[2] + vel[2] * dt)


def _circular_mean(angles_deg: List[float]) -> float:
    if not angles_deg:
        return 0.0
    sx = sum(math.sin(math.radians(a)) for a in angles_deg)
    cy = sum(math.cos(math.radians(a)) for a in angles_deg)
    return math.degrees(math.atan2(sx, cy)) % 360.0


def _circular_spread(angles_deg: List[float]) -> float:
    if len(angles_deg) < 2:
        return 0.0
    sa = sorted(a % 360.0 for a in angles_deg)
    gaps = [(sa[(i + 1) % len(sa)] - sa[i]) % 360.0 for i in range(len(sa))]
    return 360.0 - max(gaps)


class ThreatPredictor:
    """Predict future positions and likely targets for hostile tracks."""

    def __init__(self, history: Optional[Dict[str, int]] = None) -> None:
        self._history: Dict[str, int] = history or {}

    def record_attack(self, site_id: str) -> None:
        self._history[site_id] = self._history.get(site_id, 0) + 1

    def predict(
        self, tracks: List[Track], sites: List[Site],
        horizon_s: float = 60, swarms: Optional[List[Swarm]] = None,
    ) -> List[ThreatPrediction]:
        """Predict future positions and likely targets for each track."""
        track_map = {t.id: t for t in tracks}
        swarm_map = _build_swarm_map(swarms)
        results: List[ThreatPrediction] = []
        for track in tracks:
            if track.classification == TrackClass.FRIENDLY:
                continue
            results.append(self._predict_single(
                track, sites, horizon_s, swarm_map, track_map,
            ))
        return results

    def detect_corridors(self, tracks: List[Track], sites: List[Site]) -> List[ThreatCorridor]:
        """Detect convergence corridors where tracks funnel toward a site."""
        corridors: List[ThreatCorridor] = []
        for site in sites:
            closing = [t for t in tracks if closing_speed_to_site(t, site) > 0
                       and t.classification != TrackClass.FRIENDLY]
            if len(closing) < MIN_CORRIDOR_TRACKS:
                continue
            bearings, ranges = [], []
            for t in closing:
                dx, dy = t.position[0] - site.position[0], t.position[1] - site.position[1]
                bearings.append(_bearing_deg(dx, dy))
                ranges.append(math.hypot(dx, dy))
            corridors.append(ThreatCorridor(
                round(_circular_mean(bearings), 1), round(_circular_spread(bearings), 1),
                round(max(ranges), 1), len(closing), site.id,
            ))
        return corridors

    def get_early_warnings(
        self, predictions: List[ThreatPrediction],
        corridors: Optional[List[ThreatCorridor]] = None,
    ) -> List[str]:
        """Generate human-readable early warning strings."""
        w: List[str] = []
        for c in (corridors or []):
            w.append(f"WARNING: {c.estimated_count} tracks converging on "
                     f"{c.target_site} from bearing {int(c.origin_bearing):03d}, "
                     f"spread {c.width_deg:.0f} deg")
        imminent = [p for p in predictions if p.estimated_time_to_target <= 30]
        if len(imminent) >= 5:
            w.append(f"ALERT: Track pattern consistent with SATURATION attack, "
                     f"{len(imminent)} tracks within 30s")
        for p in predictions:
            if p.impact_probability >= 0.8 and p.estimated_time_to_target <= 15:
                w.append(f"CRITICAL: {p.track_id} impact on {p.likely_target} "
                         f"in {p.estimated_time_to_target:.0f}s "
                         f"(p={p.impact_probability:.0%})")
        return w

    def _predict_single(
        self, track: Track, sites: List[Site], horizon_s: float,
        swarm_map: Dict[str, Swarm], track_map: Dict[str, Track],
    ) -> ThreatPrediction:
        vel = self._effective_velocity(track, swarm_map, track_map)
        positions: List[Vec3] = []
        times: List[float] = []
        for i in range(1, max(1, int(horizon_s / PREDICTION_STEP_S)) + 1):
            dt = i * PREDICTION_STEP_S
            positions.append(_extrapolate(track.position, vel, dt))
            times.append(dt)
        target_id, prob, eta = self._score_targets(track, positions, sites)
        return ThreatPrediction(
            track_id=track.id, predicted_positions=positions,
            predicted_times=times, likely_target=target_id,
            impact_probability=prob, estimated_time_to_target=eta,
            confidence=self._confidence(track, swarm_map),
            approach_vector=_normalize(vel),
        )

    def _effective_velocity(
        self, track: Track, swarm_map: Dict[str, Swarm],
        track_map: Dict[str, Track],
    ) -> Vec3:
        swarm = swarm_map.get(track.id)
        if swarm is None or swarm.size < 2:
            return track.velocity
        members = [track_map[t] for t in swarm.member_track_ids if t in track_map]
        if len(members) < 2:
            return track.velocity
        n = len(members)
        avg = (
            sum(m.velocity[0] for m in members) / n,
            sum(m.velocity[1] for m in members) / n,
            sum(m.velocity[2] for m in members) / n,
        )
        w = SWARM_COHERENCE_WEIGHT
        return (
            (1 - w) * track.velocity[0] + w * avg[0],
            (1 - w) * track.velocity[1] + w * avg[1],
            (1 - w) * track.velocity[2] + w * avg[2],
        )

    def _score_targets(
        self, track: Track, positions: List[Vec3], sites: List[Site],
    ) -> Tuple[Optional[str], float, float]:
        best_site: Optional[str] = None
        best_prob, best_eta = 0.0, float("inf")
        for site in sites:
            closing = closing_speed_to_site(track, site)
            if closing <= 0:
                continue
            eta = _range_2d(track.position, site.position) / closing
            prob = min(1.0, self._impact_prob(track, site, positions)
                       + self._history_boost(site.id))
            if prob > best_prob or (prob == best_prob and eta < best_eta):
                best_prob, best_eta, best_site = prob, eta, site.id
        if best_site is None:
            best_eta = float("inf")
        return best_site, best_prob, best_eta

    def _impact_prob(
        self, track: Track, site: Site, positions: List[Vec3],
    ) -> float:
        min_dist = min((_range_2d(p, site.position) for p in positions),
                       default=float("inf"))
        dist_factor = max(0.0, 1.0 - min_dist / max(1.0, SITE_HIT_RADIUS_M * 10))
        speed = math.hypot(track.velocity[0], track.velocity[1])
        speed_factor = min(1.0, speed / 30.0) if speed > 0 else 0.0
        closing = closing_speed_to_site(track, site)
        alignment = max(0.0, min(1.0, closing / max(speed, 0.1))) if speed > 0.1 else 0.0
        return 0.4 * dist_factor + 0.3 * alignment + 0.3 * speed_factor

    def _history_boost(self, site_id: str) -> float:
        total = sum(self._history.values())
        if total == 0:
            return 0.0
        return 0.1 * self._history.get(site_id, 0) / total

    def _confidence(self, track: Track, swarm_map: Dict[str, Swarm]) -> float:
        sigma = 0.5 * (track.covariance[0] + track.covariance[1])
        base = max(0.3, 1.0 - sigma / 200.0)
        if track.id in swarm_map:
            base = min(1.0, base + 0.1)
        return round(base, 3)


def _build_swarm_map(swarms: Optional[List[Swarm]]) -> Dict[str, Swarm]:
    if swarms is None:
        return {}
    result: Dict[str, Swarm] = {}
    for swarm in swarms:
        for tid in swarm.member_track_ids:
            result[tid] = swarm
    return result
